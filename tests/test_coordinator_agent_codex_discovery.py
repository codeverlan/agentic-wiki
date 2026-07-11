from __future__ import annotations

import pytest

from memwiki.coordinator_agent_codex_discovery import (
    CodexAgentDiscoveryAdapter,
    CodexAgentManifest,
    CodexSurfaceManifest,
    DiscoveryAvailability,
)
from memwiki.coordinator_agent_registry import AgentRegistry, IntegrityStatus, RegistryEntry, TrustStatus
from memwiki.coordinator_agents import AgentVisibility, ExecutionMode, SurfaceKind

NOW = "2026-07-11T17:00:00Z"


def seo_manifest(**changes: object) -> CodexAgentManifest:
    values: dict[str, object] = {
        "agent_id": "personal/seo-decision-lab",
        "display_name": "SEO Decision Lab",
        "publisher": "personal",
        "version": "1.4.0",
        "source": "plugin://seo-decision-lab@personal/",
        "marketplace": "personal",
        "visibility": AgentVisibility.PERSONAL_PRIVATE,
        "availability": DiscoveryAvailability.INSTALLED,
        "surfaces": (
            CodexSurfaceManifest("seo-decision-lab/plugin", SurfaceKind.PLUGIN, ("inspect",)),
            CodexSurfaceManifest("seo-decision-lab/skill", SurfaceKind.SKILL, ("analyze",)),
            CodexSurfaceManifest("seo-decision-lab/mcp", SurfaceKind.MCP, ("query",)),
        ),
        "provenance": {"distribution": "personal-marketplace"},
    }
    values.update(changes)
    return CodexAgentManifest(**values)  # type: ignore[arg-type]


def test_discovers_exact_private_plugin_reference_without_invocation() -> None:
    calls = 0

    def inventory() -> tuple[CodexAgentManifest, ...]:
        nonlocal calls
        calls += 1
        return (seo_manifest(),)

    result = CodexAgentDiscoveryAdapter(inventory).discover(observed_at=NOW)

    assert calls == 1
    assert len(result.entries) == 1
    entry = result.entries[0]
    assert entry.agent.source == "plugin://seo-decision-lab@personal/"
    assert entry.agent.visibility is AgentVisibility.PERSONAL_PRIVATE
    assert entry.classification == "personalized_private"
    assert entry.metadata["availability"] == "installed"
    assert {surface.kind for surface in entry.agent.surfaces} == {
        SurfaceKind.PLUGIN,
        SurfaceKind.SKILL,
        SurfaceKind.MCP,
    }
    assert result.version_drifts == ()


def test_maps_every_codex_agent_surface_and_supervision_metadata() -> None:
    manifest = CodexAgentManifest(
        agent_id="codex/local-runtime",
        display_name="Codex local runtime",
        publisher="openai",
        version="2026.7",
        source="codex://local-runtime",
        marketplace="codex-host",
        visibility=AgentVisibility.PROJECT_LOCAL,
        availability=DiscoveryAvailability.INSTALLED,
        surfaces=tuple(
            CodexSurfaceManifest(f"codex/{kind.value}", kind, ("use",))
            for kind in (
                SurfaceKind.SKILL,
                SurfaceKind.PLUGIN,
                SurfaceKind.MCP,
                SurfaceKind.APP,
                SurfaceKind.WORKER,
                SurfaceKind.BROWSER,
                SurfaceKind.IMAGE_TOOL,
                SurfaceKind.API,
                SurfaceKind.COMMAND,
                SurfaceKind.COMPUTER_USE,
            )
        ),
    )

    entry = CodexAgentDiscoveryAdapter.from_manifests((manifest,)).discover(observed_at=NOW).entries[0]
    by_kind = {surface.kind: surface for surface in entry.agent.surfaces}

    assert set(by_kind) == set(SurfaceKind) - {
        SurfaceKind.CONNECTOR,
        SurfaceKind.ACTION,
        SurfaceKind.CONVERSATIONAL_HANDOFF,
    }
    assert by_kind[SurfaceKind.COMPUTER_USE].extension_metadata["supervision"] == "required"
    assert by_kind[SurfaceKind.BROWSER].extension_metadata["supervision"] == "conditional"
    assert all(surface.execution_mode is ExecutionMode.CALLABLE for surface in by_kind.values())


@pytest.mark.parametrize(
    ("availability", "expected_mode"),
    [
        (DiscoveryAvailability.DISABLED, ExecutionMode.METADATA_ONLY),
        (DiscoveryAvailability.MISSING, ExecutionMode.METADATA_ONLY),
    ],
)
def test_disabled_and_missing_agents_are_metadata_only(
    availability: DiscoveryAvailability, expected_mode: ExecutionMode
) -> None:
    entry = (
        CodexAgentDiscoveryAdapter.from_manifests((seo_manifest(availability=availability),))
        .discover(observed_at=NOW)
        .entries[0]
    )

    assert entry.metadata["availability"] == availability.value
    assert {surface.execution_mode for surface in entry.agent.surfaces} == {expected_mode}
    assert entry.trust is TrustStatus.UNTRUSTED
    assert entry.integrity is IntegrityStatus.UNKNOWN


def test_detects_stale_installed_version_against_registry() -> None:
    old_entry = RegistryEntry.create(
        seo_manifest(version="1.3.0").descriptor(),
        marketplace="personal",
        integrity=IntegrityStatus.VERIFIED,
        trust=TrustStatus.TRUSTED,
        observed_at="2026-07-10T17:00:00Z",
    )
    registry = AgentRegistry.empty().register(old_entry)

    result = CodexAgentDiscoveryAdapter.from_manifests((seo_manifest(),)).discover(observed_at=NOW, registry=registry)

    assert len(result.version_drifts) == 1
    assert result.version_drifts[0].recorded_version == "1.3.0"
    assert result.version_drifts[0].observed_version == "1.4.0"
    assert result.entries[0].metadata["version_status"] == "stale_registry"


def test_discovery_is_metadata_only_and_rejects_credentials() -> None:
    with pytest.raises(ValueError, match="credential"):
        seo_manifest(provenance={"nested": {"api_key": "must-not-read"}})

    adapter = CodexAgentDiscoveryAdapter.from_manifests((seo_manifest(),))
    first = adapter.discover(observed_at=NOW)
    second = adapter.discover(observed_at=NOW)
    assert first.to_dict() == second.to_dict()


def test_rejects_duplicate_sources_and_invalid_plugin_reference() -> None:
    with pytest.raises(ValueError, match="exact plugin reference"):
        seo_manifest(source="plugin://seo-decision-lab")

    duplicate = seo_manifest(agent_id="personal/lookalike")
    with pytest.raises(ValueError, match="duplicate Codex provenance"):
        CodexAgentDiscoveryAdapter.from_manifests((seo_manifest(), duplicate)).discover(observed_at=NOW)
