from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any, Dict, Mapping, Optional, Tuple, Type, TypeVar, Union, cast

from memwiki.coordinator_agent_registry import (
    IntegrityStatus,
    RegistryEntry,
    TrustStatus,
)
from memwiki.coordinator_agents import ExecutionMode
from memwiki.coordinator_capabilities import CostClass, SupervisionRequirement


class PolicyValue(str, Enum):
    YES = "yes"
    NO = "no"
    UNKNOWN = "unknown"


class AuthAvailability(str, Enum):
    NOT_REQUIRED = "not_required"
    REFERENCE_AVAILABLE = "reference_available"
    REFERENCE_MISSING = "reference_missing"
    UNKNOWN = "unknown"


class DataPolicy(str, Enum):
    NONE = "none"
    LOCAL = "local"
    REMOTE = "remote"
    RETAINED = "retained"
    ENABLED = "enabled"
    UNKNOWN = "unknown"


class LatencyClass(str, Enum):
    INTERACTIVE = "interactive"
    BATCH = "batch"
    LONG_RUNNING = "long_running"
    UNKNOWN = "unknown"


class QualificationStatus(str, Enum):
    QUALIFIED = "qualified"
    DOWNGRADED = "downgraded"
    REJECTED = "rejected"


SensitivePolicy = Union[DataPolicy, PolicyValue]
_E = TypeVar("_E", bound=Enum)
_SCHEMA = re.compile(r"^[a-z0-9][a-z0-9._:/+-]*$")
_AUTH_VALUES = {item.value for item in AuthAvailability}
_COST_RANK = {CostClass.FREE: 0, CostClass.METERED: 1, CostClass.HIGH: 2}
_LATENCY_RANK = {
    LatencyClass.INTERACTIVE: 0,
    LatencyClass.BATCH: 1,
    LatencyClass.LONG_RUNNING: 2,
    LatencyClass.UNKNOWN: 3,
}


