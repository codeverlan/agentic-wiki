from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

from memwiki.coordinator_agent_policy import (
    AgentRequirement,
    LatencyClass,
    QualificationReceipt,
    QualificationStatus,
)
from memwiki.coordinator_agent_registry import RegistryEntry, TrustStatus
from memwiki.coordinator_agents import AgentVisibility
from memwiki.coordinator_capabilities import CostClass


class SelectionStatus(str, Enum):
    SELECTED = "selected"
    BLOCKED = "blocked"


_COST_RANK = {CostClass.FREE.value: 0, CostClass.METERED.value: 1, CostClass.HIGH.value: 2}
_LATENCY_RANK = {
    LatencyClass.INTERACTIVE.value: 0,
    LatencyClass.BATCH.value: 1,
    LatencyClass.LONG_RUNNING.value: 2,
    LatencyClass.UNKNOWN.value: 3,
}
_PRIVATE = {
    AgentVisibility.PERSONAL_PRIVATE,
    AgentVisibility.ORGANIZATION_PRIVATE,
    AgentVisibility.PROJECT_LOCAL,
}


@dataclass(frozen=True)
class AgentCandidate:
    entry: RegistryEntry
    qualification: QualificationReceipt

    def __post_init__(self) -> None:
        receipt = self.qualification
        agent = self.entry.agent
        if receipt.agent_id != agent.agent_id:
            raise ValueError("qualification agent does not match registry entry")
        if receipt.stable_identity != self.entry.stable_identity:
            raise ValueError("qualification identity does not match registry entry")
        if receipt.agent_version != agent.version:
            raise ValueError("qualification version does not match registry entry")


@dataclass(frozen=True)
class ProjectAgentPreferences:
    """Project-memory preferences; these influence selection but never agent state."""

    preferred_agent_ids: Mapping[str, Tuple[str, ...]] = field(default_factory=dict)
    prefer_private_agents: bool = True
    allow_public_fallback: bool = True
    maximum_fallbacks: int = 3

    def __post_init__(self) -> None:
        if not 0 <= self.maximum_fallbacks <= 16:
            raise ValueError("maximum fallbacks must be between zero and sixteen")
        for operation, identifiers in self.preferred_agent_ids.items():
            if not operation or len(set(identifiers)) != len(identifiers):
                raise ValueError("project agent preferences must be non-empty and unique")


@dataclass(frozen=True)
class SelectionOption:
    agent_id: str
    stable_identity: str
    receipt_id: str
    score: Tuple[int, int, int, int, str]
    explanation: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "stable_identity": self.stable_identity,
            "receipt_id": self.receipt_id,
            "score": list(self.score),
            "explanation": self.explanation,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "SelectionOption":
        score = value["score"]
        if not isinstance(score, list) or len(score) != 5:
            raise ValueError("selection score must have five components")
        return cls(
            agent_id=str(value["agent_id"]),
            stable_identity=str(value["stable_identity"]),
            receipt_id=str(value["receipt_id"]),
            score=(int(score[0]), int(score[1]), int(score[2]), int(score[3]), str(score[4])),
            explanation=str(value["explanation"]),
        )


@dataclass(frozen=True)
class AgentFailure:
    agent_id: str
    failure_kind: str
    evidence_id: str

    def __post_init__(self) -> None:
        if not self.agent_id or not self.failure_kind:
            raise ValueError("agent failure identity and kind are required")
        if not self.evidence_id.startswith("sha256:") or len(self.evidence_id) != 71:
            raise ValueError("failure evidence must be a sha256 identifier")


@dataclass(frozen=True)
class AgentSelectionPlan:
    plan_id: str
    slice_id: str
    operation: str
    status: SelectionStatus
    selected_agent_id: Optional[str]
    fallback_chain: Tuple[SelectionOption, ...]
    failed_agent_ids: Tuple[str, ...]
    excluded_agents: Mapping[str, Tuple[str, ...]]
    explanations: Tuple[str, ...]
    blocked_slice_id: Optional[str]
    blocker_scope: Optional[str]

    def _payload(self) -> Dict[str, Any]:
        return {
            "slice_id": self.slice_id,
            "operation": self.operation,
            "status": self.status.value,
            "selected_agent_id": self.selected_agent_id,
            "fallback_chain": [item.to_dict() for item in self.fallback_chain],
            "failed_agent_ids": list(self.failed_agent_ids),
            "excluded_agents": {key: list(value) for key, value in sorted(self.excluded_agents.items())},
            "explanations": list(self.explanations),
            "blocked_slice_id": self.blocked_slice_id,
            "blocker_scope": self.blocker_scope,
        }

    def to_dict(self) -> Dict[str, Any]:
        return {"plan_id": self.plan_id, **self._payload()}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "AgentSelectionPlan":
        fallbacks = value["fallback_chain"]
        excluded = value["excluded_agents"]
        if not isinstance(fallbacks, list) or not isinstance(excluded, dict):
            raise ValueError("invalid selection plan collections")
        plan = cls(
            plan_id=str(value["plan_id"]),
            slice_id=str(value["slice_id"]),
            operation=str(value["operation"]),
            status=SelectionStatus(str(value["status"])),
            selected_agent_id=_optional_string(value["selected_agent_id"]),
            fallback_chain=tuple(SelectionOption.from_dict(item) for item in fallbacks),
            failed_agent_ids=tuple(str(item) for item in value["failed_agent_ids"]),
            excluded_agents={str(key): tuple(str(item) for item in reasons) for key, reasons in excluded.items()},
            explanations=tuple(str(item) for item in value["explanations"]),
            blocked_slice_id=_optional_string(value["blocked_slice_id"]),
            blocker_scope=_optional_string(value["blocker_scope"]),
        )
        if plan.plan_id != _plan_id(plan._payload()):
            raise ValueError("selection plan id does not match content")
        return plan


