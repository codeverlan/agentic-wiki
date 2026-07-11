from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import Callable, Dict, Mapping, Optional, Sequence, Tuple

from memwiki.coordinator_lease import (
    CoordinatorLease,
    CoordinatorLeaseAuthority,
    CoordinatorLeaseError,
    LeaseIdentity,
)


class RecoveryError(RuntimeError):
    """Startup state cannot be reconciled without violating coordinator safety."""


class RecoveryDispositionKind(str, Enum):
    RESUME = "resume"
    REQUEUE = "requeue"
    ACCEPTED = "accepted"
    OUTCOME_UNKNOWN = "outcome_unknown"
    MANUAL_SUPERVISION = "manual_supervision"


_ASSIGNMENT_STATES = {"active", "accepted", "cancelled", "missing"}
_LEASE_STATES = {"active", "expired", "missing", "offered", "released", "superseded"}
_WORKER_STATES = {"cancelled", "failed", "missing", "not_started", "reported", "running"}
_WORKTREE_STATES = {"clean", "committed", "conflicted", "dirty", "missing"}
_RECEIPT_STATES = {"cancelled", "failed", "none", "outcome_unknown", "planned", "started", "succeeded"}
_INTEGRATION_STATES = {
    "commit_applied",
    "conflict",
    "failed",
    "integrated",
    "integration_started",
    "none",
    "outcome_unknown",
    "post_validation_started",
    "push_observed_without_receipt",
    "rolled_back",
}


def _required_string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    return value


def _state(value: object, label: str, allowed: set[str]) -> str:
    result = _required_string(value, label)
    if result not in allowed:
        raise ValueError(f"{label} is invalid")
    return result


@dataclass(frozen=True)
class RecoveryArtifact:
    """Provider-neutral inspection of one in-flight slice at startup."""

    slice_id: str
    assignment_id: str
    attempt: int
    assignment_status: str
    lease_status: str
    worker_status: str
    worktree_status: str
    receipt_status: str
    integration_status: str

    def __post_init__(self) -> None:
        _required_string(self.slice_id, "slice_id")
        _required_string(self.assignment_id, "assignment_id")
        if isinstance(self.attempt, bool) or not isinstance(self.attempt, int) or self.attempt < 1:
            raise ValueError("attempt must be a positive integer")
        _state(self.assignment_status, "assignment_status", _ASSIGNMENT_STATES)
        _state(self.lease_status, "lease_status", _LEASE_STATES)
        _state(self.worker_status, "worker_status", _WORKER_STATES)
        _state(self.worktree_status, "worktree_status", _WORKTREE_STATES)
        _state(self.receipt_status, "receipt_status", _RECEIPT_STATES)
        _state(self.integration_status, "integration_status", _INTEGRATION_STATES)

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "RecoveryArtifact":
        expected = set(cls.__dataclass_fields__)
        if set(value) != expected:
            raise ValueError("recovery artifact fields do not match schema")
        return cls(
            slice_id=_required_string(value["slice_id"], "slice_id"),
            assignment_id=_required_string(value["assignment_id"], "assignment_id"),
            attempt=value["attempt"],  # type: ignore[arg-type]
            assignment_status=_state(value["assignment_status"], "assignment_status", _ASSIGNMENT_STATES),
            lease_status=_state(value["lease_status"], "lease_status", _LEASE_STATES),
            worker_status=_state(value["worker_status"], "worker_status", _WORKER_STATES),
            worktree_status=_state(value["worktree_status"], "worktree_status", _WORKTREE_STATES),
            receipt_status=_state(value["receipt_status"], "receipt_status", _RECEIPT_STATES),
            integration_status=_state(value["integration_status"], "integration_status", _INTEGRATION_STATES),
        )


@dataclass(frozen=True)
class RecoveryDisposition:
    slice_id: str
    assignment_id: str
    attempt: int
    kind: RecoveryDispositionKind
    reason: str
    dispatch_allowed: bool
    preserve_evidence: bool

    def __post_init__(self) -> None:
        _required_string(self.slice_id, "slice_id")
        _required_string(self.assignment_id, "assignment_id")
        _required_string(self.reason, "reason")
        if isinstance(self.attempt, bool) or not isinstance(self.attempt, int) or self.attempt < 1:
            raise ValueError("attempt must be a positive integer")
        if not isinstance(self.dispatch_allowed, bool):
            raise ValueError("dispatch_allowed must be boolean")
        if not isinstance(self.preserve_evidence, bool):
            raise ValueError("preserve_evidence must be boolean")

    def to_dict(self) -> Dict[str, object]:
        value = asdict(self)
        value["kind"] = self.kind.value
        return value

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "RecoveryDisposition":
        expected = set(cls.__dataclass_fields__)
        if set(value) != expected:
            raise ValueError("recovery disposition fields do not match schema")
        try:
            kind = RecoveryDispositionKind(value["kind"])
        except (TypeError, ValueError) as exc:
            raise ValueError("recovery disposition kind is invalid") from exc
        dispatch_allowed = value["dispatch_allowed"]
        preserve_evidence = value["preserve_evidence"]
        if not isinstance(dispatch_allowed, bool) or not isinstance(preserve_evidence, bool):
            raise ValueError("recovery disposition flags must be boolean")
        return cls(
            slice_id=_required_string(value["slice_id"], "slice_id"),
            assignment_id=_required_string(value["assignment_id"], "assignment_id"),
            attempt=value["attempt"],  # type: ignore[arg-type]
            kind=kind,
            reason=_required_string(value["reason"], "reason"),
            dispatch_allowed=dispatch_allowed,
            preserve_evidence=preserve_evidence,
        )