def _enum_value(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {key: _enum_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_enum_value(item) for item in value]
    return value


def _bounded_identifiers(values: Tuple[str, ...], label: str) -> None:
    if not values or len(values) > 64:
        raise ValueError(f"{label} must contain between 1 and 64 values")
    if len(set(values)) != len(values):
        raise ValueError(f"{label} values must be unique")
    if any(not _SCHEMA.fullmatch(value) for value in values):
        raise ValueError(f"{label} contains an invalid identifier")


def _parse_enum(enum_type: Type[_E], value: object, label: str) -> _E:
    try:
        return enum_type(str(value))
    except ValueError as error:
        raise ValueError(f"invalid {label}") from error


def _parse_sensitive(value: object, label: str) -> SensitivePolicy:
    text = str(value)
    try:
        return DataPolicy(text)
    except ValueError:
        try:
            return PolicyValue(text)
        except ValueError as error:
            raise ValueError(f"invalid {label}") from error


@dataclass(frozen=True)
class AgentPolicy:
    """Policy declared by an agent; auth is availability metadata, never a secret."""

    input_schemas: Tuple[str, ...]
    output_schemas: Tuple[str, ...]
    authority: Tuple[str, ...]
    auth: AuthAvailability
    data_residency: SensitivePolicy
    retention: SensitivePolicy
    telemetry: SensitivePolicy
    cost: CostClass
    latency: LatencyClass
    supervision: SupervisionRequirement
    reversible: PolicyValue
    replayable: PolicyValue
    phi_eligible: PolicyValue

    def __post_init__(self) -> None:
        _bounded_identifiers(self.input_schemas, "input schemas")
        _bounded_identifiers(self.output_schemas, "output schemas")
        _bounded_identifiers(self.authority, "authority")

    def to_dict(self) -> Dict[str, Any]:
        return cast(Dict[str, Any], _enum_value(asdict(self)))

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "AgentPolicy":
        required = {
            "input_schemas", "output_schemas", "authority", "auth",
            "data_residency", "retention", "telemetry", "cost", "latency",
            "supervision", "reversible", "replayable", "phi_eligible",
        }
        if set(value) != required:
            raise ValueError("agent policy fields do not match the schema")
        auth = str(value["auth"])
        if auth not in _AUTH_VALUES:
            raise ValueError("auth policy must describe availability by reference only")
        try:
            return cls(
                input_schemas=_string_tuple(value["input_schemas"], "input schemas"),
                output_schemas=_string_tuple(value["output_schemas"], "output schemas"),
                authority=_string_tuple(value["authority"], "authority"),
                auth=AuthAvailability(auth),
                data_residency=_parse_sensitive(value["data_residency"], "data residency"),
                retention=_parse_sensitive(value["retention"], "retention"),
                telemetry=_parse_sensitive(value["telemetry"], "telemetry"),
                cost=_parse_enum(CostClass, value["cost"], "cost"),
                latency=_parse_enum(LatencyClass, value["latency"], "latency"),
                supervision=_parse_enum(
                    SupervisionRequirement, value["supervision"], "supervision"
                ),
                reversible=_parse_enum(PolicyValue, value["reversible"], "reversible"),
                replayable=_parse_enum(PolicyValue, value["replayable"], "replayable"),
                phi_eligible=_parse_enum(PolicyValue, value["phi_eligible"], "PHI eligibility"),
            )
        except (TypeError, ValueError) as error:
            if "reference only" in str(error):
                raise
            raise ValueError(f"invalid agent policy: {error}") from error


@dataclass(frozen=True)
class AgentRequirement:
    operation: str
    accepted_input_schemas: Tuple[str, ...]
    accepted_output_schemas: Tuple[str, ...]
    allowed_authority: Tuple[str, ...]
    auth_reference_available: bool
    allowed_residency: Tuple[DataPolicy, ...]
    allowed_retention: Tuple[DataPolicy, ...]
    allowed_telemetry: Tuple[DataPolicy, ...]
    maximum_cost: CostClass
    maximum_latency: LatencyClass
    supervision_available: bool
    require_reversible: bool
    require_replayable: bool
    contains_phi: bool
    allow_handoff: bool
    callable_authorized: bool = True

    def __post_init__(self) -> None:
        _bounded_identifiers((self.operation,), "operation")
        _bounded_identifiers(self.accepted_input_schemas, "accepted input schemas")
        _bounded_identifiers(self.accepted_output_schemas, "accepted output schemas")
        _bounded_identifiers(self.allowed_authority, "allowed authority")
        if not self.allowed_residency or not self.allowed_retention or not self.allowed_telemetry:
            raise ValueError("allowed data policies must be non-empty")

    def to_dict(self) -> Dict[str, Any]:
        return cast(Dict[str, Any], _enum_value(asdict(self)))


@dataclass(frozen=True)
class QualificationReceipt:
    receipt_id: str
    agent_id: str
    stable_identity: str
    agent_version: str
    operation: str
    status: QualificationStatus
    execution_mode: Optional[ExecutionMode]
    surface_id: Optional[str]
    input_schema: Optional[str]
    output_schema: Optional[str]
    granted_authority: Tuple[str, ...]
    rejection_reasons: Tuple[str, ...]
    downgrade_reasons: Tuple[str, ...]
    policy_snapshot: Mapping[str, Any]
    requirement_snapshot: Mapping[str, Any]

    def __post_init__(self) -> None:
        expected = _receipt_id(self._payload())
        if self.receipt_id != expected:
            raise ValueError("receipt id does not match qualification content")

    def _payload(self) -> Dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "stable_identity": self.stable_identity,
            "agent_version": self.agent_version,
            "operation": self.operation,
            "status": self.status.value,
            "execution_mode": None if self.execution_mode is None else self.execution_mode.value,
            "surface_id": self.surface_id,
            "input_schema": self.input_schema,
            "output_schema": self.output_schema,
            "granted_authority": list(self.granted_authority),
            "rejection_reasons": list(self.rejection_reasons),
            "downgrade_reasons": list(self.downgrade_reasons),
            "policy_snapshot": dict(self.policy_snapshot),
            "requirement_snapshot": dict(self.requirement_snapshot),
        }

    def to_dict(self) -> Dict[str, Any]:
        return {"receipt_id": self.receipt_id, **self._payload()}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "QualificationReceipt":
        required = {
            "receipt_id", "agent_id", "stable_identity", "agent_version", "operation",
            "status", "execution_mode", "surface_id", "input_schema", "output_schema",
            "granted_authority", "rejection_reasons", "downgrade_reasons",
            "policy_snapshot", "requirement_snapshot",
        }
        if set(value) != required:
            raise ValueError("qualification receipt fields do not match the schema")
        mode = value["execution_mode"]
        return cls(
            receipt_id=str(value["receipt_id"]),
            agent_id=str(value["agent_id"]),
            stable_identity=str(value["stable_identity"]),
            agent_version=str(value["agent_version"]),
            operation=str(value["operation"]),
            status=QualificationStatus(str(value["status"])),
            execution_mode=None if mode is None else ExecutionMode(str(mode)),
            surface_id=_optional_string(value["surface_id"]),
            input_schema=_optional_string(value["input_schema"]),
            output_schema=_optional_string(value["output_schema"]),
            granted_authority=_string_tuple(value["granted_authority"], "granted authority"),
            rejection_reasons=_string_tuple(value["rejection_reasons"], "rejection reasons", empty=True),
            downgrade_reasons=_string_tuple(value["downgrade_reasons"], "downgrade reasons", empty=True),
            policy_snapshot=_mapping(value["policy_snapshot"], "policy snapshot"),
            requirement_snapshot=_mapping(value["requirement_snapshot"], "requirement snapshot"),
        )


