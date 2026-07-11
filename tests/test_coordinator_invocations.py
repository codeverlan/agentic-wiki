from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Mapping

import pytest

from memwiki.coordinator_invocations import (
    AuthorityDecision,
    InvocationProtocol,
    InvocationRequest,
    RetryableInvocationError,
)
from memwiki.coordinator_receipts import CommandReceiptStore, ReceiptConflictError


def request(**changes: object) -> InvocationRequest:
    values: dict[str, object] = {
        "idempotency_key": "invoke:run-1:1",
        "capability_id": "mcp:test.search",
        "capability_version": "1.2.0",
        "operation": "search",
        "payload": {"query": "example"},
        "metadata": {"provider": "fake-mcp"},
        "timeout_seconds": 0.2,
        "max_output_bytes": 128,
    }
    values.update(changes)
    return InvocationRequest(**values)  # type: ignore[arg-type]


def allowed() -> AuthorityDecision:
    return AuthorityDecision(allowed=True, authority="project", reason="approved")


def test_receipts_persist_identity_hash_and_redacted_metadata_only(tmp_path: Path) -> None:
    store = CommandReceiptStore(tmp_path / "receipts.json")
    protocol = InvocationProtocol(store)
    invocation = request(
        payload={"query": "needle", "api_key": "payload-secret"},
        metadata={"endpoint": "local", "authorization": "metadata-secret"},
    )

    planned = protocol.plan(invocation, allowed(), secret_values=("payload-secret",))
    persisted = (tmp_path / "receipts.json").read_text()

    assert planned.status == "planned"
    assert planned.arguments["capability_id"] == "mcp:test.search"
    assert planned.arguments["capability_version"] == "1.2.0"
    assert planned.arguments["input_sha256"] == invocation.input_sha256
    assert planned.arguments["metadata"] == {
        "authorization": "[REDACTED]",
        "endpoint": "local",
    }
    assert "needle" not in persisted
    assert "payload-secret" not in persisted
    assert "metadata-secret" not in persisted


def test_denied_authority_never_creates_a_receipt(tmp_path: Path) -> None:
    store = CommandReceiptStore(tmp_path / "receipts.json")
    protocol = InvocationProtocol(store)

    with pytest.raises(PermissionError, match="supervision required"):
        protocol.plan(
            request(),
            AuthorityDecision(False, "supervision", "supervision required"),
        )

    assert not (tmp_path / "receipts.json").exists()


def test_successful_fake_capabilities_return_bounded_result_packets(tmp_path: Path) -> None:
    protocol = InvocationProtocol(CommandReceiptStore(tmp_path / "receipts.json"))

    for kind in ("mcp", "api", "plugin", "local-command"):
        invocation = request(
            idempotency_key=f"invoke:{kind}",
            capability_id=f"{kind}:fake",
            payload={"kind": kind},
            max_output_bytes=16,
        )
        result = protocol.invoke(
            invocation,
            allowed(),
            lambda payload: {"kind": payload["kind"], "content": "x" * 100},
        )

        assert result.outcome == "succeeded"
        assert result.output_truncated is True
        assert len(result.output.encode()) <= 16
        assert len(result.output_sha256) == 64
        assert result.receipt.status == "succeeded"


def test_malformed_output_is_failed_and_preserved_as_bounded_evidence(tmp_path: Path) -> None:
    protocol = InvocationProtocol(CommandReceiptStore(tmp_path / "receipts.json"))

    result = protocol.invoke(
        request(),
        allowed(),
        lambda _payload: {"unexpected": "x" * 1000},
        validate_output=lambda output: isinstance(output, Mapping) and "items" in output,
    )

    assert result.outcome == "failed"
    assert result.error == "capability output failed schema validation"
    assert result.output_truncated is True
    assert result.receipt.status == "failed"


def test_timeout_and_cancellation_are_typed_terminal_outcomes(tmp_path: Path) -> None:
    protocol = InvocationProtocol(CommandReceiptStore(tmp_path / "receipts.json"))

    timed_out = protocol.invoke(
        request(idempotency_key="timeout", timeout_seconds=0.01),
        allowed(),
        lambda _payload: (time.sleep(0.05), {"late": True})[1],
    )
    cancelled = protocol.invoke(
        request(idempotency_key="cancelled"),
        allowed(),
        lambda _payload: {"unused": True},
        cancelled=lambda: True,
    )

    assert timed_out.outcome == "outcome_unknown"
    assert timed_out.timed_out is True
    assert timed_out.receipt.status == "outcome_unknown"
    assert cancelled.outcome == "cancelled"
    assert cancelled.cancelled is True
    assert cancelled.receipt.status == "cancelled"


def test_restart_does_not_repeat_started_or_terminal_effects(tmp_path: Path) -> None:
    path = tmp_path / "receipts.json"
    first = InvocationProtocol(CommandReceiptStore(path))
    invocation = request()
    first.plan(invocation, allowed())
    first.start(invocation)

    restarted = InvocationProtocol(CommandReceiptStore(path))
    changed = restarted.reconcile_interrupted()
    calls: list[Mapping[str, Any]] = []
    result = restarted.invoke(invocation, allowed(), lambda payload: calls.append(payload))

    assert [receipt.idempotency_key for receipt in changed] == [invocation.idempotency_key]
    assert result.outcome == "outcome_unknown"
    assert calls == []


def test_idempotency_key_cannot_be_rebound_to_different_input(tmp_path: Path) -> None:
    protocol = InvocationProtocol(CommandReceiptStore(tmp_path / "receipts.json"))
    protocol.plan(request(), allowed())

    with pytest.raises(ReceiptConflictError):
        protocol.plan(request(payload={"query": "different"}), allowed())


def test_only_explicit_pre_acceptance_failures_are_retried(tmp_path: Path) -> None:
    protocol = InvocationProtocol(CommandReceiptStore(tmp_path / "receipts.json"))
    attempts = 0

    def executor(_payload: Mapping[str, Any]) -> object:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RetryableInvocationError("provider was not reached")
        return {"accepted": True}

    result = protocol.invoke(request(retry_limit=1), allowed(), executor)

    assert result.outcome == "succeeded"
    assert attempts == 2


def test_request_rejects_unbounded_or_non_json_input() -> None:
    with pytest.raises(ValueError, match="timeout"):
        request(timeout_seconds=0)
    with pytest.raises(ValueError, match="output limit"):
        request(max_output_bytes=0)
    with pytest.raises(ValueError, match="input limit"):
        request(payload={"large": "x" * 100}, max_input_bytes=16)
    with pytest.raises(ValueError, match="JSON serializable"):
        request(payload={"bad": object()})
