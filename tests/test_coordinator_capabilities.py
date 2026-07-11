from __future__ import annotations

from dataclasses import replace

import pytest

from memwiki.coordinator_capabilities import (
    AuthRequirement,
    Availability,
    CapabilityDescriptor,
    CapabilityKind,
    CapabilityRegistry,
    CapabilityRequirement,
    CostClass,
    MutationScope,
    NetworkRequirement,
    PrivacyClass,
    ReplaySafety,
    SelectionStatus,
    SupervisionRequirement,
)


def descriptor(identifier: str, **changes: object) -> CapabilityDescriptor:
    base = CapabilityDescriptor(
        identifier=identifier,
        display_name=identifier,
        kind=CapabilityKind.SKILL,
        source="installed-tools",
        operations=("research",),
        availability=Availability.AVAILABLE,
        mutation_scope=MutationScope.READ_ONLY,
        auth=AuthRequirement.NONE,
        network=NetworkRequirement.NONE,
        cost=CostClass.FREE,
        privacy=PrivacyClass.LOCAL_ONLY,
        replay=ReplaySafety.SAFE,
        supervision=SupervisionRequirement.NONE,
        preference=100,
        metadata={"version": "1"},
    )
    return replace(base, **changes)


def test_descriptor_round_trips_normalized_metadata_without_credentials() -> None:
    value = descriptor("skill.web-search")
    assert CapabilityDescriptor.from_dict(value.to_dict()) == value
    assert value.to_dict()["kind"] == "skill"

    with pytest.raises(ValueError, match="credential"):
        descriptor("api.private", metadata={"api_key": "secret"})


@pytest.mark.parametrize(
    "kind",
    [
        CapabilityKind.PLUGIN,
        CapabilityKind.SKILL,
        CapabilityKind.MCP,
        CapabilityKind.API,
        CapabilityKind.BROWSER,
        CapabilityKind.IMAGE_TOOL,
        CapabilityKind.LOCAL_COMMAND,
        CapabilityKind.SERVICE,
        CapabilityKind.SPAWNED_AGENT,
    ],
)
def test_registry_normalizes_every_supported_provider_kind(kind: CapabilityKind) -> None:
    registry = CapabilityRegistry([descriptor(f"tool.{kind.value}", kind=kind)])
    assert registry.discover()[0].kind is kind


def test_discovery_is_metadata_only_and_never_invokes_capability() -> None:
    calls = 0

    def forbidden_invoker() -> None:
        nonlocal calls
        calls += 1

    registry = CapabilityRegistry([descriptor("skill.research")])
    assert registry.discover(operation="research")
    assert registry.select(CapabilityRequirement(operation="research")).selected
    assert calls == 0
    assert forbidden_invoker is not None  # Registry accepts no execution callback.


def test_expected_unavailable_blocks_only_the_dependent_requirement() -> None:
    registry = CapabilityRegistry(
        [
            descriptor(
                "mcp.expected",
                kind=CapabilityKind.MCP,
                availability=Availability.EXPECTED_UNAVAILABLE,
            ),
            descriptor("skill.other", operations=("format",)),
        ]
    )
    blocked = registry.select(CapabilityRequirement(operation="research"))
    unrelated = registry.select(CapabilityRequirement(operation="format"))

    assert blocked.status is SelectionStatus.BLOCKED
    assert blocked.blocking_capability_ids == ("mcp.expected",)
    assert unrelated.status is SelectionStatus.SELECTED


def test_nonexistent_resource_is_a_future_opportunity_not_a_blocker() -> None:
    registry = CapabilityRegistry(
        [descriptor("plugin.future", availability=Availability.DOES_NOT_EXIST)]
    )
    result = registry.select(CapabilityRequirement(operation="research"))
    assert result.status is SelectionStatus.FUTURE_OPPORTUNITY
    assert result.future_opportunity_ids == ("plugin.future",)
    assert not result.blocking_capability_ids


def test_selection_uses_explicit_deterministic_inputs_and_fallback_order() -> None:
    registry = CapabilityRegistry(
        [
            descriptor("skill.z", preference=20),
            descriptor("skill.a", preference=10),
            descriptor("skill.local", preference=10, kind=CapabilityKind.LOCAL_COMMAND),
        ]
    )
    result = registry.select(
        CapabilityRequirement(
            operation="research",
            allowed_kinds=(CapabilityKind.SKILL,),
            maximum_cost=CostClass.FREE,
            maximum_privacy=PrivacyClass.LOCAL_ONLY,
        )
    )
    assert result.status is SelectionStatus.SELECTED
    assert result.selected is not None
    assert result.selected.identifier == "skill.a"
    assert tuple(item.identifier for item in result.candidates) == ("skill.a", "skill.z")


def test_equal_rank_is_reported_as_ambiguous_without_hidden_selection() -> None:
    registry = CapabilityRegistry(
        [descriptor("skill.a", preference=10), descriptor("skill.b", preference=10)]
    )
    result = registry.select(CapabilityRequirement(operation="research"))
    assert result.status is SelectionStatus.AMBIGUOUS
    assert result.selected is None
    assert tuple(item.identifier for item in result.candidates) == ("skill.a", "skill.b")


def test_policy_metadata_filters_mutation_network_auth_privacy_and_supervision() -> None:
    remote = descriptor(
        "api.remote",
        kind=CapabilityKind.API,
        mutation_scope=MutationScope.EXTERNAL_WRITE,
        auth=AuthRequirement.REQUIRED,
        network=NetworkRequirement.REQUIRED,
        cost=CostClass.METERED,
        privacy=PrivacyClass.SENSITIVE_ALLOWED,
        replay=ReplaySafety.IDEMPOTENCY_REQUIRED,
        supervision=SupervisionRequirement.REQUIRED,
    )
    registry = CapabilityRegistry([remote])
    result = registry.select(
        CapabilityRequirement(
            operation="research",
            allow_mutation=False,
            allow_network=False,
            auth_available=False,
            maximum_cost=CostClass.FREE,
            maximum_privacy=PrivacyClass.LOCAL_ONLY,
            supervision_available=False,
        )
    )
    assert result.status is SelectionStatus.NO_MATCH
    assert result.rejections == {
        "api.remote": (
            "auth unavailable",
            "cost exceeds maximum",
            "mutation not allowed",
            "network not allowed",
            "privacy exceeds maximum",
            "supervision unavailable",
        )
    }


def test_duplicate_ids_and_invalid_descriptors_are_rejected() -> None:
    with pytest.raises(ValueError, match="duplicate capability"):
        CapabilityRegistry([descriptor("skill.same"), descriptor("skill.same")])
    with pytest.raises(ValueError, match="operations"):
        descriptor("skill.empty", operations=())
    with pytest.raises(ValueError, match="identifier"):
        descriptor("bad id")
