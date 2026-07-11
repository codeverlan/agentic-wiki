from __future__ import annotations

from datetime import datetime, timezone

import pytest

from memwiki.coordinator_agent_supervision import (
    AgentSupervisionGate,
    AgentSupervisionRequest,
    SupervisedAction,
)
from memwiki.coordinator_agents import AgentProduct, AgentVisibility, SurfaceKind
from memwiki.coordinator_supervision import SupervisionQueue

NOW = datetime(2026, 7, 11, 18, 0, tzinfo=timezone.utc)


def request(
    action: SupervisedAction,
    *,
    product: AgentProduct = AgentProduct.CODEX,
    visibility: AgentVisibility = AgentVisibility.PERSONAL_PRIVATE,
    surface: SurfaceKind = SurfaceKind.PLUGIN,
    agent_id: str = "seo-decision-lab@personal",
    delegated_by: tuple[str, ...] = (),
) -> AgentSupervisionRequest:
    return AgentSupervisionRequest(
        invocation_key="invoke-ax-010",
        request_hash="sha256:" + "a" * 64,
        slice_id="AX-010",
        agent_id=agent_id,
        product=product,
        visibility=visibility,
        surface_kind=surface,
        action=action,
        operation="perform supervised operation",
        evidence_ids=("qualification-1", "selection-1"),
        delegated_by=delegated_by,
    )


@pytest.mark.parametrize(
    "action",
    [
        SupervisedAction.AUTHORIZATION,
        SupervisedAction.LOGIN,
        SupervisedAction.CONSENT,
        SupervisedAction.COMPUTER_USE,
        SupervisedAction.PUBLICATION,
        SupervisedAction.PURCHASE,
        SupervisedAction.DESTRUCTIVE_ACTION,
        SupervisedAction.AMBIGUOUS_HANDOFF,
    ],
)
def test_all_supervised_actions_create_mandatory_items(action: SupervisedAction) -> None:
    queue = SupervisionQueue.empty()
    record = AgentSupervisionGate(queue).require(request(action), recorded_at=NOW)

    assert record.queue_record.mandatory
    assert record.invocation_key == "invoke-ax-010"
    assert record.resume_instructions
    assert queue.get(record.item_id).affected_slice_ids == ("AX-010",)


@pytest.mark.parametrize(
    ("product", "visibility", "surface", "expected"),
    [
        (AgentProduct.CODEX, AgentVisibility.PUBLIC, SurfaceKind.WORKER, "Codex"),
        (AgentProduct.CODEX, AgentVisibility.PERSONAL_PRIVATE, SurfaceKind.PLUGIN, "Codex"),
        (AgentProduct.CHATGPT, AgentVisibility.PUBLIC, SurfaceKind.ACTION, "ChatGPT"),
        (
            AgentProduct.CHATGPT,
            AgentVisibility.PERSONAL_PRIVATE,
            SurfaceKind.CONVERSATIONAL_HANDOFF,
            "conversation",
        ),
    ],
)
def test_resume_instructions_are_product_and_surface_specific(
    product: AgentProduct,
    visibility: AgentVisibility,
    surface: SurfaceKind,
    expected: str,
) -> None:
    record = AgentSupervisionGate(SupervisionQueue.empty()).require(
        request(
            SupervisedAction.AUTHORIZATION,
            product=product,
            visibility=visibility,
            surface=surface,
        ),
        recorded_at=NOW,
    )
    assert expected in record.resume_instructions
    assert visibility.value in record.resume_instructions


def test_delegation_cannot_bypass_computer_use_supervision() -> None:
    queue = SupervisionQueue.empty()
    gate = AgentSupervisionGate(queue)
    record = gate.require(
        request(
            SupervisedAction.COMPUTER_USE,
            product=AgentProduct.CHATGPT,
            surface=SurfaceKind.COMPUTER_USE,
            delegated_by=("public-planner", "private-codex-agent"),
        ),
        recorded_at=NOW,
    )
    assert record.delegated_by == ("public-planner", "private-codex-agent")
    assert record.queue_record.mandatory
    with pytest.raises(ValueError, match="exact invocation"):
        gate.authorize(
            record.item_id,
            invocation_key="different-invocation",
            disposition="approved",
            evidence_ids=("user-approval",),
            recorded_at=NOW,
        )


def test_approval_is_bound_to_exact_invocation_and_is_not_reusable() -> None:
    gate = AgentSupervisionGate(SupervisionQueue.empty())
    record = gate.require(request(SupervisedAction.PURCHASE), recorded_at=NOW)
    approval = gate.authorize(
        record.item_id,
        invocation_key=record.invocation_key,
        disposition="approved once",
        evidence_ids=("user-approval",),
        recorded_at=NOW,
    )
    assert approval.authorized
    assert gate.is_authorized(record.invocation_key, record.request_hash)
    assert not gate.is_authorized(record.invocation_key, "sha256:" + "b" * 64)


def test_user_return_prioritizes_handoffs_only_at_safe_boundary() -> None:
    queue = SupervisionQueue.empty()
    gate = AgentSupervisionGate(queue)
    record = gate.require(
        request(SupervisedAction.AMBIGUOUS_HANDOFF), recorded_at=NOW
    )

    decision = gate.user_returned(
        recorded_at=NOW,
        active_slice_ids=("safe-active",),
        at_safe_boundary=False,
    )
    assert decision.cancel_active_work is False
    assert decision.present_now is False
    assert decision.pending_item_ids == (record.item_id,)

    boundary = gate.presentation_decision(recorded_at=NOW, at_safe_boundary=True)
    assert boundary.present_now is True
    assert boundary.pending_item_ids == (record.item_id,)


def test_duplicate_requirement_is_idempotent_but_rebinding_is_rejected() -> None:
    gate = AgentSupervisionGate(SupervisionQueue.empty())
    first = gate.require(request(SupervisedAction.LOGIN), recorded_at=NOW)
    second = gate.require(request(SupervisedAction.LOGIN), recorded_at=NOW)
    assert first == second

    with pytest.raises(ValueError, match="bound to another request"):
        gate.require(
            request(SupervisedAction.CONSENT),
            recorded_at=NOW,
        )


def test_ambiguous_handoff_never_claims_execution_or_authorization() -> None:
    gate = AgentSupervisionGate(SupervisionQueue.empty())
    record = gate.require(
        request(
            SupervisedAction.AMBIGUOUS_HANDOFF,
            product=AgentProduct.CHATGPT,
            surface=SurfaceKind.CONVERSATIONAL_HANDOFF,
        ),
        recorded_at=NOW,
    )
    assert not record.authorized
    assert not gate.is_authorized(record.invocation_key, record.request_hash)
    assert "complete" in record.resume_instructions.lower()
