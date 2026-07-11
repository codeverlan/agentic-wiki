from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from memwiki.coordinator_lease import (
    CoordinatorLeaseAuthority,
    LeaseConflictError,
    LeaseExpiredError,
    LeaseFencedError,
    LeaseIdentity,
    LeaseNotOwnedError,
)

NOW = datetime(2026, 7, 11, 12, 0, tzinfo=timezone.utc)
TTL = timedelta(seconds=30)
PROCESS_A = LeaseIdentity(host="host-a", process_id=101, instance_id="instance-a")
PROCESS_B = LeaseIdentity(host="host-a", process_id=202, instance_id="instance-b")


def test_acquire_creates_live_fenced_lease() -> None:
    authority = CoordinatorLeaseAuthority()

    lease = authority.acquire(PROCESS_A, now=NOW, ttl=TTL)

    assert lease.owner == PROCESS_A
    assert lease.epoch == 1
    assert lease.fencing_token == 1
    assert lease.acquired_at == NOW
    assert lease.heartbeat_at == NOW
    assert lease.expires_at == NOW + TTL
    authority.assert_writer(PROCESS_A, lease.fencing_token, now=NOW)


def test_competing_process_cannot_acquire_live_lease() -> None:
    authority = CoordinatorLeaseAuthority()
    authority.acquire(PROCESS_A, now=NOW, ttl=TTL)

    with pytest.raises(LeaseConflictError, match="live coordinator lease"):
        authority.acquire(PROCESS_B, now=NOW + timedelta(seconds=1), ttl=TTL)


def test_owner_reacquire_renews_without_changing_fence() -> None:
    authority = CoordinatorLeaseAuthority()
    original = authority.acquire(PROCESS_A, now=NOW, ttl=TTL)

    renewed = authority.acquire(PROCESS_A, now=NOW + timedelta(seconds=10), ttl=TTL)

    assert renewed.epoch == original.epoch
    assert renewed.fencing_token == original.fencing_token
    assert renewed.heartbeat_at == NOW + timedelta(seconds=10)
    assert renewed.expires_at == NOW + timedelta(seconds=40)


def test_heartbeat_requires_current_owner_and_fence() -> None:
    authority = CoordinatorLeaseAuthority()
    lease = authority.acquire(PROCESS_A, now=NOW, ttl=TTL)

    with pytest.raises(LeaseNotOwnedError):
        authority.heartbeat(PROCESS_B, lease.fencing_token, now=NOW, ttl=TTL)
    with pytest.raises(LeaseFencedError):
        authority.heartbeat(PROCESS_A, lease.fencing_token + 1, now=NOW, ttl=TTL)

    renewed = authority.heartbeat(
        PROCESS_A,
        lease.fencing_token,
        now=NOW + timedelta(seconds=5),
        ttl=TTL,
    )
    assert renewed.expires_at == NOW + timedelta(seconds=35)


def test_expire_then_takeover_increments_epoch_and_fence() -> None:
    authority = CoordinatorLeaseAuthority()
    old = authority.acquire(PROCESS_A, now=NOW, ttl=TTL)

    expired = authority.expire(now=NOW + TTL)
    replacement = authority.takeover(PROCESS_B, now=NOW + TTL, ttl=TTL)

    assert expired is not None and expired.status == "expired"
    assert replacement.epoch == old.epoch + 1
    assert replacement.fencing_token == old.fencing_token + 1
    authority.assert_writer(PROCESS_B, replacement.fencing_token, now=NOW + TTL)
    with pytest.raises(LeaseFencedError):
        authority.assert_writer(PROCESS_A, old.fencing_token, now=NOW + TTL)


def test_takeover_expires_stale_lease_without_separate_expire_call() -> None:
    authority = CoordinatorLeaseAuthority()
    old = authority.acquire(PROCESS_A, now=NOW, ttl=TTL)

    replacement = authority.takeover(PROCESS_B, now=NOW + TTL, ttl=TTL)

    assert replacement.epoch == 2
    assert replacement.fencing_token > old.fencing_token


def test_takeover_rejects_live_owner() -> None:
    authority = CoordinatorLeaseAuthority()
    authority.acquire(PROCESS_A, now=NOW, ttl=TTL)

    with pytest.raises(LeaseConflictError, match="still live"):
        authority.takeover(PROCESS_B, now=NOW + timedelta(seconds=29), ttl=TTL)


def test_release_requires_current_owner_and_permanently_fences_token() -> None:
    authority = CoordinatorLeaseAuthority()
    lease = authority.acquire(PROCESS_A, now=NOW, ttl=TTL)

    with pytest.raises(LeaseNotOwnedError):
        authority.release(PROCESS_B, lease.fencing_token, now=NOW)

    released = authority.release(PROCESS_A, lease.fencing_token, now=NOW)
    assert released.status == "released"
    with pytest.raises(LeaseFencedError):
        authority.assert_writer(PROCESS_A, lease.fencing_token, now=NOW)

    replacement = authority.acquire(PROCESS_B, now=NOW, ttl=TTL)
    assert replacement.epoch == 2
    assert replacement.fencing_token == 2


def test_expired_owner_cannot_heartbeat_release_or_write() -> None:
    authority = CoordinatorLeaseAuthority()
    lease = authority.acquire(PROCESS_A, now=NOW, ttl=TTL)
    late = NOW + TTL

    with pytest.raises(LeaseExpiredError):
        authority.heartbeat(PROCESS_A, lease.fencing_token, now=late, ttl=TTL)
    with pytest.raises(LeaseExpiredError):
        authority.release(PROCESS_A, lease.fencing_token, now=late)
    with pytest.raises(LeaseExpiredError):
        authority.assert_writer(PROCESS_A, lease.fencing_token, now=late)


@pytest.mark.parametrize("ttl", [timedelta(0), timedelta(seconds=-1)])
def test_non_positive_ttl_is_rejected(ttl: timedelta) -> None:
    authority = CoordinatorLeaseAuthority()

    with pytest.raises(ValueError, match="positive"):
        authority.acquire(PROCESS_A, now=NOW, ttl=ttl)


def test_naive_clock_is_rejected() -> None:
    authority = CoordinatorLeaseAuthority()

    with pytest.raises(ValueError, match="timezone-aware"):
        authority.acquire(PROCESS_A, now=NOW.replace(tzinfo=None), ttl=TTL)


def test_identity_rejects_invalid_process_values() -> None:
    with pytest.raises(ValueError):
        LeaseIdentity(host="", process_id=1, instance_id="instance")
    with pytest.raises(ValueError):
        LeaseIdentity(host="host", process_id=0, instance_id="instance")
    with pytest.raises(ValueError):
        LeaseIdentity(host="host", process_id=1, instance_id="")
