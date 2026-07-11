from __future__ import annotations

import pytest

from memwiki.coordinator_agent_chatgpt import (
    ChatGPTCapabilityDiscoveryAdapter,
    ChatGPTDiscoveryState,
    DeterministicChatGPTHost,
    HostChatGPTAgent,
    HostChatGPTSurface,
)
from memwiki.coordinator_agents import (
    AgentProduct,
    AgentVisibility,
    ExecutionMode,
    SurfaceKind,
)


def fixtures() -> tuple[HostChatGPTAgent, ...]:
    return (
        HostChatGPTAgent(
            agent_id="openai.web-research",
            display_name="Web Research",
            visibility="public",
            publisher="openai",
            version="2.1",
            source="chatgpt-agent-directory",
            surfaces=(
                HostChatGPTSurface(
                    "action.search",
                    "action",
                    ("search",),
                    "callable",
                    "ready",
                ),
            ),
        ),
        HostChatGPTAgent(
            agent_id="personal.seo-decision-lab",
            display_name="SEO Decision Lab",
            visibility="personal_private",
            publisher="personal",
            version="1.0",
            source="plugin://seo-decision-lab@personal/",
            surfaces=(
                HostChatGPTSurface(
                    "connector.dataforseo",
                    "connector",
                    ("keyword_research",),
                    "callable",
                    "unauthorized",
                    {"authorization_type": "host_managed"},
                ),
                HostChatGPTSurface(
                    "handoff.seo",
                    "conversational_handoff",
                    ("analyze_seo",),
                    "handoff_only",
                    "ready",
                ),
            ),
        ),
        HostChatGPTAgent(
            agent_id="organization.knowledge",
            display_name="Knowledge",
            visibility="organization_private",
            publisher="example-org",
            version="unknown",
            source="chatgpt-workspace",
            surfaces=(
                HostChatGPTSurface("app.knowledge", "app", ("lookup",), "callable", "unavailable"),
                HostChatGPTSurface("mcp.archive", "remote_mcp", ("read",), "callable", "unsupported"),
            ),
        ),
    )


def test_discovers_apps_connectors_actions_mcp_and_public_and_private_agents() -> None:
    host = DeterministicChatGPTHost(fixtures())
    discovered = ChatGPTCapabilityDiscoveryAdapter(host).discover()

    assert host.discovery_count == 1
    assert [agent.agent_id for agent in discovered] == [
        "openai.web-research",
        "organization.knowledge",
        "personal.seo-decision-lab",
    ]
    assert all(agent.product is AgentProduct.CHATGPT for agent in discovered)
    assert discovered[0].visibility is AgentVisibility.PUBLIC
    assert discovered[2].visibility is AgentVisibility.PERSONAL_PRIVATE
    kinds = {surface.kind for agent in discovered for surface in agent.surfaces}
    assert kinds == {
        SurfaceKind.ACTION,
        SurfaceKind.APP,
        SurfaceKind.CONNECTOR,
        SurfaceKind.MCP,
        SurfaceKind.CONVERSATIONAL_HANDOFF,
    }


def test_ready_callable_surface_remains_callable_without_being_invoked() -> None:
    host = DeterministicChatGPTHost(fixtures())
    agent = ChatGPTCapabilityDiscoveryAdapter(host).discover()[0]

    assert agent.surfaces[0].execution_mode is ExecutionMode.CALLABLE
    assert agent.callable_operations == ("search",)
    assert host.invocation_count == 0


@pytest.mark.parametrize(
    ("surface_id", "state"),
    [
        ("connector.dataforseo", ChatGPTDiscoveryState.UNAUTHORIZED),
        ("app.knowledge", ChatGPTDiscoveryState.UNAVAILABLE),
        ("mcp.archive", ChatGPTDiscoveryState.UNSUPPORTED),
    ],
)
def test_nonready_surfaces_are_explicit_metadata_not_fabricated_callable(
    surface_id: str, state: ChatGPTDiscoveryState
) -> None:
    discovered = ChatGPTCapabilityDiscoveryAdapter(DeterministicChatGPTHost(fixtures())).discover()
    surface = next(
        surface
        for agent in discovered
        for surface in agent.surfaces
        if surface.surface_id == surface_id
    )

    assert surface.execution_mode is ExecutionMode.METADATA_ONLY
    assert surface.extension_metadata["discovery_state"] == state.value
    assert surface.extension_metadata["declared_execution_mode"] == "callable"


