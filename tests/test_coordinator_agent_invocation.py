from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

import pytest

from memwiki.coordinator_agent_invocation import (
    AgentInvocationProtocol,
    AgentInvocationRequest,
    AgentInvocationResult,
    InvocationStatus,
)
from memwiki.coordinator_agent_policy import QualificationReceipt, QualificationStatus
from memwiki.coordinator_agent_selection import (
    AgentSelectionPlan,
    SelectionOption,
    SelectionStatus,
)
from memwiki.coordinator_agents import ExecutionMode, SurfaceKind
from memwiki.coordinator_supervision import SupervisionQueue

NOW = datetime(2026, 7, 11, 16, 0, tzinfo=timezone.utc)


def qualification(mode: ExecutionMode, surface: str = "surface.main") -> QualificationReceipt:
    payload = {
        "agent_id": "agent.example",
        "stable_identity": "sha256:" + "1" * 64,
        "agent_version": "1.2.3",
        "operation": "analyze",
        "status": (
            QualificationStatus.DOWNGRADED.value
            if mode is ExecutionMode.HANDOFF_ONLY
            else QualificationStatus.QUALIFIED.value
        ),
        "execution_mode": mode.value,
        "surface_id": surface,
        "input_schema": "input.v1",
        "output_schema": "output.v1",
        "granted_authority": ["read"],
        "rejection_reasons": [],
        "downgrade_reasons": (
            ["callable execution not authorized"]
            if mode is ExecutionMode.HANDOFF_ONLY
            else []
        ),
        "policy_snapshot": {"cost": "free"},
        "requirement_snapshot": {"operation": "analyze"},
    }
    import hashlib
    import json

    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return QualificationReceipt.from_dict(
        {"receipt_id": "sha256:" + hashlib.sha256(encoded).hexdigest(), **payload}
    )


def selection(receipt: QualificationReceipt) -> AgentSelectionPlan:
    option = SelectionOption(
        agent_id=receipt.agent_id,
        stable_identity=receipt.stable_identity,
        receipt_id=receipt.receipt_id,
        score=(0, 0, 0, 0, receipt.stable_identity),
        explanation="selected for test",
    )
    raw = {
        "slice_id": "AX-007",
        "operation": "analyze",
        "status": "selected",
        "selected_agent_id": receipt.agent_id,
        "fallback_chain": [option.to_dict()],
        "failed_agent_ids": [],
        "excluded_agents": {},
        "explanations": ["selected"],
        "blocked_slice_id": None,
        "blocker_scope": None,
    }
    import hashlib
    import json

    encoded = json.dumps(raw, sort_keys=True, separators=(",", ":")).encode()
    return AgentSelectionPlan.from_dict(
        {"plan_id": "sha256:" + hashlib.sha256(encoded).hexdigest(), **raw}
    )


def request(kind: SurfaceKind = SurfaceKind.MCP) -> AgentInvocationRequest:
    return AgentInvocationRequest(
        idempotency_key="invoke-1",
        slice_id="AX-007",
        operation="analyze",
        surface_kind=kind,
        payload={"query": "safe", "token": "do-not-store"},
        metadata={"correlation": "run-1"},
        correlation_id="run-1",
        causation_id="event-1",
        timeout_seconds=5,
        max_output_bytes=1024,
    )


@pytest.mark.parametrize(
    "kind",
    [SurfaceKind.WORKER, SurfaceKind.ACTION, SurfaceKind.MCP, SurfaceKind.APP],
)
def test_callable_surface_conformance(tmp_path: Path, kind: SurfaceKind) -> None:
    protocol = AgentInvocationProtocol(tmp_path / "receipts.json")
    receipt = qualification(ExecutionMode.CALLABLE)
    calls = []

    def execute(payload: Mapping[str, Any]) -> object:
        calls.append(payload)
        return {"answer": "ok", "secret": "do-not-store"}

    result = protocol.invoke(
        request(kind), receipt, selection(receipt), execute, secret_values=("do-not-store",)
    )

    assert result.receipt.status is InvocationStatus.SUCCEEDED
    assert len(calls) == 1
    serialized = (tmp_path / "receipts.json").read_text()
    assert "do-not-store" not in serialized
    assert result.receipt.agent_version == "1.2.3"
    assert result.receipt.surface_version == "1.2.3"
    assert result.receipt.correlation_id == "run-1"
    assert result.receipt.causation_id == "event-1"


