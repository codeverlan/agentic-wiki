from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from memwiki.coordinator_lease import CoordinatorLeaseAuthority, LeaseIdentity
from memwiki.coordinator_recovery import (
    RecoveryArtifact,
    RecoveryDisposition,
    RecoveryDispositionKind,
    RecoveryError,
    StartupRecovery,
)

NOW = datetime(2026, 7, 11, 12, 0, tzinfo=timezone.utc)


def artifact(
    slice_id: str,
    *,
    assignment: str = "active",
    lease: str = "active",
    worker: str = "running",
    worktree: str = "clean",
    receipt: str = "none",
    integration: str = "none",
) -> RecoveryArtifact:
    return RecoveryArtifact(
        slice_id=slice_id,
        assignment_id=f"assignment-{slice_id}",
        attempt=1,
        assignment_status=assignment,
        lease_status=lease,
        worker_status=worker,
        worktree_status=worktree,
        receipt_status=receipt,
        integration_status=integration,
    )


@pytest.mark.parametrize(
    ("boundary", "item", "expected"),
    [
        ("assignment persisted", artifact("A", worker="not_started"), "resume"),
        ("lease expired", artifact("A", lease="expired"), "requeue"),
        ("worker vanished", artifact("A", worker="missing"), "requeue"),
        ("dirty worktree", artifact("A", worker="missing", worktree="dirty"), "manual_supervision"),
        ("effect planned", artifact("A", receipt="planned"), "resume"),
        ("effect started", artifact("A", receipt="started"), "outcome_unknown"),
        ("effect timed out", artifact("A", receipt="outcome_unknown"), "outcome_unknown"),
        ("integration started", artifact("A", integration="integration_started"), "outcome_unknown"),
        ("commit applied", artifact("A", integration="commit_applied"), "outcome_unknown"),
        ("post validation", artifact("A", integration="post_validation_started"), "outcome_unknown"),
        ("commit proven", artifact("A", integration="integrated"), "accepted"),
        ("worker report", artifact("A", worker="reported", worktree="committed"), "resume"),
    ],
)
def test_crash_matrix_is_conservative(boundary: str, item: RecoveryArtifact, expected: str) -> None:
    disposition = StartupRecovery.classify(item)
    assert disposition.kind.value == expected, boundary


def test_started_external_effect_is_never_retried() -> None:
    disposition = StartupRecovery.classify(artifact("A", receipt="started"))
    assert disposition.kind is RecoveryDispositionKind.OUTCOME_UNKNOWN
    assert disposition.dispatch_allowed is False
    assert disposition.preserve_evidence is True


def test_push_before_evidence_is_held_for_supervision() -> None:
    disposition = StartupRecovery.classify(artifact("A", integration="push_observed_without_receipt"))
    assert disposition.kind is RecoveryDispositionKind.OUTCOME_UNKNOWN
    assert "push" in disposition.reason


def test_blocked_slice_preserves_independent_requeue() -> None:
    result = StartupRecovery.reconcile(
        (
            artifact("A", receipt="started"),
            artifact("B", lease="expired", worker="missing"),
        )
    )
    assert result.by_slice("A").kind is RecoveryDispositionKind.OUTCOME_UNKNOWN
    assert result.by_slice("B").kind is RecoveryDispositionKind.REQUEUE
    assert result.dispatchable_slice_ids == ("B",)


def test_duplicate_slice_or_assignment_is_rejected() -> None:
    duplicate_slice = (artifact("A"), artifact("A"))
    with pytest.raises(RecoveryError, match="duplicate slice"):
        StartupRecovery.reconcile(duplicate_slice)
    duplicate_assignment = (
        artifact("A"),
        RecoveryArtifact(**{**artifact("B").to_dict(), "assignment_id": "assignment-A"}),
    )
    with pytest.raises(RecoveryError, match="duplicate assignment"):
        StartupRecovery.reconcile(duplicate_assignment)


def test_startup_verifies_state_before_fenced_takeover() -> None:
    authority = CoordinatorLeaseAuthority()
    old = LeaseIdentity("host", 1, "old")
    new = LeaseIdentity("host", 2, "new")
    authority.acquire(old, now=NOW, ttl=timedelta(seconds=1))
    calls: list[str] = []

    recovery = StartupRecovery(
        verify_state=lambda: calls.append("verify"),
        rebuild_projection=lambda: calls.append("rebuild"),
        lease_authority=authority,
        owner=new,
        now=lambda: NOW + timedelta(seconds=2),
        lease_ttl=timedelta(minutes=1),
        inspect=lambda: (artifact("A", lease="expired", worker="missing"),),
    )
    result = recovery.run()
    assert calls == ["verify", "rebuild"]
    assert result.coordinator_lease.owner == new
    assert result.coordinator_lease.fencing_token > 1
    assert result.by_slice("A").kind is RecoveryDispositionKind.REQUEUE


def test_verification_failure_prevents_takeover_and_inspection() -> None:
    authority = CoordinatorLeaseAuthority()
    inspected = False

    def fail() -> None:
        raise ValueError("journal hash mismatch")

    def inspect() -> tuple[RecoveryArtifact, ...]:
        nonlocal inspected
        inspected = True
        return ()

    recovery = StartupRecovery(
        verify_state=fail,
        rebuild_projection=lambda: None,
        lease_authority=authority,
        owner=LeaseIdentity("host", 2, "new"),
        now=lambda: NOW,
        lease_ttl=timedelta(minutes=1),
        inspect=inspect,
    )
    with pytest.raises(RecoveryError, match="verification failed"):
        recovery.run()
    assert authority.lease is None
    assert inspected is False


def test_live_coordinator_cannot_be_taken_over() -> None:
    authority = CoordinatorLeaseAuthority()
    authority.acquire(LeaseIdentity("host", 1, "old"), now=NOW, ttl=timedelta(minutes=2))
    recovery = StartupRecovery(
        verify_state=lambda: None,
        rebuild_projection=lambda: None,
        lease_authority=authority,
        owner=LeaseIdentity("host", 2, "new"),
        now=lambda: NOW + timedelta(seconds=1),
        lease_ttl=timedelta(minutes=1),
        inspect=lambda: (),
    )
    with pytest.raises(RecoveryError, match="takeover failed"):
        recovery.run()


def test_disposition_round_trip_is_stable() -> None:
    disposition = StartupRecovery.classify(artifact("A", receipt="started"))
    restored = RecoveryDisposition.from_dict(disposition.to_dict())
    assert restored == disposition


def test_disposition_rejects_string_boolean() -> None:
    values = StartupRecovery.classify(artifact("A")).to_dict()
    values["dispatch_allowed"] = "false"
    with pytest.raises(ValueError, match="flags"):
        RecoveryDisposition.from_dict(values)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("assignment_status", "mystery"),
        ("lease_status", "mystery"),
        ("worker_status", "mystery"),
        ("worktree_status", "mystery"),
        ("receipt_status", "mystery"),
        ("integration_status", "mystery"),
    ],
)
def test_unknown_inspection_state_is_rejected(field: str, value: str) -> None:
    values = artifact("A").to_dict()
    values[field] = value
    with pytest.raises(ValueError, match=field):
        RecoveryArtifact.from_dict(values)
