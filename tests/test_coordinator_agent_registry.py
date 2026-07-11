from __future__ import annotations

from dataclasses import replace

import pytest

from memwiki.coordinator_agent_registry import (
    AgentRegistry,
    IntegrityStatus,
    RegistryEntry,
    RegistryEventKind,
    TrustStatus,
)
from memwiki.coordinator_agents import (
    AgentDescriptor,
    AgentProduct,
    AgentSurface,
    AgentVisibility,
    ExecutionMode,
    SurfaceKind,
)


def descriptor(
    *,
    agent_id: str = "personal/seo-decision-lab",
    version: str = "1.2.0",
    publisher: str = "personal",
    source: str = "plugin://seo-decision-lab@personal/",
    visibility: AgentVisibility = AgentVisibility.PERSONAL_PRIVATE,
) -> AgentDescriptor:
    return AgentDescriptor(
        agent_id=agent_id,
        display_name="SEO Decision Lab",
        product=AgentProduct.CODEX,
        visibility=visibility,
        publisher=publisher,
        version=version,
        source=source,
        surfaces=(
            AgentSurface(
                surface_id="seo-decision-lab/mcp",
                kind=SurfaceKind.MCP,
                operations=("keyword_research",),
                execution_mode=ExecutionMode.CALLABLE,
            ),
        ),
    )


def test_registers_stable_identity_and_metadata_only_provenance() -> None:
    registry = AgentRegistry.empty()
    entry = RegistryEntry.create(
        descriptor(),
        marketplace="personal",
        integrity=IntegrityStatus.VERIFIED,
        trust=TrustStatus.TRUSTED,
        observed_at="2026-07-11T16:00:00Z",
        artifact_digest="sha256:" + "a" * 64,
        metadata={"installed": True},
    )

    updated = registry.register(entry)

    assert updated.get(entry.agent.agent_id) == entry
    assert entry.classification == "personalized_private"
    assert entry.stable_identity.startswith("sha256:")
    assert updated.history[0].kind is RegistryEventKind.REGISTERED
    assert AgentRegistry.from_dict(updated.to_dict()) == updated


@pytest.mark.parametrize("key", ["api_key", "password", "authorization", "refresh-token"])
def test_rejects_credentials_recursively(key: str) -> None:
    with pytest.raises(ValueError, match="credential"):
        RegistryEntry.create(
            descriptor(),
            marketplace="personal",
            integrity=IntegrityStatus.UNVERIFIED,
            trust=TrustStatus.UNTRUSTED,
            observed_at="2026-07-11T16:00:00Z",
            metadata={"nested": {key: "do-not-store"}},
        )


def test_public_agent_is_classified_but_not_implicitly_trusted() -> None:
    public = descriptor(
        agent_id="public/seo-agent",
        source="https://chatgpt.com/g/g-seo-agent",
        publisher="example-publisher",
        visibility=AgentVisibility.PUBLIC,
    )
    entry = RegistryEntry.create(
        public,
        marketplace="chatgpt-public",
        integrity=IntegrityStatus.UNVERIFIED,
        trust=TrustStatus.UNTRUSTED,
        observed_at="2026-07-11T16:00:00Z",
    )

    assert entry.classification == "public"
    assert entry.trust is TrustStatus.UNTRUSTED


def test_rejects_stable_id_collision_and_source_spoofing() -> None:
    original = RegistryEntry.create(
        descriptor(),
        marketplace="personal",
        integrity=IntegrityStatus.VERIFIED,
        trust=TrustStatus.TRUSTED,
        observed_at="2026-07-11T16:00:00Z",
    )
    registry = AgentRegistry.empty().register(original)

    for spoof in (
        descriptor(publisher="attacker"),
        descriptor(source="plugin://seo-decision-lab@other/"),
    ):
        with pytest.raises(ValueError, match="collision or spoofing"):
            registry.register(
                RegistryEntry.create(
                    spoof,
                    marketplace="personal",
                    integrity=IntegrityStatus.UNVERIFIED,
                    trust=TrustStatus.UNTRUSTED,
                    observed_at="2026-07-11T16:01:00Z",
                )
            )


