from dataclasses import replace

import pytest

from memwiki.coordinator_agents import (
    AgentDescriptor,
    AgentProduct,
    AgentSurface,
    AgentVisibility,
    ExecutionMode,
    SurfaceKind,
)


@pytest.mark.parametrize("product", list(AgentProduct))
@pytest.mark.parametrize("visibility", list(AgentVisibility))
def test_agent_descriptor_round_trips_product_and_visibility(
    product: AgentProduct, visibility: AgentVisibility
) -> None:
    agent = AgentDescriptor(
        agent_id=f"{product.value}.research-agent",
        display_name="Research Agent",
        product=product,
        visibility=visibility,
        publisher="verified-publisher",
        version="1.2.3",
        source="marketplace:test",
        surfaces=(
            AgentSurface(
                surface_id="research.lookup",
                kind=SurfaceKind.MCP,
                operations=("lookup",),
                execution_mode=ExecutionMode.CALLABLE,
                extension_metadata={"transport": "stdio"},
            ),
        ),
        extension_metadata={"product_label": product.value},
    )
    assert AgentDescriptor.from_dict(agent.to_dict()) == agent


def test_handoff_only_chatgpt_agent_is_not_misrepresented_as_callable() -> None:
    agent = AgentDescriptor(
        agent_id="chatgpt.public.reviewer",
        display_name="Public Reviewer",
        product=AgentProduct.CHATGPT,
        visibility=AgentVisibility.PUBLIC,
        publisher="example",
        version="unknown",
        source="chatgpt-agent",
        surfaces=(
            AgentSurface(
                surface_id="review.handoff",
                kind=SurfaceKind.CONVERSATIONAL_HANDOFF,
                operations=("review",),
                execution_mode=ExecutionMode.HANDOFF_ONLY,
            ),
        ),
    )
    assert agent.callable_operations == ()
    assert agent.handoff_operations == ("review",)


def test_bounded_extensions_preserve_unknown_product_metadata() -> None:
    agent = AgentDescriptor(
        agent_id="other.private.agent",
        display_name="Private Agent",
        product=AgentProduct.OTHER,
        visibility=AgentVisibility.PERSONAL_PRIVATE,
        publisher="local",
        version="dev",
        source="local",
        surfaces=(
            AgentSurface(
                surface_id="custom.action",
                kind=SurfaceKind.ACTION,
                operations=("act",),
                execution_mode=ExecutionMode.CALLABLE,
                extension_metadata={"vendor_mode": "bounded"},
            ),
        ),
        extension_metadata={"vendor": {"family": "custom"}},
    )
    assert AgentDescriptor.from_dict(agent.to_dict()).extension_metadata == agent.extension_metadata


@pytest.mark.parametrize(
    "metadata",
    [{"api_key": "bad"}, {"nested": {"password": "bad"}}, {"authorization": "bad"}],
)
def test_agent_and_surface_metadata_reject_credentials(metadata: dict[str, object]) -> None:
    with pytest.raises(ValueError, match="credential"):
        AgentSurface(
            surface_id="bad.surface",
            kind=SurfaceKind.API,
            operations=("call",),
            execution_mode=ExecutionMode.CALLABLE,
            extension_metadata=metadata,
        )


def test_duplicate_surfaces_operations_and_invalid_identity_are_rejected() -> None:
    surface = AgentSurface(
        surface_id="skill.one",
        kind=SurfaceKind.SKILL,
        operations=("research",),
        execution_mode=ExecutionMode.CALLABLE,
    )
    with pytest.raises(ValueError, match="duplicate surface"):
        AgentDescriptor(
            agent_id="codex.agent",
            display_name="Agent",
            product=AgentProduct.CODEX,
            visibility=AgentVisibility.PERSONAL_PRIVATE,
            publisher="local",
            version="1",
            source="plugin",
            surfaces=(surface, surface),
        )
    with pytest.raises(ValueError, match="operations"):
        replace(surface, operations=("research", "research"))
    with pytest.raises(ValueError, match="identifier"):
        replace(surface, surface_id="bad surface")
