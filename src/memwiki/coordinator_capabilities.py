from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, Iterable, Mapping, Optional, Tuple, cast


class CapabilityKind(str, Enum):
    PLUGIN = "plugin"
    SKILL = "skill"
    MCP = "mcp"
    API = "api"
    BROWSER = "browser"
    IMAGE_TOOL = "image_tool"
    LOCAL_COMMAND = "local_command"
    SERVICE = "service"
    SPAWNED_AGENT = "spawned_agent"


class Availability(str, Enum):
    AVAILABLE = "available"
    EXPECTED_UNAVAILABLE = "expected_unavailable"
    DOES_NOT_EXIST = "does_not_exist"
    DISABLED = "disabled"
    UNKNOWN = "unknown"


class MutationScope(str, Enum):
    READ_ONLY = "read_only"
    PROJECT_WRITE = "project_write"
    EXTERNAL_WRITE = "external_write"


class AuthRequirement(str, Enum):
    NONE = "none"
    OPTIONAL = "optional"
    REQUIRED = "required"


class NetworkRequirement(str, Enum):
    NONE = "none"
    OPTIONAL = "optional"
    REQUIRED = "required"


class CostClass(str, Enum):
    FREE = "free"
    METERED = "metered"
    HIGH = "high"


class PrivacyClass(str, Enum):
    LOCAL_ONLY = "local_only"
    NON_SENSITIVE_ONLY = "non_sensitive_only"
    SENSITIVE_ALLOWED = "sensitive_allowed"


class ReplaySafety(str, Enum):
    SAFE = "safe"
    IDEMPOTENCY_REQUIRED = "idempotency_required"
    UNSAFE = "unsafe"


class SupervisionRequirement(str, Enum):
    NONE = "none"
    CONDITIONAL = "conditional"
    REQUIRED = "required"


class SelectionStatus(str, Enum):
    SELECTED = "selected"
    AMBIGUOUS = "ambiguous"
    BLOCKED = "blocked"
    FUTURE_OPPORTUNITY = "future_opportunity"
    NO_MATCH = "no_match"


_IDENTIFIER = re.compile(r"^[a-z0-9][a-z0-9._:/+-]*$")
_CREDENTIAL_TERMS = {
    "access_token",
    "api_key",
    "authorization",
    "credential",
    "credentials",
    "password",
    "private_key",
    "refresh_token",
    "secret",
    "token",
}
_COST_RANK = {CostClass.FREE: 0, CostClass.METERED: 1, CostClass.HIGH: 2}
_PRIVACY_RANK = {
    PrivacyClass.LOCAL_ONLY: 0,
    PrivacyClass.NON_SENSITIVE_ONLY: 1,
    PrivacyClass.SENSITIVE_ALLOWED: 2,
}