def test_rejects_same_provenance_claimed_by_another_stable_id() -> None:
    first = RegistryEntry.create(
        descriptor(),
        marketplace="personal",
        integrity=IntegrityStatus.VERIFIED,
        trust=TrustStatus.TRUSTED,
        observed_at="2026-07-11T16:00:00Z",
    )
    second = RegistryEntry.create(
        descriptor(agent_id="personal/lookalike"),
        marketplace="personal",
        integrity=IntegrityStatus.UNVERIFIED,
        trust=TrustStatus.UNTRUSTED,
        observed_at="2026-07-11T16:01:00Z",
    )

    with pytest.raises(ValueError, match="provenance collision"):
        AgentRegistry.empty().register(first).register(second)


def test_detects_drift_and_requires_explicit_upgrade() -> None:
    current = RegistryEntry.create(
        descriptor(),
        marketplace="personal",
        integrity=IntegrityStatus.VERIFIED,
        trust=TrustStatus.TRUSTED,
        observed_at="2026-07-11T16:00:00Z",
    )
    candidate = replace(
        current,
        agent=descriptor(version="1.3.0"),
        observed_at="2026-07-11T16:10:00Z",
        artifact_digest="sha256:" + "b" * 64,
    )
    registry = AgentRegistry.empty().register(current)

    drift = registry.detect_version_drift(candidate)
    assert drift is not None
    assert (drift.recorded_version, drift.observed_version) == ("1.2.0", "1.3.0")
    with pytest.raises(ValueError, match="explicit upgrade or rollback"):
        registry.register(candidate)

    upgraded = registry.upgrade(candidate, reason="qualified release")
    assert upgraded.get(current.agent.agent_id).agent.version == "1.3.0"
    assert upgraded.history[-1].kind is RegistryEventKind.UPGRADED


def test_records_rollback_and_trust_integrity_changes() -> None:
    initial = RegistryEntry.create(
        descriptor(),
        marketplace="personal",
        integrity=IntegrityStatus.VERIFIED,
        trust=TrustStatus.TRUSTED,
        observed_at="2026-07-11T16:00:00Z",
    )
    newer = replace(
        initial,
        agent=descriptor(version="2.0.0"),
        observed_at="2026-07-11T16:10:00Z",
    )
    rollback = replace(initial, observed_at="2026-07-11T16:20:00Z")
    registry = AgentRegistry.empty().register(initial).upgrade(newer, reason="release")
    registry = registry.rollback(rollback, reason="regression")
    registry = registry.set_assurance(
        initial.agent.agent_id,
        integrity=IntegrityStatus.MISMATCH,
        trust=TrustStatus.BLOCKED,
        observed_at="2026-07-11T16:30:00Z",
        reason="digest mismatch",
    )

    assert registry.get(initial.agent.agent_id).trust is TrustStatus.BLOCKED
    assert [event.kind for event in registry.history[-2:]] == [
        RegistryEventKind.ROLLED_BACK,
        RegistryEventKind.ASSURANCE_CHANGED,
    ]
    assert AgentRegistry.from_dict(registry.to_dict()) == registry


def test_rejects_invalid_digest_timestamp_and_duplicate_registry_entries() -> None:
    with pytest.raises(ValueError, match="timestamp"):
        RegistryEntry.create(
            descriptor(),
            marketplace="personal",
            integrity=IntegrityStatus.UNVERIFIED,
            trust=TrustStatus.UNTRUSTED,
            observed_at="yesterday",
        )
    with pytest.raises(ValueError, match="digest"):
        RegistryEntry.create(
            descriptor(),
            marketplace="personal",
            integrity=IntegrityStatus.VERIFIED,
            trust=TrustStatus.TRUSTED,
            observed_at="2026-07-11T16:00:00Z",
            artifact_digest="abc",
        )

    entry = RegistryEntry.create(
        descriptor(),
        marketplace="personal",
        integrity=IntegrityStatus.UNVERIFIED,
        trust=TrustStatus.UNTRUSTED,
        observed_at="2026-07-11T16:00:00Z",
    )
    payload = AgentRegistry.empty().register(entry).to_dict()
    payload["entries"].append(payload["entries"][0])
    with pytest.raises(ValueError, match="duplicate agent"):
        AgentRegistry.from_dict(payload)
