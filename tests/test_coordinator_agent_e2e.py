from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from memwiki.coordinator_agent_context import (
    AdmissionStatus,
    AdmissionTarget,
    AgentContextQualification,
    AgentOutput,
    ContextPackager,
    ContextRequest,
    ContextSource,
    MemoryAdmission,
)
from memwiki.coordinator_agent_harness import (
    AgentCompatibilityHarness,
    AgentHarnessAdapter,
    AgentScenario,
    DeterministicChatGPTHost,
    DeterministicCodexHost,
    FakeAgentReply,
    HarnessState,
)
from memwiki.coordinator_agent_policy import (
    AgentPolicy,
    AgentRequirement,
    AuthAvailability,
    DataPolicy,
    LatencyClass,
    PolicyValue,
    qualify_agent,
)
from memwiki.coordinator_agent_registry import IntegrityStatus, RegistryEntry, TrustStatus
from memwiki.coordinator_agent_selection import (
    AgentCandidate,
    AgentFailure,
    ProjectAgentPreferences,
    plan_agent_selection,
    record_agent_failure,
)
from memwiki.coordinator_agent_supervision import (
    AgentSupervisionGate,
    AgentSupervisionRequest,
    SupervisedAction,
)
from memwiki.coordinator_agent_views import render_agent_views, verify_agent_view
from memwiki.coordinator_agents import (
    AgentDescriptor,
    AgentProduct,
    AgentSurface,
    AgentVisibility,
    ExecutionMode,
    SurfaceKind,
)
from memwiki.coordinator_capabilities import CostClass, SupervisionRequirement
from memwiki.coordinator_seo_agent import DeterministicFakeSeoMcp, SeoDecisionLabAdapter, SeoRequest
from memwiki.coordinator_supervision import SupervisionQueue

FIXTURE = Path(__file__).parent / "fixtures/coordinator_agents/cross_product_runs.json"
NOW = datetime(2026, 7, 11, 20, 0, tzinfo=timezone.utc)
SECRET = "not-a-real-e2e-secret"


@dataclass(frozen=True)
class QueueResult:
    completed: tuple[str, ...]
    blocked: tuple[str, ...]
    pending: tuple[str, ...]


def _scenarios() -> tuple[AgentScenario, ...]:
    raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return tuple(AgentScenario.from_dict(item) for item in raw["scenarios"])


def _hosts() -> dict[str, AgentHarnessAdapter]:
    return {"codex": DeterministicCodexHost(), "chatgpt": DeterministicChatGPTHost()}


def _drain(records: list[tuple[str, str]]) -> QueueResult:
    completed = tuple(slice_id for slice_id, status in records if status == "succeeded")
    blocked = tuple(slice_id for slice_id, status in records if status == "blocked")
    pending = tuple(slice_id for slice_id, status in records if status == "pending")
    return QueueResult(completed, blocked, pending)


@pytest.mark.parametrize("scenario", _scenarios(), ids=lambda item: item.name)
def test_cross_product_runs_drain_callable_work_without_duplicate_effects(
    scenario: AgentScenario,
) -> None:
    result = AgentCompatibilityHarness(_hosts()).run(scenario)
    callable_records = [item for item in result.records if item.execution_mode == "callable"]

    assert result.conformant
    assert all(item.status == "succeeded" for item in callable_records)
    assert len(result.applied_effects) == len(set(result.applied_effects))
    assert len(result.applied_effects) == len(callable_records)
    assert all(item.status != "adapter_unavailable" for item in result.records)


def test_restart_is_idempotent_and_queue_drains_byte_equivalently() -> None:
    scenario = next(item for item in _scenarios() if item.name == "mixed-agent-run")
    uninterrupted = AgentCompatibilityHarness(_hosts()).run(scenario)
    partial = AgentCompatibilityHarness(_hosts()).run(scenario, max_invocations=1)
    resumed = AgentCompatibilityHarness(
        _hosts(),
        state=HarnessState.from_dict(partial.state.to_dict()),
    ).run(scenario)

    assert resumed.to_json() == uninterrupted.to_json()
    assert resumed.applied_effects == uninterrupted.applied_effects
    assert len(resumed.applied_effects) == len(set(resumed.applied_effects))
    assert len(resumed.records) == len(scenario.invocations)


