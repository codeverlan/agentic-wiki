from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Optional, Protocol, Sequence, Tuple

from memwiki.coordinator_agents import (
    AgentDescriptor,
    AgentProduct,
    AgentSurface,
    AgentVisibility,
    ExecutionMode,
    SurfaceKind,
)


class ChatGPTDiscoveryState(str, Enum):
    READY = "ready"
    UNAUTHORIZED = "unauthorized"
    UNAVAILABLE = "unavailable"
    UNSUPPORTED = "unsupported"


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
_SURFACE_KINDS = {
    "app": SurfaceKind.APP,
    "connector": SurfaceKind.CONNECTOR,
    "action": SurfaceKind.ACTION,
    "remote_mcp": SurfaceKind.MCP,
    "conversational_handoff": SurfaceKind.CONVERSATIONAL_HANDOFF,
}


def _safe_metadata(value: object, path: str = "metadata") -> None:
    if isinstance(value, Mapping):
        if len(value) > 64:
            raise ValueError(f"{path} exceeds the bounded metadata size")
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError(f"{path} keys must be strings")
            if key.lower().replace("-", "_") in _CREDENTIAL_KEYS:
                raise ValueError(f"credential material is forbidden in ChatGPT {path}")
            _safe_metadata(item, f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        if len(value) > 128:
            raise ValueError(f"{path} exceeds the bounded metadata size")
        for index, item in enumerate(value):
            _safe_metadata(item, f"{path}[{index}]")
    elif value is not None and not isinstance(value, (str, int, float, bool)):
        raise ValueError(f"{path} must be JSON-compatible")


@dataclass(frozen=True)
class HostChatGPTSurface:
    surface_id: str
    kind: str
    operations: Tuple[str, ...]
    execution_mode: str
    discovery_state: str
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not _IDENTIFIER.fullmatch(self.surface_id):
            raise ValueError("ChatGPT surface identifier must be normalized")
        if self.kind not in _SURFACE_KINDS:
            raise ValueError("unknown ChatGPT surface kind")
        try:
            mode = ExecutionMode(self.execution_mode)
        except ValueError as error:
            raise ValueError("unknown ChatGPT execution mode") from error
        try:
            ChatGPTDiscoveryState(self.discovery_state)
        except ValueError as error:
            raise ValueError("unknown ChatGPT discovery state") from error
        if not self.operations or any(not item.strip() for item in self.operations):
            raise ValueError("ChatGPT surface operations must be non-empty")
        if len(set(self.operations)) != len(self.operations):
            raise ValueError("ChatGPT surface operations must be unique")
        if self.kind == "conversational_handoff" and mode is not ExecutionMode.HANDOFF_ONLY:
            raise ValueError("conversational handoff must use handoff-only execution")
        _safe_metadata(self.metadata, "surface metadata")


@dataclass(frozen=True)
class HostChatGPTAgent:
    agent_id: str
    display_name: str
    visibility: str
    publisher: str
    version: str
    source: str
    surfaces: Tuple[HostChatGPTSurface, ...]
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not _IDENTIFIER.fullmatch(self.agent_id):
            raise ValueError("ChatGPT agent identifier must be normalized")
        if any(not value.strip() for value in (self.display_name, self.publisher, self.version, self.source)):
            raise ValueError("ChatGPT agent identity fields must be non-empty")
        try:
            AgentVisibility(self.visibility)
        except ValueError as error:
            raise ValueError("unknown ChatGPT agent visibility") from error
        if not self.surfaces:
            raise ValueError("ChatGPT agent must declare at least one surface")
        _safe_metadata(self.metadata, "agent metadata")


@dataclass(frozen=True)
class ChatGPTHostLimitation:
    state: ChatGPTDiscoveryState
    message: str

    def __post_init__(self) -> None:
        if self.state is ChatGPTDiscoveryState.READY:
            raise ValueError("a ready host does not have a discovery limitation")
        if not self.message.strip():
            raise ValueError("host limitation message must be non-empty")


class ChatGPTDiscoveryHost(Protocol):
    """Provider-neutral callbacks exposed by a ChatGPT-capable host."""

    def discovery_state(self) -> ChatGPTDiscoveryState: ...

    def discovery_limitation(self) -> Optional[str]: ...

    def list_agents(self) -> Sequence[HostChatGPTAgent]: ...


class ChatGPTCapabilityDiscoveryAdapter:
    """Maps declared ChatGPT surfaces without probing or invoking them."""

    def __init__(self, host: ChatGPTDiscoveryHost) -> None:
        self._host = host
        self.last_limitation: Optional[ChatGPTHostLimitation] = None

    def discover(self) -> Tuple[AgentDescriptor, ...]:
        state = self._host.discovery_state()
        if state is not ChatGPTDiscoveryState.READY:
            message = self._host.discovery_limitation()
            if not message:
                message = f"ChatGPT programmatic discovery is {state.value}."
            self.last_limitation = ChatGPTHostLimitation(state, message)
            return ()

        self.last_limitation = None
        agents = tuple(self._host.list_agents())
        agent_ids = [agent.agent_id for agent in agents]
        if len(agent_ids) != len(set(agent_ids)):
            raise ValueError("duplicate ChatGPT agent identifier")
        mapped = tuple(self._map_agent(agent) for agent in agents)
        return tuple(sorted(mapped, key=lambda agent: agent.agent_id))

    @classmethod
    def _map_agent(cls, agent: HostChatGPTAgent) -> AgentDescriptor:
        surface_ids = [surface.surface_id for surface in agent.surfaces]
        if len(surface_ids) != len(set(surface_ids)):
            raise ValueError("duplicate ChatGPT surface identifier")
        return AgentDescriptor(
            agent_id=agent.agent_id,
            display_name=agent.display_name,
            product=AgentProduct.CHATGPT,
            visibility=AgentVisibility(agent.visibility),
            publisher=agent.publisher,
            version=agent.version,
            source=agent.source,
            surfaces=tuple(cls._map_surface(surface) for surface in agent.surfaces),
            extension_metadata={**dict(agent.metadata), "host": "chatgpt"},
        )

    @staticmethod
    def _map_surface(surface: HostChatGPTSurface) -> AgentSurface:
        declared_mode = ExecutionMode(surface.execution_mode)
        state = ChatGPTDiscoveryState(surface.discovery_state)
        effective_mode = declared_mode
        if state is not ChatGPTDiscoveryState.READY:
            effective_mode = ExecutionMode.METADATA_ONLY
        metadata = {
            **dict(surface.metadata),
            "discovery_state": state.value,
            "declared_execution_mode": declared_mode.value,
        }
        return AgentSurface(
            surface_id=surface.surface_id,
            kind=_SURFACE_KINDS[surface.kind],
            operations=surface.operations,
            execution_mode=effective_mode,
            extension_metadata=metadata,
        )


class DeterministicChatGPTHost:
    """In-memory discovery host; it intentionally has no invocation callback."""

    def __init__(
        self,
        agents: Sequence[HostChatGPTAgent] = (),
        *,
        discovery_state: ChatGPTDiscoveryState = ChatGPTDiscoveryState.READY,
        limitation: Optional[str] = None,
    ) -> None:
        if discovery_state is ChatGPTDiscoveryState.READY and limitation is not None:
            raise ValueError("a ready deterministic host cannot declare a limitation")
        self._agents = tuple(agents)
        self._discovery_state = discovery_state
        self._limitation = limitation
        self.discovery_count = 0
        self.invocation_count = 0

    def discovery_state(self) -> ChatGPTDiscoveryState:
        return self._discovery_state

    def discovery_limitation(self) -> Optional[str]:
        return self._limitation

    def list_agents(self) -> Sequence[HostChatGPTAgent]:
        self.discovery_count += 1
        return self._agents