@dataclass(frozen=True)
class RecoveryResult:
    dispositions: Tuple[RecoveryDisposition, ...]
    coordinator_lease: Optional[CoordinatorLease] = None

    def by_slice(self, slice_id: str) -> RecoveryDisposition:
        for disposition in self.dispositions:
            if disposition.slice_id == slice_id:
                return disposition
        raise KeyError(slice_id)

    @property
    def dispatchable_slice_ids(self) -> Tuple[str, ...]:
        return tuple(item.slice_id for item in self.dispositions if item.dispatch_allowed)


class StartupRecovery:
    """Verify durable state, fence the old owner, and reconcile in-flight work."""

    def __init__(
        self,
        *,
        verify_state: Callable[[], None],
        rebuild_projection: Callable[[], None],
        lease_authority: CoordinatorLeaseAuthority,
        owner: LeaseIdentity,
        now: Callable[[], datetime],
        lease_ttl: timedelta,
        inspect: Callable[[], Sequence[RecoveryArtifact]],
    ) -> None:
        self._verify_state = verify_state
        self._rebuild_projection = rebuild_projection
        self._lease_authority = lease_authority
        self._owner = owner
        self._now = now
        self._lease_ttl = lease_ttl
        self._inspect = inspect

    def run(self) -> RecoveryResult:
        try:
            self._verify_state()
            self._rebuild_projection()
        except Exception as exc:
            raise RecoveryError(f"startup verification failed: {exc}") from exc
        try:
            lease = self._lease_authority.takeover(self._owner, now=self._now(), ttl=self._lease_ttl)
        except (CoordinatorLeaseError, ValueError) as exc:
            raise RecoveryError(f"coordinator takeover failed: {exc}") from exc
        result = self.reconcile(self._inspect())
        return RecoveryResult(result.dispositions, lease)

    @staticmethod
    def reconcile(artifacts: Sequence[RecoveryArtifact]) -> RecoveryResult:
        ordered = sorted(artifacts, key=lambda item: (item.slice_id, item.attempt))
        slice_ids = [item.slice_id for item in ordered]
        assignment_ids = [item.assignment_id for item in ordered]
        if len(slice_ids) != len(set(slice_ids)):
            raise RecoveryError("duplicate slice in startup inspection")
        if len(assignment_ids) != len(set(assignment_ids)):
            raise RecoveryError("duplicate assignment in startup inspection")
        return RecoveryResult(tuple(StartupRecovery.classify(item) for item in ordered))

    @staticmethod
    def classify(artifact: RecoveryArtifact) -> RecoveryDisposition:
        def result(
            kind: RecoveryDispositionKind,
            reason: str,
            *,
            dispatch: bool = False,
        ) -> RecoveryDisposition:
            return RecoveryDisposition(
                slice_id=artifact.slice_id,
                assignment_id=artifact.assignment_id,
                attempt=artifact.attempt,
                kind=kind,
                reason=reason,
                dispatch_allowed=dispatch,
                preserve_evidence=True,
            )

        if artifact.integration_status == "integrated":
            return result(RecoveryDispositionKind.ACCEPTED, "integration has terminal commit proof")
        if artifact.integration_status in {
            "integration_started",
            "commit_applied",
            "post_validation_started",
            "outcome_unknown",
            "push_observed_without_receipt",
        }:
            label = artifact.integration_status.replace("_", " ")
            return result(
                RecoveryDispositionKind.OUTCOME_UNKNOWN,
                f"{label}; reconcile Git and integration evidence before any repeat",
            )
        if artifact.receipt_status in {"started", "outcome_unknown"}:
            return result(
                RecoveryDispositionKind.OUTCOME_UNKNOWN,
                "external effect may have occurred; do not retry without terminal evidence",
            )
        if artifact.worktree_status in {"dirty", "conflicted"}:
            return result(
                RecoveryDispositionKind.MANUAL_SUPERVISION,
                "worktree contains unaccepted evidence that must be preserved and reviewed",
            )
        if artifact.assignment_status == "accepted" or artifact.receipt_status == "succeeded":
            return result(RecoveryDispositionKind.ACCEPTED, "accepted work has terminal evidence")
        if artifact.assignment_status in {"cancelled", "missing"}:
            return result(
                RecoveryDispositionKind.REQUEUE,
                "assignment is no longer active and may be issued as a new attempt",
                dispatch=True,
            )
        if artifact.lease_status in {"expired", "missing", "released", "superseded"}:
            return result(
                RecoveryDispositionKind.REQUEUE,
                "slice lease is not live; preserve the attempt and issue a fenced successor",
                dispatch=True,
            )
        if artifact.worker_status in {"cancelled", "failed", "missing"}:
            return result(
                RecoveryDispositionKind.REQUEUE,
                "worker is terminal or unavailable; issue a new fenced attempt",
                dispatch=True,
            )
        return result(
            RecoveryDispositionKind.RESUME,
            "durable assignment remains safe to reconcile without duplicate dispatch",
        )