def _seo_candidate(agent_id: str, visibility: AgentVisibility, source: str) -> AgentCandidate:
    descriptor = AgentDescriptor(
        agent_id=agent_id,
        display_name=agent_id,
        product=AgentProduct.CODEX,
        visibility=visibility,
        publisher="personal" if visibility is AgentVisibility.PERSONAL_PRIVATE else "public",
        version="0.1.0",
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
        observed_at="2026-07-11T20:00:00Z",
    )
    requirement = _seo_requirement()
    policy = AgentPolicy(
        input_schemas=requirement.accepted_input_schemas,
        output_schemas=requirement.accepted_output_schemas,
        authority=requirement.allowed_authority,
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
    return AgentCandidate(entry, qualify_agent(entry, policy, requirement))


def _seo_requirement(*, auth: bool = True) -> AgentRequirement:
    return AgentRequirement(
        operation="dataforseo_keyword_research",
        accepted_input_schemas=("schema:seo-request:v1",),
        accepted_output_schemas=("schema:seo-result:v1",),
        allowed_authority=("invoke_external_read",),
        auth_reference_available=auth,
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


def test_private_seo_lab_is_preferred_then_public_agent_is_safe_fallback() -> None:
    private = _seo_candidate(
        "personal/seo-decision-lab",
        AgentVisibility.PERSONAL_PRIVATE,
        "plugin://seo-decision-lab@personal/",
    )
    public = _seo_candidate(
        "public/seo-agent", AgentVisibility.PUBLIC, "agent://public/seo-agent"
    )
    plan = plan_agent_selection(
        "S-SEO",
        _seo_requirement(),
        (public, private),
        ProjectAgentPreferences(
            preferred_agent_ids={
                "dataforseo_keyword_research": ("personal/seo-decision-lab",)
            },
            prefer_private_agents=True,
            allow_public_fallback=True,
        ),
    )
    assert plan.selected_agent_id == "personal/seo-decision-lab"
    assert tuple(item.agent_id for item in plan.fallback_chain) == ("public/seo-agent",)

    fallback = record_agent_failure(
        plan,
        AgentFailure(
            "personal/seo-decision-lab", "authentication_unavailable", "sha256:" + "a" * 64
        ),
    )
    assert fallback.selected_agent_id == "public/seo-agent"
    assert fallback.blocked_slice_id is None


def test_missing_seo_auth_blocks_only_dependent_slice_and_independent_work_drains() -> None:
    seo = SeoDecisionLabAdapter().execute(
        SeoRequest("S-SEO", "keyword_ideas", "example.com", {"keywords": ["therapy"]}),
        DeterministicFakeSeoMcp(authenticated=False),
    )
    independent = AgentCompatibilityHarness({"codex": DeterministicCodexHost()}).run(
        AgentScenario.from_dict(
            {
                "name": "independent",
                "agents": [
                    {
                        "agent_id": "codex.public",
                        "adapter": "codex",
                        "visibility": "public",
                        "execution_mode": "callable",
                    }
                ],
                "invocations": [
                    {
                        "invocation_id": "independent-build",
                        "agent_id": "codex.public",
                        "operation": "build",
                        "payload": {"slice_id": "S-INDEPENDENT"},
                    }
                ],
            }
        )
    )
    queue = _drain(
        [("S-SEO", seo.status), ("S-INDEPENDENT", independent.records[0].status)]
    )

    assert seo.blocker_scope == "slice"
    assert queue.blocked == ("S-SEO",)
    assert queue.completed == ("S-INDEPENDENT",)
    assert queue.pending == ()
    assert len(independent.applied_effects) == 1


def test_context_is_admitted_with_agent_and_source_provenance() -> None:
    qualification = AgentContextQualification(
        "chatgpt.public",
        "sha256:chatgpt-public",
        "qualification-chatgpt-public",
        qualified=True,
        local_only=False,
        phi_eligible=False,
    )
    source = ContextSource(
        "requirements", {"goal": "Produce a public research summary."}, "revision-1"
    )
    bundle = ContextPackager().package(
        (source,),
        ContextRequest("research", ("requirements",), 100),
        qualification,
    )
    output = AgentOutput(
        "chatgpt-output",
        {"summary": "Evidence-backed result"},
        bundle.bundle_id,
        bundle.bundle_hash,
    )
    decision = MemoryAdmission().admit(
        output,
        target=AdmissionTarget.EVIDENCE,
        bundle=bundle,
        qualification=qualification,
        current_source_hashes={source.source_id: source.source_hash},
    )

    assert decision.status is AdmissionStatus.ADMITTED
    assert decision.destination == "evidence/chatgpt-output.json"
    assert decision.provenance.agent_id == "chatgpt.public"
    assert decision.provenance.source_ids == ("requirements",)
    assert decision.provenance.output_hash.startswith("sha256:")


def test_handoff_supervision_and_redacted_html_preserve_provenance() -> None:
    queue = SupervisionQueue.empty()
    gate = AgentSupervisionGate(queue)
    supervision = gate.require(
        AgentSupervisionRequest(
            invocation_key="handoff-private-review",
            request_hash="sha256:" + "b" * 64,
            slice_id="S-HANDOFF",
            agent_id="chatgpt.private",
            product=AgentProduct.CHATGPT,
            visibility=AgentVisibility.PERSONAL_PRIVATE,
            surface_kind=SurfaceKind.CONVERSATIONAL_HANDOFF,
            action=SupervisedAction.AMBIGUOUS_HANDOFF,
            operation="private review",
            evidence_ids=("qualification-private",),
        ),
        recorded_at=NOW,
    )
    returned = gate.user_returned(
        recorded_at=NOW,
        active_slice_ids=("S-INDEPENDENT",),
        at_safe_boundary=False,
    )
    assert supervision.queue_record.mandatory
    assert returned.cancel_active_work is False
    assert returned.present_now is False
    assert gate.presentation_decision(recorded_at=NOW, at_safe_boundary=True).present_now

    projection: dict[str, Any] = {
        "run_id": "agent-e2e",
        "source_revision": "revision-1",
        "updated_at": NOW.isoformat(),
        "agents": [
            {
                "agent_id": "chatgpt.private",
                "display_name": "Private reviewer",
                "product": "chatgpt",
                "classification": "personal_private",
                "version": "1",
                "observed_version": "1",
                "drift": False,
                "trust": "trusted",
                "availability": "handoff_only",
                "hidden_prompt": SECRET,
            }
        ],
        "invocations": [],
        "handoffs": [
            {
                "id": supervision.item_id,
                "agent_id": "chatgpt.private",
                "slice_id": "S-HANDOFF",
                "status": "waiting",
                "reason": "user supervision required",
                "credential": SECRET,
            }
        ],
        "failures": [],
        "connections": [
            {
                "id": "chatgpt-private-agent",
                "kind": "conversational_handoff",
                "agent_id": "chatgpt.private",
                "version": "1",
                "status": "available",
                "token": SECRET,
            }
        ],
        "provenance": [
            {
                "id": "provenance-1",
                "agent_id": "chatgpt.private",
                "invocation_id": "handoff-private-review",
                "slice_id": "S-HANDOFF",
                "artifact": "drafts/review.json",
                "input_hash": "sha256:" + "c" * 64,
                "output_hash": "sha256:" + "d" * 64,
                "recorded_at": NOW.isoformat(),
                "result": SECRET,
            }
        ],
    }
    views = render_agent_views(projection, sensitive_values=(SECRET,))
    rendered = json.dumps(views.dashboard) + "".join(views.html.values())

    assert SECRET not in rendered
    assert "provenance-1" in rendered
    assert "chatgpt.private" in rendered
    assert "hidden_prompt" not in rendered
    for page in views.html.values():
        verify_agent_view(page, projection)


def test_duplicate_host_reply_never_creates_duplicate_effect() -> None:
    scenario = next(item for item in _scenarios() if item.name == "mixed-agent-run")
    duplicate = FakeAgentReply("success", {"ok": True}, reply_id="one-effect")
    result = AgentCompatibilityHarness(
        {
            "codex": DeterministicCodexHost({"mixed-code": (duplicate,)}),
            "chatgpt": DeterministicChatGPTHost({"mixed-analysis": (duplicate,)}),
        }
    ).run(scenario)

    assert [item.status for item in result.records] == [
        "succeeded",
        "duplicate_ignored",
        "handoff_required",
    ]
    assert result.applied_effects == ("one-effect",)
