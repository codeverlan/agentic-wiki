import json
from typing import Callable

import pytest

from memwiki.coordinator_model_routing import (
    CapabilityTier,
    HostCapabilityProfile,
    ModelCapability,
    ModelRoutingPolicy,
    ModelRoutingRequirement,
    ReasoningEffort,
    route_model,
)


def _profile() -> HostCapabilityProfile:
    return HostCapabilityProfile(
        host_id="local-host",
        models=(
            ModelCapability("luna", CapabilityTier.NARROW, (ReasoningEffort.LOW,)),
            ModelCapability("terra", CapabilityTier.IMPLEMENTATION, (ReasoningEffort.LOW, ReasoningEffort.MEDIUM)),
            ModelCapability("sol", CapabilityTier.FRONTIER, (ReasoningEffort.MEDIUM, ReasoningEffort.HIGH)),
        ),
    )


def _policy() -> ModelRoutingPolicy:
    return ModelRoutingPolicy(
        preferred_by_tier={
            CapabilityTier.NARROW: ("gpt-5.6-luna", ReasoningEffort.LOW),
            CapabilityTier.IMPLEMENTATION: ("gpt-5.6-terra", ReasoningEffort.MEDIUM),
            CapabilityTier.FRONTIER: ("gpt-5.6-sol", ReasoningEffort.HIGH),
        },
        qualified_failure_escalation=2,
    )


def test_routes_provider_neutral_requirement_to_lowest_safe_tier() -> None:
    decision = route_model(
        ModelRoutingRequirement(required_tier=CapabilityTier.NARROW, required_reasoning=ReasoningEffort.LOW),
        _profile(),
        _policy(),
    )

    assert decision.selected_model_id == "luna"
    assert decision.selected_tier is CapabilityTier.NARROW
    assert decision.selected_reasoning is ReasoningEffort.LOW
    assert decision.used_fallback is True


def test_prefers_available_project_policy_model_and_reasoning() -> None:
    profile = HostCapabilityProfile(
        host_id="local-host",
        models=(
            ModelCapability(
                "gpt-5.6-terra", CapabilityTier.IMPLEMENTATION, (ReasoningEffort.LOW, ReasoningEffort.MEDIUM)
            ),
        ),
    )

    decision = route_model(
        ModelRoutingRequirement(required_tier=CapabilityTier.IMPLEMENTATION, required_reasoning=ReasoningEffort.LOW),
        profile,
        _policy(),
    )

    assert decision.selected_model_id == "gpt-5.6-terra"
    assert decision.selected_reasoning is ReasoningEffort.MEDIUM
    assert decision.used_fallback is False


def test_critical_risk_never_downgrades_below_frontier() -> None:
    decision = route_model(
        ModelRoutingRequirement(
            required_tier=CapabilityTier.NARROW,
            required_reasoning=ReasoningEffort.LOW,
            critical_risk=True,
        ),
        _profile(),
        _policy(),
    )

    assert decision.selected_model_id == "sol"
    assert decision.selected_tier is CapabilityTier.FRONTIER


def test_deterministically_selects_compatible_host_model_when_preferred_is_unavailable() -> None:
    profile = HostCapabilityProfile(
        host_id="local-host",
        models=(
            ModelCapability("zeta", CapabilityTier.IMPLEMENTATION, (ReasoningEffort.MEDIUM,)),
            ModelCapability("alpha", CapabilityTier.IMPLEMENTATION, (ReasoningEffort.MEDIUM,)),
        ),
    )

    decision = route_model(
        ModelRoutingRequirement(required_tier=CapabilityTier.IMPLEMENTATION, required_reasoning=ReasoningEffort.MEDIUM),
        profile,
        _policy(),
    )

    assert decision.selected_model_id == "alpha"
    assert decision.used_fallback is True


def test_matches_provider_neutral_capability_requirements_and_policy_fallbacks() -> None:
    profile = HostCapabilityProfile(
        host_id="local-host",
        models=(
            ModelCapability(
                "luna",
                CapabilityTier.NARROW,
                (ReasoningEffort.LOW,),
                capabilities=("summarize",),
            ),
            ModelCapability(
                "terra-backup",
                CapabilityTier.IMPLEMENTATION,
                (ReasoningEffort.MEDIUM,),
                capabilities=("implement", "summarize"),
            ),
        ),
    )
    policy = ModelRoutingPolicy(
        fallback_by_tier={
            CapabilityTier.IMPLEMENTATION: (("terra-backup", ReasoningEffort.MEDIUM),),
        }
    )

    decision = route_model(
        ModelRoutingRequirement(
            required_tier=CapabilityTier.NARROW,
            required_reasoning=ReasoningEffort.LOW,
            required_capabilities=("implement",),
        ),
        profile,
        policy,
    )

    assert decision.selected_model_id == "terra-backup"
    assert decision.selected_reasoning is ReasoningEffort.MEDIUM
    assert decision.used_fallback is True


def test_repeated_qualified_failures_escalate_but_single_failure_does_not() -> None:
    requirement = ModelRoutingRequirement(
        required_tier=CapabilityTier.NARROW,
        required_reasoning=ReasoningEffort.LOW,
        qualified_failures=2,
    )

    decision = route_model(requirement, _profile(), _policy())

    assert decision.selected_tier is CapabilityTier.IMPLEMENTATION
    assert decision.escalated is True


def test_policy_and_decision_are_content_addressed_and_round_trip() -> None:
    policy = _policy()
    decision = route_model(
        ModelRoutingRequirement(required_tier=CapabilityTier.IMPLEMENTATION, required_reasoning=ReasoningEffort.LOW),
        _profile(),
        policy,
    )

    assert policy.policy_id.startswith("sha256:")
    assert decision.decision_id.startswith("sha256:")
    assert ModelRoutingPolicy.from_dict(json.loads(json.dumps(policy.to_dict()))) == policy
    assert type(decision).from_dict(json.loads(json.dumps(decision.to_dict()))) == decision


@pytest.mark.parametrize(
    ("factory", "message"),
    [
        (
            lambda: HostCapabilityProfile(
                host_id="local-host",
                models=(ModelCapability("sol", CapabilityTier.FRONTIER, (ReasoningEffort.HIGH,)),),
                metadata={"api_key": "not-a-secret"},
            ),
            "credentials or secrets",
        ),
        (
            lambda: ModelRoutingPolicy(
                preferred_by_tier={CapabilityTier.NARROW: ("luna", ReasoningEffort.LOW)},
                metadata={"authorization": "not-a-secret"},
            ),
            "credentials or secrets",
        ),
    ],
)
def test_rejects_credentials_or_secrets_in_serializable_metadata(factory: Callable[[], object], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        factory()
