from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from enum import Enum
from typing import Any, Dict, Mapping, Optional, Tuple

from memwiki.coordinator_agents import AgentDescriptor, AgentVisibility


class IntegrityStatus(str, Enum):
    VERIFIED = "verified"
    UNVERIFIED = "unverified"
    MISMATCH = "mismatch"
    UNKNOWN = "unknown"


class TrustStatus(str, Enum):
    TRUSTED = "trusted"
    UNTRUSTED = "untrusted"
    BLOCKED = "blocked"


class RegistryEventKind(str, Enum):
    REGISTERED = "registered"
    UPGRADED = "upgraded"
    ROLLED_BACK = "rolled_back"
    ASSURANCE_CHANGED = "assurance_changed"


_IDENTIFIER = re.compile(r"^[a-z0-9][a-z0-9._:/+@-]*$")
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
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


def _timestamp(value: str) -> None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("registry timestamp must be ISO 8601") from error
    if parsed.tzinfo is None:
        raise ValueError("registry timestamp must include a timezone")


def _safe_metadata(value: object, path: str = "metadata") -> None:
    if isinstance(value, Mapping):
        if len(value) > 64:
            raise ValueError(f"{path} exceeds the bounded metadata size")
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError(f"{path} keys must be strings")
            if key.lower().replace("-", "_") in _CREDENTIAL_KEYS:
                raise ValueError(f"credential material is forbidden in registry {path}")
            _safe_metadata(item, f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        if len(value) > 128:
            raise ValueError(f"{path} exceeds the bounded metadata size")
        for index, item in enumerate(value):
            _safe_metadata(item, f"{path}[{index}]")
    elif value is not None and not isinstance(value, (str, int, float, bool)):
        raise ValueError(f"{path} must be JSON-compatible")


def _identity_payload(agent: AgentDescriptor, marketplace: str) -> Dict[str, str]:
    return {
        "agent_id": agent.agent_id,
        "marketplace": marketplace,
        "product": agent.product.value,
        "publisher": agent.publisher,
        "source": agent.source,
        "visibility": agent.visibility.value,
    }


def _stable_identity(agent: AgentDescriptor, marketplace: str) -> str:
    encoded = json.dumps(
        _identity_payload(agent, marketplace), sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _classification(visibility: AgentVisibility) -> str:
    if visibility is AgentVisibility.PUBLIC:
        return "public"
    if visibility in {
        AgentVisibility.ORGANIZATION_PRIVATE,
        AgentVisibility.PERSONAL_PRIVATE,
    }:
        return "personalized_private"
    return "project_local"


@dataclass(frozen=True)
class RegistryEntry:
    agent: AgentDescriptor
    marketplace: str
    stable_identity: str
    classification: str
    integrity: IntegrityStatus
    trust: TrustStatus
    observed_at: str
    artifact_digest: Optional[str]
    metadata: Mapping[str, Any]

    def __post_init__(self) -> None:
        if not _IDENTIFIER.fullmatch(self.marketplace):
            raise ValueError("marketplace must be a normalized identifier")
        _timestamp(self.observed_at)
        expected = _stable_identity(self.agent, self.marketplace)
        if self.stable_identity != expected:
            raise ValueError("stable identity does not match agent provenance")
        if self.classification != _classification(self.agent.visibility):
            raise ValueError("agent classification does not match visibility")
        if self.artifact_digest is not None and not _DIGEST.fullmatch(self.artifact_digest):
            raise ValueError("artifact digest must be a sha256 digest")
        _safe_metadata(self.metadata)

    @classmethod
    def create(
        cls,
        agent: AgentDescriptor,
        *,
        marketplace: str,
        integrity: IntegrityStatus,
        trust: TrustStatus,
        observed_at: str,
        artifact_digest: Optional[str] = None,
        metadata: Optional[Mapping[str, Any]] = None,
    ) -> "RegistryEntry":
        return cls(
            agent=agent,
            marketplace=marketplace,
            stable_identity=_stable_identity(agent, marketplace),
            classification=_classification(agent.visibility),
            integrity=integrity,
            trust=trust,
            observed_at=observed_at,
            artifact_digest=artifact_digest,
            metadata={} if metadata is None else metadata,
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "agent": self.agent.to_dict(),
            "marketplace": self.marketplace,
            "stable_identity": self.stable_identity,
            "classification": self.classification,
            "integrity": self.integrity.value,
            "trust": self.trust.value,
            "observed_at": self.observed_at,
            "artifact_digest": self.artifact_digest,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "RegistryEntry":
        required = {
            "agent",
            "marketplace",
            "stable_identity",
            "classification",
            "integrity",
            "trust",
            "observed_at",
            "artifact_digest",
            "metadata",
        }
        if set(value) != required or not isinstance(value["agent"], dict):
            raise ValueError("registry entry fields do not match the schema")
        if not isinstance(value["metadata"], dict):
            raise ValueError("registry metadata must be an object")
        digest = value["artifact_digest"]
        if digest is not None and not isinstance(digest, str):
            raise ValueError("artifact digest must be a string or null")
        return cls(
            agent=AgentDescriptor.from_dict(value["agent"]),
            marketplace=str(value["marketplace"]),
            stable_identity=str(value["stable_identity"]),
            classification=str(value["classification"]),
            integrity=IntegrityStatus(str(value["integrity"])),
            trust=TrustStatus(str(value["trust"])),
            observed_at=str(value["observed_at"]),
            artifact_digest=digest,
            metadata=value["metadata"],
        )


@dataclass(frozen=True)
class VersionDrift:
    agent_id: str
    recorded_version: str
    observed_version: str
    recorded_digest: Optional[str]
    observed_digest: Optional[str]


@dataclass(frozen=True)
class RegistryEvent:
    kind: RegistryEventKind
    agent_id: str
    occurred_at: str
    from_version: Optional[str]
    to_version: str
    reason: str
    stable_identity: str

    def __post_init__(self) -> None:
        _timestamp(self.occurred_at)
        if not self.reason.strip():
            raise ValueError("registry event reason must be non-empty")

    def to_dict(self) -> Dict[str, Any]:
        value = asdict(self)
        value["kind"] = self.kind.value
        return value

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "RegistryEvent":
        required = {
            "kind",
            "agent_id",
            "occurred_at",
            "from_version",
            "to_version",
            "reason",
            "stable_identity",
        }
        if set(value) != required:
            raise ValueError("registry event fields do not match the schema")
        previous = value["from_version"]
        if previous is not None and not isinstance(previous, str):
            raise ValueError("event from_version must be a string or null")
        return cls(
            kind=RegistryEventKind(str(value["kind"])),
            agent_id=str(value["agent_id"]),
            occurred_at=str(value["occurred_at"]),
            from_version=previous,
            to_version=str(value["to_version"]),
            reason=str(value["reason"]),
            stable_identity=str(value["stable_identity"]),
        )


@dataclass(frozen=True)
class AgentRegistry:
    entries: Tuple[RegistryEntry, ...]
    history: Tuple[RegistryEvent, ...]

    def __post_init__(self) -> None:
        ids = [entry.agent.agent_id for entry in self.entries]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate agent identifier in registry")
        coordinates = [(entry.marketplace, entry.agent.source) for entry in self.entries]
        if len(coordinates) != len(set(coordinates)):
            raise ValueError("duplicate provenance coordinate in registry")

    @classmethod
    def empty(cls) -> "AgentRegistry":
        return cls(entries=(), history=())

    def get(self, agent_id: str) -> RegistryEntry:
        for entry in self.entries:
            if entry.agent.agent_id == agent_id:
                return entry
        raise KeyError(agent_id)

    def register(self, entry: RegistryEntry) -> "AgentRegistry":
        current = self._optional(entry.agent.agent_id)
        if current is not None:
            if current.stable_identity != entry.stable_identity:
                raise ValueError("stable agent ID collision or spoofing detected")
            if current.agent.version != entry.agent.version or (
                current.artifact_digest != entry.artifact_digest
            ):
                raise ValueError("version drift requires explicit upgrade or rollback")
            if current == entry:
                return self
            raise ValueError("existing registry entry requires an explicit operation")
        for existing in self.entries:
            if (existing.marketplace, existing.agent.source) == (
                entry.marketplace,
                entry.agent.source,
            ):
                raise ValueError("agent provenance collision detected")
        event = RegistryEvent(
            kind=RegistryEventKind.REGISTERED,
            agent_id=entry.agent.agent_id,
            occurred_at=entry.observed_at,
            from_version=None,
            to_version=entry.agent.version,
            reason="initial registration",
            stable_identity=entry.stable_identity,
        )
        return AgentRegistry(
            entries=tuple(sorted((*self.entries, entry), key=lambda item: item.agent.agent_id)),
            history=(*self.history, event),
        )

    def detect_version_drift(self, observed: RegistryEntry) -> Optional[VersionDrift]:
        current = self._optional(observed.agent.agent_id)
        if current is None:
            return None
        if current.stable_identity != observed.stable_identity:
            raise ValueError("stable agent ID collision or spoofing detected")
        if (
            current.agent.version == observed.agent.version
            and current.artifact_digest == observed.artifact_digest
        ):
            return None
        return VersionDrift(
            agent_id=observed.agent.agent_id,
            recorded_version=current.agent.version,
            observed_version=observed.agent.version,
            recorded_digest=current.artifact_digest,
            observed_digest=observed.artifact_digest,
        )

    def upgrade(self, entry: RegistryEntry, *, reason: str) -> "AgentRegistry":
        return self._replace_version(entry, RegistryEventKind.UPGRADED, reason)

    def rollback(self, entry: RegistryEntry, *, reason: str) -> "AgentRegistry":
        return self._replace_version(entry, RegistryEventKind.ROLLED_BACK, reason)

    def set_assurance(
        self,
        agent_id: str,
        *,
        integrity: IntegrityStatus,
        trust: TrustStatus,
        observed_at: str,
        reason: str,
    ) -> "AgentRegistry":
        current = self.get(agent_id)
        updated = replace(current, integrity=integrity, trust=trust, observed_at=observed_at)
        return self._replace(
            updated,
            RegistryEvent(
                kind=RegistryEventKind.ASSURANCE_CHANGED,
                agent_id=agent_id,
                occurred_at=observed_at,
                from_version=current.agent.version,
                to_version=current.agent.version,
                reason=reason,
                stable_identity=current.stable_identity,
            ),
        )

    def _replace_version(
        self, entry: RegistryEntry, kind: RegistryEventKind, reason: str
    ) -> "AgentRegistry":
        current = self.get(entry.agent.agent_id)
        if current.stable_identity != entry.stable_identity:
            raise ValueError("stable agent ID collision or spoofing detected")
        if current.agent.version == entry.agent.version:
            raise ValueError("version operation requires a different version")
        event = RegistryEvent(
            kind=kind,
            agent_id=entry.agent.agent_id,
            occurred_at=entry.observed_at,
            from_version=current.agent.version,
            to_version=entry.agent.version,
            reason=reason,
            stable_identity=entry.stable_identity,
        )
        return self._replace(entry, event)

    def _replace(self, entry: RegistryEntry, event: RegistryEvent) -> "AgentRegistry":
        entries = tuple(
            entry if item.agent.agent_id == entry.agent.agent_id else item
            for item in self.entries
        )
        return AgentRegistry(entries=entries, history=(*self.history, event))

    def _optional(self, agent_id: str) -> Optional[RegistryEntry]:
        try:
            return self.get(agent_id)
        except KeyError:
            return None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "entries": [entry.to_dict() for entry in self.entries],
            "history": [event.to_dict() for event in self.history],
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "AgentRegistry":
        if set(value) != {"entries", "history"}:
            raise ValueError("agent registry fields do not match the schema")
        entries = value["entries"]
        history = value["history"]
        if not isinstance(entries, list) or not isinstance(history, list):
            raise ValueError("agent registry entries and history must be lists")
        return cls(
            entries=tuple(RegistryEntry.from_dict(item) for item in entries),
            history=tuple(RegistryEvent.from_dict(item) for item in history),
        )