def _enum_values(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {key: _enum_values(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_enum_values(item) for item in value]
    return value


def _assert_metadata_safe(value: Any, path: str = "metadata") -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError(f"{path} keys must be strings")
            normalized = key.lower().replace("-", "_")
            if normalized in _CREDENTIAL_TERMS:
                raise ValueError(f"credential material is forbidden in capability {path}")
            _assert_metadata_safe(item, f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _assert_metadata_safe(item, f"{path}[{index}]")
    elif value is not None and not isinstance(value, (str, int, float, bool)):
        raise ValueError(f"{path} must contain only JSON-compatible metadata")


@dataclass(frozen=True)
class CapabilityDescriptor:
    """Normalized discovery metadata. It intentionally contains no executor."""

    identifier: str
    display_name: str
    kind: CapabilityKind
    source: str
    operations: Tuple[str, ...]
    availability: Availability
    mutation_scope: MutationScope
    auth: AuthRequirement
    network: NetworkRequirement
    cost: CostClass
    privacy: PrivacyClass
    replay: ReplaySafety
    supervision: SupervisionRequirement
    preference: int = 100
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not _IDENTIFIER.fullmatch(self.identifier):
            raise ValueError("capability identifier must be a normalized stable identifier")
        if not self.display_name.strip() or not self.source.strip():
            raise ValueError("display name and source must be non-empty")
        if not self.operations or any(not operation.strip() for operation in self.operations):
            raise ValueError("capability operations must contain non-empty values")
        if len(set(self.operations)) != len(self.operations):
            raise ValueError("capability operations must be unique")
        if self.preference < 0:
            raise ValueError("capability preference must be non-negative")
        _assert_metadata_safe(self.metadata)

    def to_dict(self) -> Dict[str, Any]:
        return cast(Dict[str, Any], _enum_values(asdict(self)))

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> CapabilityDescriptor:
        required = {
            "identifier",
            "display_name",
            "kind",
            "source",
            "operations",
            "availability",
            "mutation_scope",
            "auth",
            "network",
            "cost",
            "privacy",
            "replay",
            "supervision",
            "preference",
            "metadata",
        }
        if set(value) != required:
            raise ValueError("capability descriptor fields do not match the schema")
        operations = value["operations"]
        metadata = value["metadata"]
        if not isinstance(operations, list) or not all(isinstance(item, str) for item in operations):
            raise ValueError("capability operations must be a string list")
        if not isinstance(metadata, dict):
            raise ValueError("capability metadata must be an object")
        try:
            return cls(
                identifier=str(value["identifier"]),
                display_name=str(value["display_name"]),
                kind=CapabilityKind(str(value["kind"])),
                source=str(value["source"]),
                operations=tuple(operations),
                availability=Availability(str(value["availability"])),
                mutation_scope=MutationScope(str(value["mutation_scope"])),
                auth=AuthRequirement(str(value["auth"])),
                network=NetworkRequirement(str(value["network"])),
                cost=CostClass(str(value["cost"])),
                privacy=PrivacyClass(str(value["privacy"])),
                replay=ReplaySafety(str(value["replay"])),
                supervision=SupervisionRequirement(str(value["supervision"])),
                preference=int(value["preference"]),
                metadata=metadata,
            )
        except (TypeError, ValueError) as error:
            raise ValueError(f"invalid capability descriptor: {error}") from error


@dataclass(frozen=True)
class CapabilityRequirement:
    operation: str
    allowed_kinds: Tuple[CapabilityKind, ...] = ()
    allow_mutation: bool = True
    allow_network: bool = True
    auth_available: bool = True
    maximum_cost: CostClass = CostClass.HIGH
    maximum_privacy: PrivacyClass = PrivacyClass.SENSITIVE_ALLOWED
    supervision_available: bool = True

    def __post_init__(self) -> None:
        if not self.operation.strip():
            raise ValueError("required operation must be non-empty")
        if len(set(self.allowed_kinds)) != len(self.allowed_kinds):
            raise ValueError("allowed capability kinds must be unique")


@dataclass(frozen=True)
class CapabilitySelection:
    status: SelectionStatus
    selected: Optional[CapabilityDescriptor]
    candidates: Tuple[CapabilityDescriptor, ...]
    blocking_capability_ids: Tuple[str, ...] = ()
    future_opportunity_ids: Tuple[str, ...] = ()
    rejections: Mapping[str, Tuple[str, ...]] = field(default_factory=dict)


class CapabilityRegistry:
    """Provider-neutral, metadata-only registry and deterministic selector."""

    def __init__(self, descriptors: Iterable[CapabilityDescriptor] = ()) -> None:
        indexed: Dict[str, CapabilityDescriptor] = {}
        for descriptor in descriptors:
            if descriptor.identifier in indexed:
                raise ValueError(f"duplicate capability identifier: {descriptor.identifier}")
            indexed[descriptor.identifier] = descriptor
        self._descriptors = indexed

    def discover(
        self,
        *,
        operation: Optional[str] = None,
        kinds: Tuple[CapabilityKind, ...] = (),
        include_unavailable: bool = True,
    ) -> Tuple[CapabilityDescriptor, ...]:
        """Return a stable snapshot without probing or invoking providers."""

        values = (
            descriptor
            for descriptor in self._descriptors.values()
            if (operation is None or operation in descriptor.operations)
            and (not kinds or descriptor.kind in kinds)
            and (include_unavailable or descriptor.availability is Availability.AVAILABLE)
        )
        return tuple(sorted(values, key=lambda item: item.identifier))

    def select(self, requirement: CapabilityRequirement) -> CapabilitySelection:
        relevant = self.discover(
            operation=requirement.operation,
            kinds=requirement.allowed_kinds,
        )
        expected = tuple(
            item.identifier
            for item in relevant
            if item.availability is Availability.EXPECTED_UNAVAILABLE
        )
        future = tuple(
            item.identifier for item in relevant if item.availability is Availability.DOES_NOT_EXIST
        )
        rejections: Dict[str, Tuple[str, ...]] = {}
        candidates = []
        for descriptor in relevant:
            if descriptor.availability is not Availability.AVAILABLE:
                continue
            reasons = self._policy_rejections(descriptor, requirement)
            if reasons:
                rejections[descriptor.identifier] = reasons
            else:
                candidates.append(descriptor)
        ranked = tuple(sorted(candidates, key=lambda item: (item.preference, item.identifier)))
        if ranked:
            tied = tuple(item for item in ranked if item.preference == ranked[0].preference)
            if len(tied) > 1:
                return CapabilitySelection(
                    status=SelectionStatus.AMBIGUOUS,
                    selected=None,
                    candidates=ranked,
                    rejections=rejections,
                )
            return CapabilitySelection(
                status=SelectionStatus.SELECTED,
                selected=ranked[0],
                candidates=ranked,
                rejections=rejections,
            )
        if expected:
            status = SelectionStatus.BLOCKED
        elif future:
            status = SelectionStatus.FUTURE_OPPORTUNITY
        else:
            status = SelectionStatus.NO_MATCH
        return CapabilitySelection(
            status=status,
            selected=None,
            candidates=(),
            blocking_capability_ids=expected,
            future_opportunity_ids=future,
            rejections=rejections,
        )

    @staticmethod
    def _policy_rejections(
        descriptor: CapabilityDescriptor,
        requirement: CapabilityRequirement,
    ) -> Tuple[str, ...]:
        reasons = []
        if descriptor.auth is AuthRequirement.REQUIRED and not requirement.auth_available:
            reasons.append("auth unavailable")
        if _COST_RANK[descriptor.cost] > _COST_RANK[requirement.maximum_cost]:
            reasons.append("cost exceeds maximum")
        if descriptor.mutation_scope is not MutationScope.READ_ONLY and not requirement.allow_mutation:
            reasons.append("mutation not allowed")
        if descriptor.network is NetworkRequirement.REQUIRED and not requirement.allow_network:
            reasons.append("network not allowed")
        if _PRIVACY_RANK[descriptor.privacy] > _PRIVACY_RANK[requirement.maximum_privacy]:
            reasons.append("privacy exceeds maximum")
        if (
            descriptor.supervision is SupervisionRequirement.REQUIRED
            and not requirement.supervision_available
        ):
            reasons.append("supervision unavailable")
        return tuple(sorted(reasons))