def _optional_string(value: object) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("optional selection value must be a string or null")
    return value


def _plan_id(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _preference_rank(candidate: AgentCandidate, preferences: ProjectAgentPreferences) -> int:
    preferred = preferences.preferred_agent_ids.get(candidate.qualification.operation, ())
    try:
        return preferred.index(candidate.entry.agent.agent_id)
    except ValueError:
        return len(preferred) + 1


def _option(candidate: AgentCandidate, preferences: ProjectAgentPreferences) -> SelectionOption:
    entry = candidate.entry
    receipt = candidate.qualification
    private_rank = 0 if entry.agent.visibility in _PRIVATE and preferences.prefer_private_agents else 1
    cost = _COST_RANK[str(receipt.policy_snapshot["cost"])]
    latency = _LATENCY_RANK[str(receipt.policy_snapshot["latency"])]
    preference = _preference_rank(candidate, preferences)
    score = (preference, private_rank, cost, latency, entry.stable_identity)
    explanation = (
        f"{entry.agent.agent_id}: project preference rank {preference}; "
        f"visibility {entry.agent.visibility.value}; trust {entry.trust.value}; "
        f"operation {receipt.operation}; cost {receipt.policy_snapshot['cost']}; "
        f"latency {receipt.policy_snapshot['latency']}"
    )
    return SelectionOption(
        agent_id=entry.agent.agent_id,
        stable_identity=entry.stable_identity,
        receipt_id=receipt.receipt_id,
        score=score,
        explanation=explanation,
    )


def plan_agent_selection(
    slice_id: str,
    requirement: AgentRequirement,
    candidates: Sequence[AgentCandidate],
    preferences: ProjectAgentPreferences,
) -> AgentSelectionPlan:
    """Build a deterministic, policy-bounded selection and fallback chain."""

    if not slice_id:
        raise ValueError("slice id must be non-empty")
    excluded: Dict[str, Tuple[str, ...]] = {}
    options = []
    seen = set()
    for candidate in candidates:
        agent_id = candidate.entry.agent.agent_id
        if agent_id in seen:
            raise ValueError("duplicate agent candidate")
        seen.add(agent_id)
        receipt = candidate.qualification
        reasons = []
        if receipt.operation != requirement.operation:
            reasons.append("operation mismatch")
        if receipt.status is not QualificationStatus.QUALIFIED:
            reasons.append(f"qualification {receipt.status.value}")
        if candidate.entry.trust is not TrustStatus.TRUSTED:
            reasons.append("agent is not trusted")
        if (
            candidate.entry.agent.visibility is AgentVisibility.PUBLIC
            and not preferences.allow_public_fallback
        ):
            reasons.append("public fallback disabled")
        if reasons:
            excluded[agent_id] = tuple(sorted(reasons))
        else:
            options.append(_option(candidate, preferences))
    options.sort(key=lambda item: item.score)
    selected = options[0] if options else None
    fallbacks = tuple(options[1 : 1 + preferences.maximum_fallbacks])
    payload = {
        "slice_id": slice_id,
        "operation": requirement.operation,
        "status": (SelectionStatus.SELECTED if selected else SelectionStatus.BLOCKED).value,
        "selected_agent_id": None if selected is None else selected.agent_id,
        "fallback_chain": [item.to_dict() for item in fallbacks],
        "failed_agent_ids": [],
        "excluded_agents": {key: list(value) for key, value in sorted(excluded.items())},
        "explanations": [item.explanation for item in options],
        "blocked_slice_id": None if selected else slice_id,
        "blocker_scope": None if selected else "slice",
    }
    return AgentSelectionPlan(
        plan_id=_plan_id(payload),
        slice_id=slice_id,
        operation=requirement.operation,
        status=SelectionStatus.SELECTED if selected else SelectionStatus.BLOCKED,
        selected_agent_id=None if selected is None else selected.agent_id,
        fallback_chain=fallbacks,
        failed_agent_ids=(),
        excluded_agents=excluded,
        explanations=tuple(item.explanation for item in options),
        blocked_slice_id=None if selected else slice_id,
        blocker_scope=None if selected else "slice",
    )


def record_agent_failure(plan: AgentSelectionPlan, failure: AgentFailure) -> AgentSelectionPlan:
    """Advance one slice's chain without changing any other slice or widening policy."""

    if failure.agent_id != plan.selected_agent_id:
        raise ValueError("failure does not match the selected agent")
    next_option = plan.fallback_chain[0] if plan.fallback_chain else None
    updated = replace(
        plan,
        plan_id="",
        status=SelectionStatus.SELECTED if next_option else SelectionStatus.BLOCKED,
        selected_agent_id=None if next_option is None else next_option.agent_id,
        fallback_chain=plan.fallback_chain[1:],
        failed_agent_ids=(*plan.failed_agent_ids, failure.agent_id),
        explanations=(
            *plan.explanations,
            f"{failure.agent_id} failed ({failure.failure_kind}); evidence {failure.evidence_id}",
        ),
        blocked_slice_id=None if next_option else plan.slice_id,
        blocker_scope=None if next_option else "slice",
    )
    return replace(updated, plan_id=_plan_id(updated._payload()))