def test_conversational_handoff_creates_supervision_without_execution(tmp_path: Path) -> None:
    queue = SupervisionQueue.empty()
    protocol = AgentInvocationProtocol(tmp_path / "receipts.json", supervision=queue)
    qualified = qualification(ExecutionMode.HANDOFF_ONLY, "conversation.main")
    called = False

    def execute(_: Mapping[str, Any]) -> object:
        nonlocal called
        called = True
        return {}

    result = protocol.invoke(
        request(SurfaceKind.CONVERSATIONAL_HANDOFF),
        qualified,
        selection(qualified),
        execute,
        recorded_at=NOW,
        secret_values=("do-not-store",),
    )

    assert not called
    assert result.receipt.status is InvocationStatus.HANDOFF_REQUIRED
    assert result.receipt.started_at is None
    assert queue.get(result.receipt.supervision_item_id or "").summary


def test_restart_prevents_duplicate_execution(tmp_path: Path) -> None:
    path = tmp_path / "receipts.json"
    qualified = qualification(ExecutionMode.CALLABLE)
    count = 0

    def execute(_: Mapping[str, Any]) -> object:
        nonlocal count
        count += 1
        return {"ok": True}

    first = AgentInvocationProtocol(path).invoke(
        request(), qualified, selection(qualified), execute
    )
    second = AgentInvocationProtocol(path).invoke(
        request(), qualified, selection(qualified), execute
    )
    assert first.receipt == second.receipt
    assert count == 1


def test_started_receipt_becomes_outcome_unknown_after_restart(tmp_path: Path) -> None:
    path = tmp_path / "receipts.json"
    protocol = AgentInvocationProtocol(path)
    qualified = qualification(ExecutionMode.CALLABLE)
    req = request()
    protocol.plan(req, qualified, selection(qualified))
    protocol.authorize(req.idempotency_key)
    protocol.start(req.idempotency_key)

    recovered = AgentInvocationProtocol(path).reconcile_interrupted()
    assert recovered[0].status is InvocationStatus.OUTCOME_UNKNOWN


def test_timeout_cancel_and_failure_are_typed(tmp_path: Path) -> None:
    qualified = qualification(ExecutionMode.CALLABLE)
    plan = selection(qualified)
    cancelled = AgentInvocationProtocol(tmp_path / "cancel.json").invoke(
        request(), qualified, plan, lambda _: {}, cancelled=lambda: True
    )
    failed = AgentInvocationProtocol(tmp_path / "fail.json").invoke(
        request(), qualified, plan, lambda _: AgentInvocationResult.failed("no")
    )
    timeout = AgentInvocationProtocol(tmp_path / "timeout.json").invoke(
        request(), qualified, plan, lambda _: AgentInvocationResult.timeout()
    )
    assert cancelled.receipt.status is InvocationStatus.CANCELLED
    assert failed.receipt.status is InvocationStatus.FAILED
    assert timeout.receipt.status is InvocationStatus.OUTCOME_UNKNOWN


def test_rejects_mismatched_selection_and_idempotency_rebinding(tmp_path: Path) -> None:
    protocol = AgentInvocationProtocol(tmp_path / "receipts.json")
    qualified = qualification(ExecutionMode.CALLABLE)
    blocked = AgentSelectionPlan(
        plan_id="irrelevant",
        slice_id="AX-007",
        operation="analyze",
        status=SelectionStatus.BLOCKED,
        selected_agent_id=None,
        fallback_chain=(),
        failed_agent_ids=(),
        excluded_agents={},
        explanations=(),
        blocked_slice_id="AX-007",
        blocker_scope="slice",
    )
    with pytest.raises(ValueError, match="selected"):
        protocol.plan(request(), qualified, blocked)
    protocol.invoke(request(), qualified, selection(qualified), lambda _: {})
    changed = replace(request(), payload={"query": "different"})
    with pytest.raises(ValueError, match="bound"):
        protocol.plan(changed, qualified, selection(qualified))