def _string_tuple(value: object, label: str, *, empty: bool = False) -> Tuple[str, ...]:
    if not isinstance(value, (list, tuple)) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"{label} must be a string list")
    result = tuple(value)
    if not empty and not result:
        raise ValueError(f"{label} must be non-empty")
    return result


def _mapping(value: object, label: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return value


def _optional_string(value: object) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("optional receipt values must be strings or null")
    return value


def _receipt_id(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _unknown(value: SensitivePolicy) -> bool:
    return value in {DataPolicy.UNKNOWN, PolicyValue.UNKNOWN}


def qualify_agent(
    entry: RegistryEntry,
    policy: AgentPolicy,
    requirement: AgentRequirement,
) -> QualificationReceipt:
    """Negotiate one operation without probing, authenticating, or invoking the agent."""

    surfaces = tuple(
        sorted(
            (surface for surface in entry.agent.surfaces if requirement.operation in surface.operations),
            key=lambda surface: surface.surface_id,
        )
    )
    reasons = []
    if entry.trust is not TrustStatus.TRUSTED:
        reasons.append("agent is not trusted")
    if entry.integrity is not IntegrityStatus.VERIFIED:
        reasons.append("agent integrity is not verified")
    if not surfaces:
        reasons.append("operation unavailable")

    input_common = sorted(set(policy.input_schemas) & set(requirement.accepted_input_schemas))
    output_common = sorted(set(policy.output_schemas) & set(requirement.accepted_output_schemas))
    if not input_common:
        reasons.append("input schema incompatible")
    if not output_common:
        reasons.append("output schema incompatible")
    excess = sorted(set(policy.authority) - set(requirement.allowed_authority))
    if excess:
        reasons.append("authority exceeds allowance")
    if policy.auth is AuthAvailability.UNKNOWN:
        reasons.append("auth availability unknown")
    elif policy.auth is AuthAvailability.REFERENCE_MISSING or (
        policy.auth is AuthAvailability.REFERENCE_AVAILABLE
        and not requirement.auth_reference_available
    ):
        reasons.append("auth reference unavailable")

    sensitive = {
        "data residency": policy.data_residency,
        "retention": policy.retention,
        "telemetry": policy.telemetry,
        "reversible": policy.reversible,
        "replayable": policy.replayable,
        "phi eligible": policy.phi_eligible,
    }
    for label, value in sensitive.items():
        if _unknown(value):
            reasons.append(f"{label} unknown")
    if isinstance(policy.data_residency, DataPolicy) and policy.data_residency not in requirement.allowed_residency:
        reasons.append("data residency not allowed")
    if isinstance(policy.retention, DataPolicy) and policy.retention not in requirement.allowed_retention:
        reasons.append("retention not allowed")
    if isinstance(policy.telemetry, DataPolicy) and policy.telemetry not in requirement.allowed_telemetry:
        reasons.append("telemetry not allowed")
    if _COST_RANK[policy.cost] > _COST_RANK[requirement.maximum_cost]:
        reasons.append("cost exceeds maximum")
    if _LATENCY_RANK[policy.latency] > _LATENCY_RANK[requirement.maximum_latency]:
        reasons.append("latency exceeds maximum")
    if policy.supervision is SupervisionRequirement.REQUIRED and not requirement.supervision_available:
        reasons.append("supervision unavailable")
    if requirement.require_reversible and policy.reversible is not PolicyValue.YES:
        reasons.append("reversibility required")
    if requirement.require_replayable and policy.replayable is not PolicyValue.YES:
        reasons.append("replay required")
    if requirement.contains_phi:
        if policy.phi_eligible is not PolicyValue.YES:
            reasons.append("PHI eligibility denied")
        if policy.data_residency is not DataPolicy.LOCAL:
            reasons.append("PHI requires local residency")
        if policy.telemetry is not DataPolicy.NONE:
            reasons.append("PHI requires telemetry disabled")

    surface = surfaces[0] if surfaces else None
    mode: Optional[ExecutionMode] = None
    downgrades = []
    if surface is not None:
        mode = surface.execution_mode
        if mode is ExecutionMode.METADATA_ONLY:
            reasons.append("surface is metadata-only")
            mode = None
        elif mode is ExecutionMode.CALLABLE and not requirement.callable_authorized:
            if requirement.allow_handoff:
                mode = ExecutionMode.HANDOFF_ONLY
                downgrades.append("callable execution not authorized")
            else:
                reasons.append("callable execution not authorized")
                mode = None
        elif mode is ExecutionMode.HANDOFF_ONLY and not requirement.allow_handoff:
            reasons.append("handoff not allowed")
            mode = None

    rejection_reasons = tuple(sorted(set(reasons)))
    downgrade_reasons = tuple(sorted(set(downgrades))) if not rejection_reasons else ()
    status = (
        QualificationStatus.REJECTED
        if rejection_reasons
        else QualificationStatus.DOWNGRADED
        if downgrade_reasons
        else QualificationStatus.QUALIFIED
    )
    if status is QualificationStatus.REJECTED:
        mode = None
    granted_authority = tuple(sorted(set(policy.authority) & set(requirement.allowed_authority)))
    payload = {
        "agent_id": entry.agent.agent_id,
        "stable_identity": entry.stable_identity,
        "agent_version": entry.agent.version,
        "operation": requirement.operation,
        "status": status.value,
        "execution_mode": None if mode is None else mode.value,
        "surface_id": None if surface is None else surface.surface_id,
        "input_schema": input_common[0] if input_common else None,
        "output_schema": output_common[0] if output_common else None,
        "granted_authority": list(granted_authority),
        "rejection_reasons": list(rejection_reasons),
        "downgrade_reasons": list(downgrade_reasons),
        "policy_snapshot": policy.to_dict(),
        "requirement_snapshot": requirement.to_dict(),
    }
    return QualificationReceipt(
        receipt_id=_receipt_id(payload),
        agent_id=entry.agent.agent_id,
        stable_identity=entry.stable_identity,
        agent_version=entry.agent.version,
        operation=requirement.operation,
        status=status,
        execution_mode=mode,
        surface_id=None if surface is None else surface.surface_id,
        input_schema=input_common[0] if input_common else None,
        output_schema=output_common[0] if output_common else None,
        granted_authority=granted_authority,
        rejection_reasons=rejection_reasons,
        downgrade_reasons=downgrade_reasons,
        policy_snapshot=policy.to_dict(),
        requirement_snapshot=requirement.to_dict(),
    )
