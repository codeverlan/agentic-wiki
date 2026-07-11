from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, Mapping, Tuple, cast


class AgentProduct(str, Enum):
    CODEX = "codex"
    CHATGPT = "chatgpt"
    OTHER = "other"


class AgentVisibility(str, Enum):
    PUBLIC = "public"
    ORGANIZATION_PRIVATE = "organization_private"
    PERSONAL_PRIVATE = "personal_private"
    PROJECT_LOCAL = "project_local"


class SurfaceKind(str, Enum):
    SKILL = "skill"
    PLUGIN = "plugin"
    MCP = "mcp"
    APP = "app"
    CONNECTOR = "connector"
    ACTION = "action"
    API = "api"
    COMMAND = "command"
    BROWSER = "browser"
    COMPUTER_USE = "computer_use"
    IMAGE_TOOL = "image_tool"
    WORKER = "worker"
    CONVERSATIONAL_HANDOFF = "conversational_handoff"


class ExecutionMode(str, Enum):
    CALLABLE = "callable"
    HANDOFF_ONLY = "handoff_only"
    METADATA_ONLY = "metadata_only"


_IDENTIFIER = re.compile(r"^[a-z0-9][a-z0-9._:/+@-]*$")
_CREDENTIAL_KEYS = {
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


def _safe_metadata(value: object, path: str = "extension_metadata") -> None:
    if isinstance(value, Mapping):
        if len(value) > 64:
            raise ValueError(f"{path} exceeds the bounded extension size")
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError(f"{path} keys must be strings")
            if key.lower().replace("-", "_") in _CREDENTIAL_KEYS:
                raise ValueError(f"credential material is forbidden in {path}")
            _safe_metadata(item, f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        if len(value) > 128:
            raise ValueError(f"{path} exceeds the bounded extension size")
        for index, item in enumerate(value):
            _safe_metadata(item, f"{path}[{index}]")
    elif value is not None and not isinstance(value, (str, int, float, bool)):
        raise ValueError(f"{path} must be JSON-compatible")


def _enum_values(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {key: _enum_values(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_enum_values(item) for item in value]
    return value


@dataclass(frozen=True)
class AgentSurface:
    surface_id: str
    kind: SurfaceKind
    operations: Tuple[str, ...]
    execution_mode: ExecutionMode
    extension_metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not _IDENTIFIER.fullmatch(self.surface_id):
            raise ValueError("surface identifier must be normalized")
        if not self.operations or any(not item.strip() for item in self.operations):
            raise ValueError("surface operations must be non-empty")
        if len(set(self.operations)) != len(self.operations):
            raise ValueError("surface operations must be unique")
        _safe_metadata(self.extension_metadata)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "AgentSurface":
        required = {"surface_id", "kind", "operations", "execution_mode", "extension_metadata"}
        if set(value) != required or not isinstance(value["operations"], list):
            raise ValueError("agent surface fields do not match the schema")
        metadata = value["extension_metadata"]
        if not isinstance(metadata, dict):
            raise ValueError("surface extension_metadata must be an object")
        return cls(
            surface_id=str(value["surface_id"]),
            kind=SurfaceKind(str(value["kind"])),
            operations=tuple(str(item) for item in value["operations"]),
            execution_mode=ExecutionMode(str(value["execution_mode"])),
            extension_metadata=metadata,
        )


@dataclass(frozen=True)
class AgentDescriptor:
    agent_id: str
    display_name: str
    product: AgentProduct
    visibility: AgentVisibility
    publisher: str
    version: str
    source: str
    surfaces: Tuple[AgentSurface, ...]
    extension_metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not _IDENTIFIER.fullmatch(self.agent_id):
            raise ValueError("agent identifier must be normalized")
        if any(not value.strip() for value in (self.display_name, self.publisher, self.version, self.source)):
            raise ValueError("agent identity fields must be non-empty")
        if not self.surfaces:
            raise ValueError("agent must expose at least one surface")
        surface_ids = [item.surface_id for item in self.surfaces]
        if len(surface_ids) != len(set(surface_ids)):
            raise ValueError("duplicate surface identifier")
        _safe_metadata(self.extension_metadata)

    @property
    def callable_operations(self) -> Tuple[str, ...]:
        return tuple(
            sorted(
                {
                    operation
                    for surface in self.surfaces
                    if surface.execution_mode is ExecutionMode.CALLABLE
                    for operation in surface.operations
                }
            )
        )

    @property
    def handoff_operations(self) -> Tuple[str, ...]:
        return tuple(
            sorted(
                {
                    operation
                    for surface in self.surfaces
                    if surface.execution_mode is ExecutionMode.HANDOFF_ONLY
                    for operation in surface.operations
                }
            )
        )

    def to_dict(self) -> Dict[str, Any]:
        return cast(Dict[str, Any], _enum_values(asdict(self)))

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "AgentDescriptor":
        required = {
            "agent_id",
            "display_name",
            "product",
            "visibility",
            "publisher",
            "version",
            "source",
            "surfaces",
            "extension_metadata",
        }
        if set(value) != required or not isinstance(value["surfaces"], list):
            raise ValueError("agent descriptor fields do not match the schema")
        metadata = value["extension_metadata"]
        if not isinstance(metadata, dict):
            raise ValueError("agent extension_metadata must be an object")
        return cls(
            agent_id=str(value["agent_id"]),
            display_name=str(value["display_name"]),
            product=AgentProduct(str(value["product"])),
            visibility=AgentVisibility(str(value["visibility"])),
            publisher=str(value["publisher"]),
            version=str(value["version"]),
            source=str(value["source"]),
            surfaces=tuple(AgentSurface.from_dict(item) for item in value["surfaces"]),
            extension_metadata=metadata,
        )
