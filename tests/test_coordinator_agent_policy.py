from __future__ import annotations

from dataclasses import replace

import pytest

from memwiki.coordinator_agent_policy import (
    AgentPolicy,
    AgentRequirement,
    AuthAvailability,
    DataPolicy,
    LatencyClass,
    PolicyValue,
    QualificationStatus,
    qualify_agent,
)
from memwiki.coordinator_agent_registry import (
    IntegrityStatus,
    RegistryEntry,
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
from memwiki.coordinator_capabilities import CostClass, SupervisionRequirement


def entry(*, mode: ExecutionMode = ExecutionMode.CALLABLE) -> RegistryEntry:
    descriptor = AgentDescriptor(
        agent_id="personal/seo-decision-lab",
        display_name="SEO Decision Lab",
        product=AgentProduct.CODEX,
        visibility=AgentVisibility.PERSONAL_PRIVATE,
        publisher="personal",
        version="1.0.0",
        source="plugin://seo-decision-lab@personal/",
        surfaces=(
            AgentSurface(
                surface_id="seo-decision-lab/mcp",
                kind=SurfaceKind.MCP,
                operations=("keyword_research",),
                execution_mode=mode,
            ),
        ),
    )
    return RegistryEntry.create(
        descriptor,
        marketplace="personal",
        integrity=IntegrityStatus.VERIFIED,
        trust=TrustStatus.TRUSTED,
        observed_at="2026-07-11T16:00:00Z",
    )


def policy() -> AgentPolicy:
    return AgentPolicy(
        input_schemas=("schema:keyword-request:v1",),
        output_schemas=("schema:keyword-result:v1",),
        authority=("read_project", "invoke_external_read"),
        auth=AuthAvailability.REFERENCE_AVAILABLE,
        data_residency=DataPolicy.LOCAL,
        retention=DataPolicy.NONE,
        telemetry=DataPolicy.NONE,
        cost=CostClass.METERED,
        latency=LatencyClass.INTERACTIVE,
        supervision=SupervisionRequirement.CONDITIONAL,
        reversible=PolicyValue.YES,
        replayable=PolicyValue.YES,
        phi_eligible=PolicyValue.NO,
    )


def requirement() -> AgentRequirement:
    return AgentRequirement(
        operation="keyword_research",
        accepted_input_schemas=("schema:keyword-request:v1",),
        accepted_output_schemas=("schema:keyword-result:v1",),
        allowed_authority=("read_project", "invoke_external_read"),
        auth_reference_available=True,
        allowed_residency=(DataPolicy.LOCAL,),
        allowed_retention=(DataPolicy.NONE,),
        allowed_telemetry=(DataPolicy.NONE,),
        maximum_cost=CostClass.METERED,
        maximum_latency=LatencyClass.BATCH,
        supervision_available=True,
        require_reversible=True,
        require_replayable=True,
        contains_phi=False,
        allow_handoff=True,
    )


def test_qualifies_with_deterministic_receipt_and_round_trip() -> None:
    first = qualify_agent(entry(), policy(), requirement())
    second = qualify_agent(entry(), policy(), requirement())

    assert first == second
    assert first.status is QualificationStatus.QUALIFIED
    assert first.execution_mode is ExecutionMode.CALLABLE
    assert first.input_schema == "schema:keyword-request:v1"
    assert first.output_schema == "schema:keyword-result:v1"
    assert first.receipt_id.startswith("sha256:")
    assert type(first).from_dict(first.to_dict()) == first
    assert "credential" not in str(first.to_dict()).lower()


def test_schema_negotiation_is_deterministic_not_declaration_order() -> None:
    negotiated = qualify_agent(
        entry(),
        replace(
            policy(),
            input_schemas=("schema:z:v1", "schema:a:v1"),
            output_schemas=("schema:y:v1", "schema:b:v1"),
        ),
        replace(
            requirement(),
            accepted_input_schemas=("schema:a:v1", "schema:z:v1"),
            accepted_output_schemas=("schema:b:v1", "schema:y:v1"),
        ),
    )

    assert negotiated.input_schema == "schema:a:v1"
    assert negotiated.output_schema == "schema:b:v1"


@pytest.mark.parametrize(
    ("changed_policy", "reason"),
    [
        ({"input_schemas": ("schema:other:v1",)}, "input schema incompatible"),
        ({"output_schemas": ("schema:other:v1",)}, "output schema incompatible"),
        ({"authority": ("delete_project",)}, "authority exceeds allowance"),
        ({"auth": AuthAvailability.REFERENCE_MISSING}, "auth reference unavailable"),
        ({"data_residency": DataPolicy.REMOTE}, "data residency not allowed"),
        ({"retention": DataPolicy.RETAINED}, "retention not allowed"),
        ({"telemetry": DataPolicy.ENABLED}, "telemetry not allowed"),
        ({"cost": CostClass.HIGH}, "cost exceeds maximum"),
        ({"latency": LatencyClass.LONG_RUNNING}, "latency exceeds maximum"),
        ({"supervision": SupervisionRequirement.REQUIRED}, "supervision unavailable"),
        ({"reversible": PolicyValue.NO}, "reversibility required"),
        ({"replayable": PolicyValue.NO}, "replay required"),
    ],
)
def test_rejects_incompatible_policy(changed_policy: dict[str, object], reason: str) -> None:
    constrained = replace(requirement(), supervision_available=False)
    receipt = qualify_agent(entry(), replace(policy(), **changed_policy), constrained)

    assert receipt.status is QualificationStatus.REJECTED
    assert reason in receipt.rejection_reasons
    assert receipt.execution_mode is None


@pytest.mark.parametrize(
    "field",
    ["data_residency", "retention", "telemetry", "reversible", "replayable", "phi_eligible"],
)
def test_unknown_sensitive_policy_defaults_to_deny(field: str) -> None:
    receipt = qualify_agent(entry(), replace(policy(), **{field: PolicyValue.UNKNOWN}), requirement())

    assert receipt.status is QualificationStatus.REJECTED
    assert f"{field.replace('_', ' ')} unknown" in receipt.rejection_reasons


def test_phi_requires_explicit_eligibility_local_residency_and_no_telemetry() -> None:
    needed = replace(requirement(), contains_phi=True)
    eligible = replace(policy(), phi_eligible=PolicyValue.YES)
    assert qualify_agent(entry(), eligible, needed).status is QualificationStatus.QUALIFIED

    for unsafe in (
        replace(eligible, phi_eligible=PolicyValue.NO),
        replace(eligible, data_residency=DataPolicy.REMOTE),
        replace(eligible, telemetry=DataPolicy.ENABLED),
    ):
        receipt = qualify_agent(entry(), unsafe, needed)
        assert receipt.status is QualificationStatus.REJECTED
        assert any(reason.startswith("PHI") for reason in receipt.rejection_reasons)


def test_callable_surface_can_downgrade_to_handoff_but_not_metadata_only() -> None:
    handoff_required = replace(requirement(), callable_authorized=False)
    downgraded = qualify_agent(entry(), policy(), handoff_required)
    assert downgraded.status is QualificationStatus.DOWNGRADED
    assert downgraded.execution_mode is ExecutionMode.HANDOFF_ONLY
    assert downgraded.downgrade_reasons == ("callable execution not authorized",)

    rejected = qualify_agent(entry(mode=ExecutionMode.METADATA_ONLY), policy(), requirement())
    assert rejected.status is QualificationStatus.REJECTED
    assert "surface is metadata-only" in rejected.rejection_reasons


def test_untrusted_or_unverified_registry_entry_is_rejected() -> None:
    base = entry()
    for unsafe in (
        replace(base, trust=TrustStatus.UNTRUSTED),
        replace(base, integrity=IntegrityStatus.UNKNOWN),
    ):
        receipt = qualify_agent(unsafe, policy(), requirement())
        assert receipt.status is QualificationStatus.REJECTED


def test_rejects_credential_values_and_invalid_receipt_hash() -> None:
    with pytest.raises(ValueError, match="reference only"):
        AgentPolicy.from_dict({**policy().to_dict(), "auth": "bearer real-secret"})

    receipt = qualify_agent(entry(), policy(), requirement())
    with pytest.raises(ValueError, match="receipt id"):
        type(receipt).from_dict({**receipt.to_dict(), "receipt_id": "sha256:" + "0" * 64})
