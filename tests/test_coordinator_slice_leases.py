from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from memwiki.coordinator_assignments import (
    AssignmentEnvelope,
    BoundedContextManifest,
    ReportContract,
)
from memwiki.coordinator_slice_leases import (
    SliceLeaseAuthority,
    SliceLeaseConflictError,
    SliceLeaseFencedError,
    SliceLeaseStateError,
)

NOW = datetime(2026, 7, 11, 12, 0, tzinfo=timezone.utc)
TTL = timedelta(seconds=30)


def assignment(
    *,
    assignment_id: str = "assignment-1",
    slice_id: str = "AC-012",
    worker_id: str = "worker-1",
    owned_paths: tuple[str, ...] = ("src/memwiki/coordinator_slice_leases.py",),
    base_revision: str = "0123456789abcdef",
) -> AssignmentEnvelope:
    return AssignmentEnvelope.create(
        assignment_id=assignment_id,
        slice_id=slice_id,
        worker_id=worker_id,
        context=BoundedContextManifest.create(
            artifacts=("plan.json",),
            context_hashes=("sha256:plan",),
            token_budget=4_000,
        ),
        owned_paths=owned_paths,
        forbidden_paths=("wiki", ".git"),
        base_revision=base_revision,
        required_tests=("uv run pytest tests/test_coordinator_slice_leases.py -q",),
        permissions=("write_owned_paths",),
        lease_policy="acknowledge-before-write",
        report_contract=ReportContract(
            destination=f"reports/{slice_id}/{assignment_id}.json",
            required_fields=("changed_files", "tests"),
            schema_version=1,
        ),
        issued_at=NOW,
        acceptance_deadline=NOW + timedelta(minutes=5),
    )


def test_acquire_and_acknowledge_bind_assignment_and_coordinator_fence() -> None:
    authority = SliceLeaseAuthority()
    packet = assignment()

    lease = authority.acquire(packet, attempt=1, coordinator_fencing_token=7, now=NOW, ttl=TTL)
    acknowledged = authority.acknowledge(
        packet.assignment_id,
        worker_id=packet.worker_id,
        lease_token=lease.lease_token,
        coordinator_fencing_token=7,
        now=NOW + timedelta(seconds=1),
    )

    assert acknowledged.status == "active"
    assert acknowledged.assignment_hash == packet.assignment_hash
    assert acknowledged.slice_id == packet.slice_id
    assert acknowledged.owned_paths == packet.owned_paths
    assert acknowledged.base_revision == packet.base_revision
    assert acknowledged.attempt == 1
    assert acknowledged.coordinator_fencing_token == 7


def test_only_one_live_lease_can_own_a_slice_or_overlapping_path() -> None:
    authority = SliceLeaseAuthority()
    authority.acquire(assignment(), attempt=1, coordinator_fencing_token=1, now=NOW, ttl=TTL)

    with pytest.raises(SliceLeaseConflictError, match="slice"):
        authority.acquire(
            assignment(assignment_id="assignment-2", worker_id="worker-2"),
            attempt=2,
            coordinator_fencing_token=1,
            now=NOW,
            ttl=TTL,
        )
    with pytest.raises(SliceLeaseConflictError, match="path"):
        authority.acquire(
            assignment(
                assignment_id="assignment-3",
                slice_id="AC-099",
                worker_id="worker-3",
                owned_paths=("src/memwiki",),
            ),
            attempt=1,
            coordinator_fencing_token=1,
            now=NOW,
            ttl=TTL,
        )


@pytest.mark.parametrize(
    "candidate",
    ["SRC/MEMWIKI/COORDINATOR_SLICE_LEASES.PY", "src/memwiki/coordinator_slice_leases.py/x"],
)
def test_path_fencing_is_case_normalized_and_detects_descendants(candidate: str) -> None:
    authority = SliceLeaseAuthority()
    authority.acquire(assignment(), attempt=1, coordinator_fencing_token=1, now=NOW, ttl=TTL)

    with pytest.raises(SliceLeaseConflictError, match="path"):
        authority.acquire(
            assignment(
                assignment_id="assignment-other",
                slice_id="AC-other",
                worker_id="worker-other",
                owned_paths=(candidate,),
            ),
            attempt=1,
            coordinator_fencing_token=1,
            now=NOW,
            ttl=TTL,
        )


def test_heartbeat_release_and_expiry_require_current_identity_and_fences() -> None:
    authority = SliceLeaseAuthority()
    packet = assignment()
    lease = authority.acquire(packet, attempt=1, coordinator_fencing_token=3, now=NOW, ttl=TTL)

    with pytest.raises(SliceLeaseFencedError):
        authority.heartbeat(
            packet.assignment_id,
            worker_id=packet.worker_id,
            lease_token=lease.lease_token,
            coordinator_fencing_token=2,
            now=NOW,
            ttl=TTL,
        )
    authority.acknowledge(
        packet.assignment_id,
        worker_id=packet.worker_id,
        lease_token=lease.lease_token,
        coordinator_fencing_token=3,
        now=NOW,
    )
    renewed = authority.heartbeat(
        packet.assignment_id,
        worker_id=packet.worker_id,
        lease_token=lease.lease_token,
        coordinator_fencing_token=3,
        now=NOW + timedelta(seconds=5),
        ttl=TTL,
    )
    assert renewed.expires_at == NOW + timedelta(seconds=35)

    expired = authority.expire(now=NOW + timedelta(seconds=35))
    assert expired == (packet.assignment_id,)
    with pytest.raises(SliceLeaseFencedError):
        authority.release(
            packet.assignment_id,
            worker_id=packet.worker_id,
            lease_token=lease.lease_token,
            coordinator_fencing_token=3,
            now=NOW + timedelta(seconds=35),
        )


