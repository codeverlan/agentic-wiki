from __future__ import annotations

import hashlib
import json

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
from memwiki.coordinator_agent_registry import IntegrityStatus, RegistryEntry, TrustStatus
from memwiki.coordinator_agent_selection import (
    AgentCandidate,
    AgentFailure,
    ProjectAgentPreferences,
    SelectionStatus,
    plan_agent_selection,
    record_agent_failure,
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


def _entry(
    agent_id: str,
    *,
    visibility: AgentVisibility,
    source: str,
    cost: CostClass = CostClass.METERED,
    latency: LatencyClass = LatencyClass.INTERACTIVE,
) -> tuple[RegistryEntry, AgentPolicy]:
    descriptor = AgentDescriptor(
        agent_id=agent_id,
        display_name=agent_id,
        product=AgentProduct.CODEX,
        visibility=visibility,
        publisher="personal" if visibility is AgentVisibility.PERSONAL_PRIVATE else "public",
        version="1.0.0",
        source=source,
        surfaces=(
            AgentSurface(
                surface_id=f"{agent_id}/dataforseo",
                kind=SurfaceKind.MCP,
                operations=("dataforseo_keyword_research",),
                execution_mode=ExecutionMode.CALLABLE,
            ),
        ),
    )
    entry = RegistryEntry.create(
        descriptor,
        marketplace="personal" if visibility is AgentVisibility.PERSONAL_PRIVATE else "public",
        integrity=IntegrityStatus.VERIFIED,
        trust=TrustStatus.TRUSTED,
        observed_at="2026-07-11T16:00:00Z",
    )
    policy = AgentPolicy(
        input_schemas=("schema:seo-request:v1",),
        output_schemas=("schema:seo-result:v1",),
        authority=("invoke_external_read",),
        auth=AuthAvailability.REFERENCE_AVAILABLE,
        data_residency=DataPolicy.LOCAL,
        retention=DataPolicy.NONE,
        telemetry=DataPolicy.NONE,
        cost=cost,
        latency=latency,
        supervision=SupervisionRequirement.CONDITIONAL,
        reversible=PolicyValue.YES,
        replayable=PolicyValue.YES,
        phi_eligible=PolicyValue.NO,
    )
    return entry, policy


def _requirement() -> AgentRequirement:
    return AgentRequirement(
        operation="dataforseo_keyword_research",
        accepted_input_schemas=("schema:seo-request:v1",),
        accepted_output_schemas=("schema:seo-result:v1",),
        allowed_authority=("invoke_external_read",),
        auth_reference_available=True,
        allowed_residency=(DataPolicy.LOCAL,),
        allowed_retention=(DataPolicy.NONE,),
        allowed_telemetry=(DataPolicy.NONE,),
        maximum_cost=CostClass.HIGH,
        maximum_latency=LatencyClass.LONG_RUNNING,
        supervision_available=True,
        require_reversible=True,
        require_replayable=True,
        contains_phi=False,
        allow_handoff=False,
    )


def _candidate(
    agent_id: str,
    visibility: AgentVisibility,
    source: str,
    *,
    cost: CostClass = CostClass.METERED,
    latency: LatencyClass = LatencyClass.INTERACTIVE,
) -> AgentCandidate:
    entry, policy = _entry(
        agent_id, visibility=visibility, source=source, cost=cost, latency=latency
    )
    return AgentCandidate(entry=entry, qualification=qualify_agent(entry, policy, _requirement()))


def test_prefers_seo_decision_lab_private_agent_for_dataforseo() -> None:
    private = _candidate(
        "personal/seo-decision-lab",
        AgentVisibility.PERSONAL_PRIVATE,
        "plugin://seo-decision-lab@personal/",
    )
    public = _candidate(
        "public/seo-agent", AgentVisibility.PUBLIC, "agent://public/seo-agent"
    )
    preferences = ProjectAgentPreferences(
        preferred_agent_ids={"dataforseo_keyword_research": ("personal/seo-decision-lab",)},
        prefer_private_agents=True,
        allow_public_fallback=True,
        maximum_fallbacks=3,
    )

    plan = plan_agent_selection("slice-seo", _requirement(), (public, private), preferences)

    assert plan.status is SelectionStatus.SELECTED
    assert plan.selected_agent_id == "personal/seo-decision-lab"
    assert [item.agent_id for item in plan.fallback_chain] == ["public/seo-agent"]
    assert "project preference rank 0" in plan.explanations[0]
    assert type(plan).from_dict(plan.to_dict()) == plan


def test_falls_back_to_qualified_public_agent_after_private_failure() -> None:
    private = _candidate(
        "personal/seo-decision-lab",
        AgentVisibility.PERSONAL_PRIVATE,
        "plugin://seo-decision-lab@personal/",
    )
    public = _candidate(
        "public/seo-agent", AgentVisibility.PUBLIC, "agent://public/seo-agent"
    )
    plan = plan_agent_selection(
        "slice-seo",
        _requirement(),
        (public, private),
        ProjectAgentPreferences(prefer_private_agents=True, allow_public_fallback=True),
    )

    updated = record_agent_failure(
        plan,
        AgentFailure(
            agent_id="personal/seo-decision-lab",
            failure_kind="transient_unavailable",
            evidence_id="sha256:" + "a" * 64,
        ),
    )

    assert updated.status is SelectionStatus.SELECTED
    assert updated.selected_agent_id == "public/seo-agent"
    assert updated.failed_agent_ids == ("personal/seo-decision-lab",)


def test_unqualified_alternative_is_never_added_to_fallback_chain() -> None:
    private = _candidate(
        "personal/seo-decision-lab",
        AgentVisibility.PERSONAL_PRIVATE,
        "plugin://seo-decision-lab@personal/",
    )
    public = _candidate(
        "public/seo-agent", AgentVisibility.PUBLIC, "agent://public/seo-agent"
    )
    payload = public.qualification.to_dict()
    payload["status"] = QualificationStatus.REJECTED.value
    payload["execution_mode"] = None
    payload["rejection_reasons"] = ["policy denied"]
    content = {key: value for key, value in payload.items() if key != "receipt_id"}
    payload["receipt_id"] = "sha256:" + hashlib.sha256(
        json.dumps(content, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    rejected_public = AgentCandidate(
        entry=public.entry,
        qualification=type(public.qualification).from_dict(payload),
    )

    plan = plan_agent_selection(
        "slice-seo",
        _requirement(),
        (private, rejected_public),
        ProjectAgentPreferences(prefer_private_agents=True, allow_public_fallback=True),
    )

    assert plan.selected_agent_id == "personal/seo-decision-lab"
    assert plan.fallback_chain == ()
    assert plan.excluded_agents["public/seo-agent"] == ("qualification rejected",)


def test_no_qualified_agent_blocks_only_the_dependent_slice() -> None:
    public = _candidate(
        "public/seo-agent", AgentVisibility.PUBLIC, "agent://public/seo-agent"
    )
    plan = plan_agent_selection(
        "slice-seo",
        _requirement(),
        (public,),
        ProjectAgentPreferences(allow_public_fallback=False),
    )

    assert plan.status is SelectionStatus.BLOCKED
    assert plan.blocked_slice_id == "slice-seo"
    assert plan.blocker_scope == "slice"
    assert plan.selected_agent_id is None


def test_failure_does_not_change_independent_slice_plan() -> None:
    private = _candidate(
        "personal/seo-decision-lab",
        AgentVisibility.PERSONAL_PRIVATE,
        "plugin://seo-decision-lab@personal/",
    )
    preferences = ProjectAgentPreferences(prefer_private_agents=True)
    seo = plan_agent_selection("slice-seo", _requirement(), (private,), preferences)
    independent = plan_agent_selection("slice-docs", _requirement(), (private,), preferences)

    blocked = record_agent_failure(
        seo,
        AgentFailure(
            agent_id="personal/seo-decision-lab",
            failure_kind="terminal",
            evidence_id="sha256:" + "b" * 64,
        ),
    )

    assert blocked.status is SelectionStatus.BLOCKED
    assert blocked.blocked_slice_id == "slice-seo"
    assert independent.status is SelectionStatus.SELECTED
    assert independent.blocked_slice_id is None