def test_conversational_handoff_remains_typed_and_is_never_callable() -> None:
    agent = ChatGPTCapabilityDiscoveryAdapter(DeterministicChatGPTHost(fixtures())).discover()[2]
    handoff = next(surface for surface in agent.surfaces if surface.surface_id == "handoff.seo")

    assert handoff.kind is SurfaceKind.CONVERSATIONAL_HANDOFF
    assert handoff.execution_mode is ExecutionMode.HANDOFF_ONLY
    assert agent.handoff_operations == ("analyze_seo",)
    assert "analyze_seo" not in agent.callable_operations


def test_programmatic_discovery_unavailable_is_an_explicit_host_limitation() -> None:
    host = DeterministicChatGPTHost(
        (),
        discovery_state=ChatGPTDiscoveryState.UNSUPPORTED,
        limitation="This ChatGPT host exposes conversational discovery only.",
    )
    adapter = ChatGPTCapabilityDiscoveryAdapter(host)

    assert adapter.discover() == ()
    limitation = adapter.last_limitation
    assert limitation is not None
    assert limitation.state is ChatGPTDiscoveryState.UNSUPPORTED
    assert limitation.message == "This ChatGPT host exposes conversational discovery only."


def test_duplicate_agent_and_surface_identities_are_rejected() -> None:
    duplicate_agent = (fixtures()[0], fixtures()[0])
    with pytest.raises(ValueError, match="duplicate ChatGPT agent"):
        ChatGPTCapabilityDiscoveryAdapter(DeterministicChatGPTHost(duplicate_agent)).discover()

    agent = fixtures()[0]
    duplicate_surface = HostChatGPTAgent(
        agent.agent_id,
        agent.display_name,
        agent.visibility,
        agent.publisher,
        agent.version,
        agent.source,
        (agent.surfaces[0], agent.surfaces[0]),
    )
    with pytest.raises(ValueError, match="duplicate ChatGPT surface"):
        ChatGPTCapabilityDiscoveryAdapter(DeterministicChatGPTHost((duplicate_surface,))).discover()


def test_host_data_never_accepts_credentials() -> None:
    with pytest.raises(ValueError, match="credential"):
        HostChatGPTSurface(
            "action.bad", "action", ("run",), "callable", "ready", {"api_key": "no"}
        )
    with pytest.raises(ValueError, match="credential"):
        HostChatGPTAgent(
            "personal.bad",
            "Bad",
            "personal_private",
            "personal",
            "1",
            "chatgpt",
            (HostChatGPTSurface("app.bad", "app", ("read",), "metadata_only", "ready"),),
            {"token": "no"},
        )


def test_fake_host_and_discovery_are_deterministic() -> None:
    host = DeterministicChatGPTHost(tuple(reversed(fixtures())))
    adapter = ChatGPTCapabilityDiscoveryAdapter(host)

    first = tuple(agent.to_dict() for agent in adapter.discover())
    second = tuple(agent.to_dict() for agent in adapter.discover())
    assert first == second


def test_invalid_host_values_are_rejected_instead_of_guessed() -> None:
    with pytest.raises(ValueError, match="surface kind"):
        HostChatGPTSurface("unknown.x", "unknown", ("run",), "callable", "ready")
    with pytest.raises(ValueError, match="execution mode"):
        HostChatGPTSurface("app.x", "app", ("run",), "magic", "ready")
    with pytest.raises(ValueError, match="discovery state"):
        HostChatGPTSurface("app.x", "app", ("run",), "callable", "maybe")