def test_takeover_supersedes_old_attempt_and_rejects_late_report() -> None:
    authority = SliceLeaseAuthority()
    old_packet = assignment()
    old = authority.acquire(old_packet, attempt=1, coordinator_fencing_token=4, now=NOW, ttl=TTL)

    replacement_packet = assignment(assignment_id="assignment-2", worker_id="worker-2")
    replacement = authority.takeover(
        replacement_packet,
        attempt=2,
        coordinator_fencing_token=5,
        now=NOW + TTL,
        ttl=TTL,
    )
    admission = authority.admit_report(
        old_packet.assignment_id,
        worker_id=old_packet.worker_id,
        lease_token=old.lease_token,
        coordinator_fencing_token=4,
        attempt=1,
        assignment_hash=old_packet.assignment_hash,
        received_at=NOW + TTL,
    )

    assert replacement.attempt == 2
    assert admission.preserve is True
    assert admission.integrable is False
    assert admission.reason == "superseded lease"


def test_current_report_is_integrable_only_for_exact_lease_identity() -> None:
    authority = SliceLeaseAuthority()
    packet = assignment()
    lease = authority.acquire(packet, attempt=1, coordinator_fencing_token=8, now=NOW, ttl=TTL)
    authority.acknowledge(
        packet.assignment_id,
        worker_id=packet.worker_id,
        lease_token=lease.lease_token,
        coordinator_fencing_token=8,
        now=NOW,
    )

    accepted = authority.admit_report(
        packet.assignment_id,
        worker_id=packet.worker_id,
        lease_token=lease.lease_token,
        coordinator_fencing_token=8,
        attempt=1,
        assignment_hash=packet.assignment_hash,
        received_at=NOW + timedelta(seconds=2),
    )
    stale = authority.admit_report(
        packet.assignment_id,
        worker_id=packet.worker_id,
        lease_token="wrong",
        coordinator_fencing_token=8,
        attempt=1,
        assignment_hash=packet.assignment_hash,
        received_at=NOW + timedelta(seconds=2),
    )

    assert accepted.integrable is True
    assert stale.preserve is True
    assert stale.integrable is False
    assert stale.reason == "lease identity mismatch"


def test_restart_serialization_preserves_fences_and_monotonic_tokens() -> None:
    authority = SliceLeaseAuthority()
    packet = assignment()
    old = authority.acquire(packet, attempt=1, coordinator_fencing_token=9, now=NOW, ttl=TTL)
    payload = authority.to_dict()

    restored = SliceLeaseAuthority.from_dict(payload)
    assert restored.to_dict() == payload
    restored.expire(now=NOW + TTL)
    replacement = restored.takeover(
        assignment(assignment_id="assignment-2", worker_id="worker-2"),
        attempt=2,
        coordinator_fencing_token=10,
        now=NOW + TTL,
        ttl=TTL,
    )
    assert replacement.lease_token != old.lease_token
    assert replacement.generation == old.generation + 1


def test_coordinator_fencing_token_cannot_move_backward() -> None:
    authority = SliceLeaseAuthority()
    authority.acquire(assignment(), attempt=1, coordinator_fencing_token=9, now=NOW, ttl=TTL)

    with pytest.raises(SliceLeaseFencedError, match="coordinator"):
        authority.acquire(
            assignment(
                assignment_id="assignment-other",
                slice_id="AC-other",
                worker_id="worker-other",
                owned_paths=("tests/other.py",),
            ),
            attempt=1,
            coordinator_fencing_token=8,
            now=NOW,
            ttl=TTL,
        )


def test_attempts_must_increase_and_clocks_and_ttl_are_validated() -> None:
    authority = SliceLeaseAuthority()
    packet = assignment()
    authority.acquire(packet, attempt=2, coordinator_fencing_token=1, now=NOW, ttl=TTL)
    authority.expire(now=NOW + TTL)

    with pytest.raises(SliceLeaseStateError, match="attempt"):
        authority.takeover(
            assignment(assignment_id="assignment-2", worker_id="worker-2"),
            attempt=2,
            coordinator_fencing_token=2,
            now=NOW + TTL,
            ttl=TTL,
        )
    with pytest.raises(ValueError, match="timezone-aware"):
        SliceLeaseAuthority().acquire(
            packet,
            attempt=1,
            coordinator_fencing_token=1,
            now=NOW.replace(tzinfo=None),
            ttl=TTL,
        )
    with pytest.raises(ValueError, match="positive"):
        SliceLeaseAuthority().acquire(
            packet,
            attempt=1,
            coordinator_fencing_token=1,
            now=NOW,
            ttl=timedelta(0),
        )
