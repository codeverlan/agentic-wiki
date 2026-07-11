from pathlib import Path

import pytest

from memwiki.coordinator_receipts import CommandReceiptStore, ReceiptConflictError


def test_receipt_lifecycle_is_durable_and_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "receipts.json"
    store = CommandReceiptStore(path)

    planned = store.plan("deploy:AC-001", "deploy", {"target": "local"})
    assert store.plan("deploy:AC-001", "deploy", {"target": "local"}) == planned
    started = store.transition("deploy:AC-001", "started")
    succeeded = store.transition("deploy:AC-001", "succeeded", result={"revision": "abc123"})

    reopened = CommandReceiptStore(path)
    assert started.status == "started"
    assert reopened.get("deploy:AC-001") == succeeded
    assert reopened.should_execute("deploy:AC-001") is False


def test_same_idempotency_key_cannot_change_command_identity(tmp_path: Path) -> None:
    store = CommandReceiptStore(tmp_path / "receipts.json")
    store.plan("key", "publish", {"target": "a"})

    with pytest.raises(ReceiptConflictError):
        store.plan("key", "publish", {"target": "b"})


@pytest.mark.parametrize("terminal", ["succeeded", "failed", "cancelled", "outcome_unknown"])
def test_terminal_receipts_cannot_be_restarted(tmp_path: Path, terminal: str) -> None:
    store = CommandReceiptStore(tmp_path / "receipts.json")
    store.plan("key", "external-call", {})
    store.transition("key", "started")
    store.transition("key", terminal)

    assert store.should_execute("key") is False
    with pytest.raises(ReceiptConflictError, match="terminal"):
        store.transition("key", "started")


def test_planned_command_can_resume_but_started_command_becomes_outcome_unknown(tmp_path: Path) -> None:
    path = tmp_path / "receipts.json"
    store = CommandReceiptStore(path)
    store.plan("planned", "one", {})
    store.plan("started", "two", {})
    store.transition("started", "started")

    recovered = CommandReceiptStore(path)
    changed = recovered.reconcile_interrupted()

    assert recovered.should_execute("planned") is True
    assert recovered.get("started").status == "outcome_unknown"
    assert [receipt.idempotency_key for receipt in changed] == ["started"]
