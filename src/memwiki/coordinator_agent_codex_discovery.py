from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, Mapping, Optional, Sequence, Tuple

from memwiki.coordinator_agent_registry import (
    AgentRegistry,
    IntegrityStatus,
    RegistryEntry,
    TrustStatus,
    VersionDrift,
)
from memwiki.coordinator_agents import (
    AgentDescriptor,
    AgentProduct,
    AgentSurface,
    AgentVisibility,
    ExecutionMode,
    SurfaceKind,
)


class DiscoveryAvailability(str, Enum):
    INSTALLED = "installed"
    DISABLED = "disabled"
    MISSING = "missing"
    UNKNOWN = "unknown"


_PLUGIN_REFERENCE = re.compile(r"^plugin://[a-z0-9][a-z0-9._-]*@[a-z0-9][a-z0-9._-]*/$")
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


def _safe_metadata(value: object, path: str) -> None:
    if isinstance(value, Mapping):
        if len(value) > 64:
            raise ValueError(f"{path} exceeds the bounded metadata size")
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError(f"{path} keys must be strings")
            if key.lower().replace("-", "_") in _CREDENTIAL_KEYS:
                raise ValueError(f"credential material is forbidden in {path}")
            _safe_metadata(item, f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        if len(value) > 128:
            raise ValueError(f"{path} exceeds the bounded metadata size")
        for index, item in enumerate(value):
            _safe_metadata(item, f"{path}[{index}]")
    elif value is not None and not isinstance(value, (str, int, float, bool)):
        raise ValueError(f"{path} must be JSON-compatible")


def _supervision(kind: SurfaceKind) -> str:
    if kind is SurfaceKind.COMPUTER_USE:
        return "required"
    if kind in {SurfaceKind.BROWSER, SurfaceKind.API, SurfaceKind.COMMAND, SurfaceKind.WORKER}:
        return "conditional"
    return "none"


@dataclass(frozen=True)
class CodexSurfaceManifest:
    surface_id: str
    kind: SurfaceKind
    operations: Tuple[str, ...]
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _safe_metadata(self.metadata, "Codex surface metadata")

    def surface(self, availability: DiscoveryAvailability) -> AgentSurface:
        mode = (
            ExecutionMode.CALLABLE if availability is DiscoveryAvailability.INSTALLED else ExecutionMode.METADATA_ONLY
        )
        metadata = dict(self.metadata)
        metadata.update(
            {
                "availability": availability.value,
                "discovery_mode": "metadata_only",
                "supervision": _supervision(self.kind),
            }
        )
        return AgentSurface(
            surface_id=self.surface_id,
            kind=self.kind,
            operations=self.operations,
            execution_mode=mode,
            extension_metadata=metadata,
        )


@dataclass(frozen=True)
class CodexAgentManifest:
    agent_id: str
    display_name: str
    publisher: str
    version: str
    source: str
    marketplace: str
    visibility: AgentVisibility
    availability: DiscoveryAvailability
    surfaces: Tuple[CodexSurfaceManifest, ...]
    provenance: Mapping[str, Any] = field(default_factory=dict)
    artifact_digest: Optional[str] = None

    def __post_init__(self) -> None:
        if self.source.startswith("plugin://") and not _PLUGIN_REFERENCE.fullmatch(self.source):
            raise ValueError("Codex plugins require an exact plugin reference")
        _safe_metadata(self.provenance, "Codex provenance")

    def descriptor(self) -> AgentDescriptor:
        return AgentDescriptor(
            agent_id=self.agent_id,
            display_name=self.display_name,
            product=AgentProduct.CODEX,
            visibility=self.visibility,
            publisher=self.publisher,
            version=self.version,
            source=self.source,
            surfaces=tuple(surface.surface(self.availability) for surface in self.surfaces),
            extension_metadata={
                "availability": self.availability.value,
                "discovery_mode": "metadata_only",
                "provenance": dict(self.provenance),
            },
        )


@dataclass(frozen=True)
class CodexDiscoveryResult:
    entries: Tuple[RegistryEntry, ...]
    version_drifts: Tuple[VersionDrift, ...]
    observed_at: str

    def to_dict(self) -> Dict[str, object]:
        return {
            "entries": [entry.to_dict() for entry in self.entries],
            "version_drifts": [
                {
                    "agent_id": drift.agent_id,
                    "recorded_version": drift.recorded_version,
                    "observed_version": drift.observed_version,
                    "recorded_digest": drift.recorded_digest,
                    "observed_digest": drift.observed_digest,
                }
                for drift in self.version_drifts
            ],
            "observed_at": self.observed_at,
        }


InventoryProvider = Callable[[], Sequence[CodexAgentManifest]]


class CodexAgentDiscoveryAdapter:
    """Maps a Codex metadata inventory to provider-neutral agent registry entries."""

    def __init__(self, inventory: InventoryProvider) -> None:
        self._inventory = inventory

    @classmethod
    def from_manifests(cls, manifests: Sequence[CodexAgentManifest]) -> "CodexAgentDiscoveryAdapter":
        snapshot = tuple(manifests)
        return cls(lambda: snapshot)

    def discover(self, *, observed_at: str, registry: Optional[AgentRegistry] = None) -> CodexDiscoveryResult:
        manifests = tuple(self._inventory())
        self._validate_unique(manifests)
        entries = []
        drifts = []
        for manifest in sorted(manifests, key=lambda item: item.agent_id):
            entry = self._entry(manifest, observed_at=observed_at)
            drift = self._drift(registry, entry)
            if drift is not None:
                drifts.append(drift)
                metadata = dict(entry.metadata)
                metadata["version_status"] = "stale_registry"
                entry = RegistryEntry.create(
                    entry.agent,
                    marketplace=entry.marketplace,
                    integrity=entry.integrity,
                    trust=entry.trust,
                    observed_at=entry.observed_at,
                    artifact_digest=entry.artifact_digest,
                    metadata=metadata,
                )
            entries.append(entry)
        return CodexDiscoveryResult(tuple(entries), tuple(drifts), observed_at)

    @staticmethod
    def _entry(manifest: CodexAgentManifest, *, observed_at: str) -> RegistryEntry:
        installed = manifest.availability is DiscoveryAvailability.INSTALLED
        return RegistryEntry.create(
            manifest.descriptor(),
            marketplace=manifest.marketplace,
            integrity=IntegrityStatus.UNVERIFIED if installed else IntegrityStatus.UNKNOWN,
            trust=TrustStatus.UNTRUSTED,
            observed_at=observed_at,
            artifact_digest=manifest.artifact_digest,
            metadata={
                "availability": manifest.availability.value,
                "version_status": "current",
                "discovery_mode": "metadata_only",
                "supervision_required": any(surface.kind is SurfaceKind.COMPUTER_USE for surface in manifest.surfaces),
                "provenance": dict(manifest.provenance),
            },
        )

    @staticmethod
    def _drift(registry: Optional[AgentRegistry], candidate: RegistryEntry) -> Optional[VersionDrift]:
        if registry is None:
            return None
        try:
            return registry.detect_version_drift(candidate)
        except KeyError:
            return None

    @staticmethod
    def _validate_unique(manifests: Sequence[CodexAgentManifest]) -> None:
        identities = [manifest.agent_id for manifest in manifests]
        if len(identities) != len(set(identities)):
            raise ValueError("duplicate Codex agent identifier")
        provenance = [(manifest.marketplace, manifest.source) for manifest in manifests]
        if len(provenance) != len(set(provenance)):
            raise ValueError("duplicate Codex provenance")
