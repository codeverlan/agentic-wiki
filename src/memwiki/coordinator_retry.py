from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Sequence, Tuple

CLASSIFICATIONS = {
    "infrastructure_retry",
    "validation_repair",
    "blocker",
    "cancellation",
    "terminal_failure",
}
ACTIONS = {
    "retry_after",
    "repair",
    "blocked",
    "cancelled",
    "terminal_escalation",
}


def _positive_integer(value: object, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ValueError(f"{label} must be a positive integer")
    return value


def _timestamp(value: object, label: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be an ISO-8601 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError(f"{label} must be an ISO-8601 timestamp") from None
    if parsed.tzinfo is None:
        raise ValueError(f"{label} must include an offset")
    return parsed


def _aware(value: datetime, label: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError(f"{label} must be an offset-aware datetime")
    return value


@dataclass(frozen=True)
class RetryPolicy:
    max_infrastructure_retries: int = 3
    max_validation_repairs: int = 2
    initial_backoff_seconds: float = 5.0
    backoff_multiplier: float = 2.0
    max_backoff_seconds: float = 300.0
    jitter_fraction: float = 0.1

    def __post_init__(self) -> None:
        _positive_integer(self.max_infrastructure_retries, "infrastructure retry limit")
        _positive_integer(self.max_validation_repairs, "validation repair limit")
        for value, label in (
            (self.initial_backoff_seconds, "initial backoff"),
            (self.backoff_multiplier, "backoff multiplier"),
            (self.max_backoff_seconds, "maximum backoff"),
        ):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
                raise ValueError(f"{label} must be positive")
        if (
            isinstance(self.jitter_fraction, bool)
            or not isinstance(self.jitter_fraction, (int, float))
            or not 0 <= self.jitter_fraction <= 1
        ):
            raise ValueError("jitter fraction must be between zero and one")


@dataclass
class SliceRetryState:
    attempts: int = 0
    infrastructure_retries: int = 0
    validation_repairs: int = 0
    terminal_escalations: int = 0


@dataclass(frozen=True)
class RetryDecision:
    decision_id: str
    slice_id: str
    classification: str
    action: str
    ordinal: int
    recorded_at: datetime
    eligible_at: Optional[datetime]
    reason: str
    affects_slice_only: bool = True


@dataclass(frozen=True)
class RepairAction:
    repair_id: str
    slice_id: str
    decision_id: str
    action: str
    evidence_ids: Tuple[str, ...]
    recorded_at: datetime


class RetryLedger:
    """Persistent retry decisions and repairs with restart-safe counters."""

    def __init__(
        self,
        slices: Optional[Dict[str, SliceRetryState]] = None,
        decisions: Optional[List[RetryDecision]] = None,
        repair_actions: Optional[List[RepairAction]] = None,
    ) -> None:
        self._slices = slices or {}
        self._decisions = decisions or []
        self._repair_actions = repair_actions or []

    @classmethod
    def empty(cls) -> "RetryLedger":
        return cls()

    def slice_state(self, slice_id: str) -> SliceRetryState:
        self._require_text(slice_id, "slice id")
        return self._slices.setdefault(slice_id, SliceRetryState())

    def record_outcome(
        self,
        slice_id: str,
        classification: str,
        recorded_at: datetime,
        policy: RetryPolicy,
    ) -> RetryDecision:
        self._require_text(slice_id, "slice id")
        if classification not in CLASSIFICATIONS:
            raise ValueError(f"unknown classification: {classification}")
        now = _aware(recorded_at, "recorded_at")
        state = self.slice_state(slice_id)
        state.attempts += 1

        if classification == "infrastructure_retry":
            ordinal = state.infrastructure_retries + 1
            if ordinal <= policy.max_infrastructure_retries:
                state.infrastructure_retries = ordinal
                action = "retry_after"
                eligible_at = now + timedelta(seconds=self._delay(slice_id, ordinal, policy))
                reason = "infrastructure_retry_scheduled"
            else:
                action, eligible_at, reason = (
                    "terminal_escalation",
                    None,
                    "infrastructure_retry_exhausted",
                )
                state.terminal_escalations += 1
        elif classification == "validation_repair":
            ordinal = state.validation_repairs + 1
            if ordinal <= policy.max_validation_repairs:
                state.validation_repairs = ordinal
                action, eligible_at, reason = "repair", None, "validation_repair_scheduled"
            else:
                action, eligible_at, reason = (
                    "terminal_escalation",
                    None,
                    "validation_repair_exhausted",
                )
                state.terminal_escalations += 1
        else:
            ordinal = self._classification_count(slice_id, classification) + 1
            action = {
                "blocker": "blocked",
                "cancellation": "cancelled",
                "terminal_failure": "terminal_escalation",
            }[classification]
            eligible_at = None
            reason = classification
            if action == "terminal_escalation":
                state.terminal_escalations += 1

        decision = RetryDecision(
            decision_id=self._identifier("decision", slice_id, state.attempts, classification),
            slice_id=slice_id,
            classification=classification,
            action=action,
            ordinal=ordinal,
            recorded_at=now,
            eligible_at=eligible_at,
            reason=reason,
        )
        self._decisions.append(decision)
        return decision

    def record_repair_action(
        self,
        *,
        slice_id: str,
        decision_id: str,
        action: str,
        evidence_ids: Sequence[str],
        recorded_at: datetime,
    ) -> RepairAction:
        self._require_text(action, "repair action")
        if not all(isinstance(item, str) and item for item in evidence_ids):
            raise ValueError("evidence ids must be non-empty strings")
        matching = [item for item in self._decisions if item.decision_id == decision_id]
        if not matching or matching[0].slice_id != slice_id or matching[0].action != "repair":
            raise ValueError("repair action must reference a repair decision for the slice")
        ordinal = len(self._repair_actions) + 1
        record = RepairAction(
            repair_id=self._identifier("repair", slice_id, ordinal, decision_id),
            slice_id=slice_id,
            decision_id=decision_id,
            action=action,
            evidence_ids=tuple(evidence_ids),
            recorded_at=_aware(recorded_at, "recorded_at"),
        )
        self._repair_actions.append(record)
        return record

    def to_dict(self) -> Dict[str, object]:
        return {
            "schema_version": 1,
            "slices": {
                key: asdict(value) for key, value in sorted(self._slices.items())
            },
            "decisions": [self._decision_dict(item) for item in self._decisions],
            "repair_actions": [self._repair_dict(item) for item in self._repair_actions],
        }

    @classmethod
    def from_dict(cls, payload: object) -> "RetryLedger":
        if not isinstance(payload, dict) or set(payload) != {
            "schema_version",
            "slices",
            "decisions",
            "repair_actions",
        }:
            raise ValueError("retry ledger has invalid fields")
        if payload["schema_version"] != 1:
            raise ValueError("unsupported retry ledger schema version")
        raw_slices = payload["slices"]
        if not isinstance(raw_slices, dict):
            raise ValueError("slices must be an object")
        slices: Dict[str, SliceRetryState] = {}
        for slice_id, raw in raw_slices.items():
            cls._require_text(slice_id, "slice id")
            if not isinstance(raw, dict) or set(raw) != {
                "attempts",
                "infrastructure_retries",
                "validation_repairs",
                "terminal_escalations",
            }:
                raise ValueError("slice retry state has invalid fields")
            values = list(raw.values())
            if any(not isinstance(item, int) or isinstance(item, bool) or item < 0 for item in values):
                raise ValueError("slice retry counters must be non-negative integers")
            slices[slice_id] = SliceRetryState(**raw)
        decisions = cls._parse_decisions(payload["decisions"])
        repairs = cls._parse_repairs(payload["repair_actions"])
        ledger = cls(slices, decisions, repairs)
        ledger._validate_references()
        return ledger

    @classmethod
    def _parse_decisions(cls, raw: object) -> List[RetryDecision]:
        if not isinstance(raw, list):
            raise ValueError("decisions must be a list")
        result = []
        fields = {
            "decision_id", "slice_id", "classification", "action", "ordinal",
            "recorded_at", "eligible_at", "reason", "affects_slice_only",
        }
        for item in raw:
            if not isinstance(item, dict) or set(item) != fields:
                raise ValueError("retry decision has invalid fields")
            if item["classification"] not in CLASSIFICATIONS:
                raise ValueError(f"unknown classification: {item['classification']}")
            if item["action"] not in ACTIONS:
                raise ValueError(f"unknown retry action: {item['action']}")
            if not isinstance(item["affects_slice_only"], bool):
                raise ValueError("affects_slice_only must be a boolean")
            eligible = item["eligible_at"]
            result.append(RetryDecision(
                decision_id=cls._require_text(item["decision_id"], "decision id"),
                slice_id=cls._require_text(item["slice_id"], "slice id"),
                classification=item["classification"],
                action=item["action"],
                ordinal=_positive_integer(item["ordinal"], "decision ordinal"),
                recorded_at=_timestamp(item["recorded_at"], "decision recorded_at"),
                eligible_at=None if eligible is None else _timestamp(eligible, "decision eligible_at"),
                reason=cls._require_text(item["reason"], "decision reason"),
                affects_slice_only=item["affects_slice_only"],
            ))
        return result

    @classmethod
    def _parse_repairs(cls, raw: object) -> List[RepairAction]:
        if not isinstance(raw, list):
            raise ValueError("repair actions must be a list")
        result = []
        fields = {"repair_id", "slice_id", "decision_id", "action", "evidence_ids", "recorded_at"}
        for item in raw:
            if not isinstance(item, dict) or set(item) != fields:
                raise ValueError("repair action has invalid fields")
            evidence = item["evidence_ids"]
            if not isinstance(evidence, list) or not all(isinstance(value, str) and value for value in evidence):
                raise ValueError("evidence ids must be non-empty strings")
            result.append(RepairAction(
                repair_id=cls._require_text(item["repair_id"], "repair id"),
                slice_id=cls._require_text(item["slice_id"], "slice id"),
                decision_id=cls._require_text(item["decision_id"], "decision id"),
                action=cls._require_text(item["action"], "repair action"),
                evidence_ids=tuple(evidence),
                recorded_at=_timestamp(item["recorded_at"], "repair recorded_at"),
            ))
        return result

    def _validate_references(self) -> None:
        ids = {item.decision_id for item in self._decisions}
        if len(ids) != len(self._decisions):
            raise ValueError("duplicate retry decision id")
        for item in self._decisions:
            if item.slice_id not in self._slices:
                raise ValueError("retry decision references unknown slice state")
        for repair in self._repair_actions:
            matching = [item for item in self._decisions if item.decision_id == repair.decision_id]
            if not matching or matching[0].slice_id != repair.slice_id or matching[0].action != "repair":
                raise ValueError("repair action references invalid repair decision")
        for slice_id, state in self._slices.items():
            decisions = [item for item in self._decisions if item.slice_id == slice_id]
            expected = SliceRetryState(
                attempts=len(decisions),
                infrastructure_retries=sum(
                    item.classification == "infrastructure_retry"
                    and item.action == "retry_after"
                    for item in decisions
                ),
                validation_repairs=sum(
                    item.classification == "validation_repair" and item.action == "repair"
                    for item in decisions
                ),
                terminal_escalations=sum(
                    item.action == "terminal_escalation" for item in decisions
                ),
            )
            if state != expected:
                raise ValueError(f"slice {slice_id} counters do not match decision history")

    def _classification_count(self, slice_id: str, classification: str) -> int:
        return sum(
            item.slice_id == slice_id and item.classification == classification
            for item in self._decisions
        )

    @staticmethod
    def _delay(slice_id: str, ordinal: int, policy: RetryPolicy) -> float:
        base = min(
            policy.max_backoff_seconds,
            policy.initial_backoff_seconds * policy.backoff_multiplier ** (ordinal - 1),
        )
        digest = hashlib.sha256(f"{slice_id}:{ordinal}".encode()).digest()
        unit = int.from_bytes(digest[:8], "big") / float(2**64 - 1)
        jitter = (unit * 2 - 1) * policy.jitter_fraction
        return max(0.0, base * (1 + jitter))

    @staticmethod
    def _identifier(kind: str, slice_id: str, ordinal: int, discriminator: str) -> str:
        value = f"{kind}:{slice_id}:{ordinal}:{discriminator}"
        return hashlib.sha256(value.encode()).hexdigest()

    @staticmethod
    def _require_text(value: object, label: str) -> str:
        if not isinstance(value, str) or not value:
            raise ValueError(f"{label} must be a non-empty string")
        return value

    @staticmethod
    def _decision_dict(item: RetryDecision) -> Dict[str, object]:
        data = asdict(item)
        data["recorded_at"] = item.recorded_at.isoformat()
        data["eligible_at"] = None if item.eligible_at is None else item.eligible_at.isoformat()
        return data

    @staticmethod
    def _repair_dict(item: RepairAction) -> Dict[str, object]:
        data = asdict(item)
        data["evidence_ids"] = list(item.evidence_ids)
        data["recorded_at"] = item.recorded_at.isoformat()
        return data
