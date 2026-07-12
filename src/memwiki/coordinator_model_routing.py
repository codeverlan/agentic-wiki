"""Provider-neutral, host-aware model and reasoning selection."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, Mapping, Optional, Sequence, Tuple


class CapabilityTier(str, Enum):
    NARROW = "narrow"
    IMPLEMENTATION = "implementation"
    FRONTIER = "frontier"


class ReasoningEffort(str, Enum):
    MINIMAL = "minimal"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    XHIGH = "xhigh"
    MAX = "max"
    ULTRA = "ultra"


_TIER_ORDER = (CapabilityTier.NARROW, CapabilityTier.IMPLEMENTATION, CapabilityTier.FRONTIER)
_REASONING_ORDER = (
    ReasoningEffort.MINIMAL,
    ReasoningEffort.LOW,
    ReasoningEffort.MEDIUM,
    ReasoningEffort.HIGH,
    ReasoningEffort.XHIGH,
    ReasoningEffort.MAX,
    ReasoningEffort.ULTRA,
)
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/+-]{0,127}$")
_SECRET_KEY = re.compile(r"(?:api[_-]?key|authorization|credential|password|secret|token)", re.IGNORECASE)


def _canonical(value: object) -> bytes:
    try:
        return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False).encode(
            "utf-8"
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("routing data must be JSON serializable") from exc


def _digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value)).hexdigest()


def _identifier(value: object, label: str) -> str:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise ValueError(f"{label} must be a bounded identifier")
    return value


def _metadata(value: Mapping[str, object], label: str) -> Dict[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} metadata must be an object")

    def inspect(item: object) -> None:
        if isinstance(item, Mapping):
            for key, nested in item.items():
                if not isinstance(key, str):
                    raise ValueError(f"{label} metadata keys must be strings")
                if _SECRET_KEY.search(key):
                    raise ValueError(f"{label} metadata must not contain credentials or secrets")
                inspect(nested)
        elif isinstance(item, (list, tuple)):
            for nested in item:
                inspect(nested)

    inspect(value)
    normalized = json.loads(_canonical(dict(value)))
    if not isinstance(normalized, dict):  # Kept for mypy and future JSON decoder changes.
        raise ValueError(f"{label} metadata must be an object")
    return normalized


def _capabilities(values: Sequence[str], label: str) -> Tuple[str, ...]:
    if len(values) != len(set(values)):
        raise ValueError(f"{label} must not contain duplicates")
    return tuple(sorted(_identifier(value, label) for value in values))


def _reasoning(values: Sequence[ReasoningEffort], label: str) -> Tuple[ReasoningEffort, ...]:
    if not values:
        raise ValueError(f"{label} must not be empty")
    result = tuple(values)
    if len(result) != len(set(result)):
        raise ValueError(f"{label} must not contain duplicates")
    if any(not isinstance(value, ReasoningEffort) for value in result):
        raise ValueError(f"{label} must contain reasoning efforts")
    return tuple(sorted(result, key=_REASONING_ORDER.index))


def _tier_at_least(value: CapabilityTier, minimum: CapabilityTier) -> bool:
    return _TIER_ORDER.index(value) >= _TIER_ORDER.index(minimum)


def _reasoning_at_least(value: ReasoningEffort, minimum: ReasoningEffort) -> bool:
    return _REASONING_ORDER.index(value) >= _REASONING_ORDER.index(minimum)


@dataclass(frozen=True)
class ModelCapability:
    model_id: str
    tier: CapabilityTier
    reasoning_efforts: Tuple[ReasoningEffort, ...]
    capabilities: Tuple[str, ...] = ()
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "model_id", _identifier(self.model_id, "model id"))
        if not isinstance(self.tier, CapabilityTier):
            raise ValueError("model tier must be a capability tier")
        object.__setattr__(self, "reasoning_efforts", _reasoning(self.reasoning_efforts, "model reasoning efforts"))
        object.__setattr__(self, "capabilities", _capabilities(self.capabilities, "model capabilities"))
        object.__setattr__(self, "metadata", _metadata(self.metadata, "model"))

    def to_dict(self) -> Dict[str, object]:
        return {
            "model_id": self.model_id,
            "tier": self.tier.value,
            "reasoning_efforts": [item.value for item in self.reasoning_efforts],
            "capabilities": list(self.capabilities),
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "ModelCapability":
        expected = {"model_id", "tier", "reasoning_efforts", "capabilities", "metadata"}
        if set(value) != expected:
            raise ValueError("model capability fields do not match the schema")
        return cls(
            model_id=_identifier(value["model_id"], "model id"),
            tier=CapabilityTier(str(value["tier"])),
            reasoning_efforts=tuple(ReasoningEffort(str(item)) for item in _sequence(value["reasoning_efforts"])),
            capabilities=tuple(str(item) for item in _sequence(value["capabilities"])),
            metadata=_mapping(value["metadata"], "model metadata"),
        )


@dataclass(frozen=True)
class HostCapabilityProfile:
    host_id: str
    models: Tuple[ModelCapability, ...]
    metadata: Mapping[str, object] = field(default_factory=dict)
    profile_id: str = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "host_id", _identifier(self.host_id, "host id"))
        if not self.models:
            raise ValueError("host capability profile must contain at least one model")
        if any(not isinstance(item, ModelCapability) for item in self.models):
            raise ValueError("host models must be model capabilities")
        if len({item.model_id for item in self.models}) != len(self.models):
            raise ValueError("host capability profile contains duplicate model ids")
        object.__setattr__(self, "models", tuple(sorted(self.models, key=lambda item: item.model_id)))
        object.__setattr__(self, "metadata", _metadata(self.metadata, "host capability profile"))
        object.__setattr__(self, "profile_id", _digest(self._payload()))

    def _payload(self) -> Dict[str, object]:
        return {
            "host_id": self.host_id,
            "models": [item.to_dict() for item in self.models],
            "metadata": dict(self.metadata),
        }

    def to_dict(self) -> Dict[str, object]:
        return {"profile_id": self.profile_id, **self._payload()}

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "HostCapabilityProfile":
        expected = {"profile_id", "host_id", "models", "metadata"}
        if set(value) != expected:
            raise ValueError("host capability profile fields do not match the schema")
        profile = cls(
            host_id=_identifier(value["host_id"], "host id"),
            models=tuple(
                ModelCapability.from_dict(_mapping(item, "model capability")) for item in _sequence(value["models"])
            ),
            metadata=_mapping(value["metadata"], "host capability profile metadata"),
        )
        if value["profile_id"] != profile.profile_id:
            raise ValueError("host capability profile id does not match content")
        return profile


@dataclass(frozen=True)
class ModelRoutingRequirement:
    required_tier: CapabilityTier
    required_reasoning: ReasoningEffort
    required_capabilities: Tuple[str, ...] = ()
    critical_risk: bool = False
    qualified_failures: int = 0
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.required_tier, CapabilityTier):
            raise ValueError("required tier must be a capability tier")
        if not isinstance(self.required_reasoning, ReasoningEffort):
            raise ValueError("required reasoning must be a reasoning effort")
        if not isinstance(self.critical_risk, bool):
            raise ValueError("critical risk must be a boolean")
        if (
            not isinstance(self.qualified_failures, int)
            or isinstance(self.qualified_failures, bool)
            or self.qualified_failures < 0
        ):
            raise ValueError("qualified failures must be a non-negative integer")
        object.__setattr__(
            self, "required_capabilities", _capabilities(self.required_capabilities, "required capabilities")
        )
        object.__setattr__(self, "metadata", _metadata(self.metadata, "requirement"))

    def to_dict(self) -> Dict[str, object]:
        return {
            "required_tier": self.required_tier.value,
            "required_reasoning": self.required_reasoning.value,
            "required_capabilities": list(self.required_capabilities),
            "critical_risk": self.critical_risk,
            "qualified_failures": self.qualified_failures,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "ModelRoutingRequirement":
        expected = {
            "required_tier",
            "required_reasoning",
            "required_capabilities",
            "critical_risk",
            "qualified_failures",
            "metadata",
        }
        if set(value) != expected:
            raise ValueError("model routing requirement fields do not match the schema")
        return cls(
            required_tier=CapabilityTier(str(value["required_tier"])),
            required_reasoning=ReasoningEffort(str(value["required_reasoning"])),
            required_capabilities=tuple(str(item) for item in _sequence(value["required_capabilities"])),
            critical_risk=value["critical_risk"]
            if isinstance(value["critical_risk"], bool)
            else _invalid_bool("critical risk"),
            qualified_failures=value["qualified_failures"]
            if isinstance(value["qualified_failures"], int)
            else _invalid_int("qualified failures"),
            metadata=_mapping(value["metadata"], "requirement metadata"),
        )


@dataclass(frozen=True)
class ModelRoutingPolicy:
    preferred_by_tier: Mapping[CapabilityTier, Tuple[str, ReasoningEffort]] = field(default_factory=dict)
    fallback_by_tier: Mapping[CapabilityTier, Tuple[Tuple[str, ReasoningEffort], ...]] = field(default_factory=dict)
    qualified_failure_escalation: int = 2
    metadata: Mapping[str, object] = field(default_factory=dict)
    policy_id: str = field(init=False)

    def __post_init__(self) -> None:
        if (
            not isinstance(self.qualified_failure_escalation, int)
            or isinstance(self.qualified_failure_escalation, bool)
            or self.qualified_failure_escalation < 1
        ):
            raise ValueError("qualified failure escalation must be a positive integer")
        preferred = _policy_choices(self.preferred_by_tier, "preferred")
        fallback = _policy_fallbacks(self.fallback_by_tier)
        object.__setattr__(self, "preferred_by_tier", preferred)
        object.__setattr__(self, "fallback_by_tier", fallback)
        object.__setattr__(self, "metadata", _metadata(self.metadata, "policy"))
        object.__setattr__(self, "policy_id", _digest(self._payload()))

    def _payload(self) -> Dict[str, object]:
        return {
            "preferred_by_tier": {
                tier.value: [model_id, reasoning.value]
                for tier, (model_id, reasoning) in self.preferred_by_tier.items()
            },
            "fallback_by_tier": {
                tier.value: [[model_id, reasoning.value] for model_id, reasoning in choices]
                for tier, choices in self.fallback_by_tier.items()
            },
            "qualified_failure_escalation": self.qualified_failure_escalation,
            "metadata": dict(self.metadata),
        }

    def to_dict(self) -> Dict[str, object]:
        return {"policy_id": self.policy_id, **self._payload()}

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "ModelRoutingPolicy":
        expected = {"policy_id", "preferred_by_tier", "fallback_by_tier", "qualified_failure_escalation", "metadata"}
        if set(value) != expected:
            raise ValueError("model routing policy fields do not match the schema")
        policy = cls(
            preferred_by_tier=_parse_policy_choices(_mapping(value["preferred_by_tier"], "preferred policy")),
            fallback_by_tier=_parse_policy_fallbacks(_mapping(value["fallback_by_tier"], "fallback policy")),
            qualified_failure_escalation=value["qualified_failure_escalation"]
            if isinstance(value["qualified_failure_escalation"], int)
            else _invalid_int("qualified failure escalation"),
            metadata=_mapping(value["metadata"], "policy metadata"),
        )
        if value["policy_id"] != policy.policy_id:
            raise ValueError("model routing policy id does not match content")
        return policy


@dataclass(frozen=True)
class ModelRoutingDecision:
    decision_id: str
    selected_model_id: str
    selected_tier: CapabilityTier
    selected_reasoning: ReasoningEffort
    used_fallback: bool
    escalated: bool
    requirement_snapshot: Mapping[str, object]
    host_profile_id: str
    policy_id: str
    compatible_model_ids: Tuple[str, ...]

    def _payload(self) -> Dict[str, object]:
        return {
            "selected_model_id": self.selected_model_id,
            "selected_tier": self.selected_tier.value,
            "selected_reasoning": self.selected_reasoning.value,
            "used_fallback": self.used_fallback,
            "escalated": self.escalated,
            "requirement_snapshot": dict(self.requirement_snapshot),
            "host_profile_id": self.host_profile_id,
            "policy_id": self.policy_id,
            "compatible_model_ids": list(self.compatible_model_ids),
        }

    def to_dict(self) -> Dict[str, object]:
        return {"decision_id": self.decision_id, **self._payload()}

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "ModelRoutingDecision":
        expected = {
            "decision_id",
            "selected_model_id",
            "selected_tier",
            "selected_reasoning",
            "used_fallback",
            "escalated",
            "requirement_snapshot",
            "host_profile_id",
            "policy_id",
            "compatible_model_ids",
        }
        if set(value) != expected:
            raise ValueError("model routing decision fields do not match the schema")
        decision = cls(
            decision_id=str(value["decision_id"]),
            selected_model_id=_identifier(value["selected_model_id"], "selected model id"),
            selected_tier=CapabilityTier(str(value["selected_tier"])),
            selected_reasoning=ReasoningEffort(str(value["selected_reasoning"])),
            used_fallback=value["used_fallback"]
            if isinstance(value["used_fallback"], bool)
            else _invalid_bool("used fallback"),
            escalated=value["escalated"] if isinstance(value["escalated"], bool) else _invalid_bool("escalated"),
            requirement_snapshot=_mapping(value["requirement_snapshot"], "requirement snapshot"),
            host_profile_id=_sha(value["host_profile_id"], "host profile id"),
            policy_id=_sha(value["policy_id"], "policy id"),
            compatible_model_ids=tuple(
                _identifier(item, "compatible model id") for item in _sequence(value["compatible_model_ids"])
            ),
        )
        if decision.decision_id != _digest(decision._payload()):
            raise ValueError("model routing decision id does not match content")
        return decision


def route_model(
    requirement: ModelRoutingRequirement,
    host_profile: HostCapabilityProfile,
    policy: ModelRoutingPolicy,
) -> ModelRoutingDecision:
    """Select the lowest compatible tier using only the supplied host profile."""

    effective_tier = _effective_tier(requirement, policy)
    candidates = tuple(
        model
        for model in host_profile.models
        if _tier_at_least(model.tier, effective_tier)
        and _reasoning_for(model, requirement.required_reasoning) is not None
        and set(requirement.required_capabilities).issubset(model.capabilities)
    )
    if not candidates:
        raise ValueError("no host model satisfies the routing requirement")

    selected = _select_candidate(candidates, requirement, effective_tier, policy)
    reasoning = _selected_reasoning(selected, requirement, policy)
    preferred = policy.preferred_by_tier.get(selected.tier)
    used_fallback = preferred != (selected.model_id, reasoning)
    payload = {
        "selected_model_id": selected.model_id,
        "selected_tier": selected.tier.value,
        "selected_reasoning": reasoning.value,
        "used_fallback": used_fallback,
        "escalated": effective_tier is not requirement.required_tier,
        "requirement_snapshot": requirement.to_dict(),
        "host_profile_id": host_profile.profile_id,
        "policy_id": policy.policy_id,
        "compatible_model_ids": [item.model_id for item in candidates],
    }
    return ModelRoutingDecision(
        decision_id=_digest(payload),
        selected_model_id=selected.model_id,
        selected_tier=selected.tier,
        selected_reasoning=reasoning,
        used_fallback=used_fallback,
        escalated=effective_tier is not requirement.required_tier,
        requirement_snapshot=requirement.to_dict(),
        host_profile_id=host_profile.profile_id,
        policy_id=policy.policy_id,
        compatible_model_ids=tuple(item.model_id for item in candidates),
    )


def _effective_tier(requirement: ModelRoutingRequirement, policy: ModelRoutingPolicy) -> CapabilityTier:
    if requirement.critical_risk:
        return CapabilityTier.FRONTIER
    escalation_steps = requirement.qualified_failures // policy.qualified_failure_escalation
    index = min(_TIER_ORDER.index(requirement.required_tier) + escalation_steps, len(_TIER_ORDER) - 1)
    return _TIER_ORDER[index]


def _select_candidate(
    candidates: Tuple[ModelCapability, ...],
    requirement: ModelRoutingRequirement,
    effective_tier: CapabilityTier,
    policy: ModelRoutingPolicy,
) -> ModelCapability:
    lowest_tier = next(tier for tier in _TIER_ORDER if any(item.tier is tier for item in candidates))
    lowest = tuple(item for item in candidates if item.tier is lowest_tier)
    preferred = _policy_options(policy, lowest_tier)
    for model_id, reasoning in preferred:
        match = next((item for item in lowest if item.model_id == model_id), None)
        if (
            match is not None
            and _reasoning_at_least(reasoning, requirement.required_reasoning)
            and reasoning in match.reasoning_efforts
        ):
            return match
    return min(lowest, key=lambda item: item.model_id)


def _selected_reasoning(
    model: ModelCapability, requirement: ModelRoutingRequirement, policy: ModelRoutingPolicy
) -> ReasoningEffort:
    choices = _policy_options(policy, model.tier)
    for model_id, reasoning in choices:
        if (
            model_id == model.model_id
            and reasoning in model.reasoning_efforts
            and _reasoning_at_least(reasoning, requirement.required_reasoning)
        ):
            return reasoning
    result = _reasoning_for(model, requirement.required_reasoning)
    if result is None:
        raise ValueError("selected model does not satisfy required reasoning")
    return result


def _reasoning_for(model: ModelCapability, minimum: ReasoningEffort) -> Optional[ReasoningEffort]:
    return next((item for item in model.reasoning_efforts if _reasoning_at_least(item, minimum)), None)


def _policy_options(policy: ModelRoutingPolicy, tier: CapabilityTier) -> Tuple[Tuple[str, ReasoningEffort], ...]:
    preferred = policy.preferred_by_tier.get(tier)
    fallbacks = policy.fallback_by_tier.get(tier, ())
    return fallbacks if preferred is None else (preferred, *fallbacks)


def _policy_choices(
    values: Mapping[CapabilityTier, Tuple[str, ReasoningEffort]], label: str
) -> Dict[CapabilityTier, Tuple[str, ReasoningEffort]]:
    result: Dict[CapabilityTier, Tuple[str, ReasoningEffort]] = {}
    for tier, choice in values.items():
        if not isinstance(tier, CapabilityTier) or not isinstance(choice, tuple) or len(choice) != 2:
            raise ValueError(f"{label} policy choices must map tiers to model and reasoning")
        model_id, reasoning = choice
        result[tier] = (_identifier(model_id, f"{label} model id"), _reasoning_value(reasoning, label))
    return dict(sorted(result.items(), key=lambda item: _TIER_ORDER.index(item[0])))


def _policy_fallbacks(
    values: Mapping[CapabilityTier, Tuple[Tuple[str, ReasoningEffort], ...]],
) -> Dict[CapabilityTier, Tuple[Tuple[str, ReasoningEffort], ...]]:
    result: Dict[CapabilityTier, Tuple[Tuple[str, ReasoningEffort], ...]] = {}
    for tier, choices in values.items():
        if not isinstance(tier, CapabilityTier):
            raise ValueError("fallback policy keys must be capability tiers")
        parsed = tuple(
            (_identifier(model_id, "fallback model id"), _reasoning_value(reasoning, "fallback"))
            for model_id, reasoning in choices
        )
        if len(parsed) != len(set(parsed)):
            raise ValueError("fallback policy choices must not contain duplicates")
        result[tier] = parsed
    return dict(sorted(result.items(), key=lambda item: _TIER_ORDER.index(item[0])))


def _parse_policy_choices(value: Mapping[str, object]) -> Dict[CapabilityTier, Tuple[str, ReasoningEffort]]:
    return {CapabilityTier(key): (_choice(item, "preferred policy")) for key, item in value.items()}


def _parse_policy_fallbacks(
    value: Mapping[str, object],
) -> Dict[CapabilityTier, Tuple[Tuple[str, ReasoningEffort], ...]]:
    return {
        CapabilityTier(key): tuple(_choice(item, "fallback policy") for item in _sequence(choices))
        for key, choices in value.items()
    }


def _choice(value: object, label: str) -> Tuple[str, ReasoningEffort]:
    parts = _sequence(value)
    if len(parts) != 2:
        raise ValueError(f"{label} choice must contain a model id and reasoning effort")
    return _identifier(parts[0], f"{label} model id"), _reasoning_value(parts[1], label)


def _reasoning_value(value: object, label: str) -> ReasoningEffort:
    try:
        return value if isinstance(value, ReasoningEffort) else ReasoningEffort(str(value))
    except ValueError as exc:
        raise ValueError(f"{label} reasoning must be a reasoning effort") from exc


def _sequence(value: object) -> Tuple[object, ...]:
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Sequence):
        raise ValueError("routing collections must be arrays")
    return tuple(value)


def _mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an object")
    return value


def _invalid_bool(label: str) -> bool:
    raise ValueError(f"{label} must be a boolean")


def _invalid_int(label: str) -> int:
    raise ValueError(f"{label} must be an integer")


def _sha(value: object, label: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", value):
        raise ValueError(f"{label} must be a sha256 content id")
    return value
