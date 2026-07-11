from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Optional


class CoordinatorLeaseError(RuntimeError):
    """Base error for coordinator ownership failures."""


class LeaseConflictError(CoordinatorLeaseError):
    """Another coordinator still owns the live lease."""


class LeaseNotOwnedError(CoordinatorLeaseError):
    """The supplied process identity does not own the lease."""


class LeaseFencedError(CoordinatorLeaseError):
    """The supplied fencing token is no longer authoritative."""


class LeaseExpiredError(CoordinatorLeaseError):
    """The coordinator lease has reached its expiry boundary."""


@dataclass(frozen=True)
class LeaseIdentity:
    host: str
    process_id: int
    instance_id: str

    def __post_init__(self) -> None:
        if not self.host.strip():
            raise ValueError("host must be a non-empty string")
        if isinstance(self.process_id, bool) or self.process_id < 1:
            raise ValueError("process_id must be a positive integer")
        if not self.instance_id.strip():
            raise ValueError("instance_id must be a non-empty string")


@dataclass(frozen=True)
class CoordinatorLease:
    owner: LeaseIdentity
    epoch: int
    fencing_token: int
    status: str
    acquired_at: datetime
    heartbeat_at: datetime
    expires_at: datetime

    def is_live(self, now: datetime) -> bool:
        return self.status == "active" and now < self.expires_at


class CoordinatorLeaseAuthority:
    """Owns coordinator lease transitions and rejects obsolete writers."""

    def __init__(self) -> None:
        self._lease: Optional[CoordinatorLease] = None
        self._last_epoch = 0
        self._last_fencing_token = 0

    @property
    def lease(self) -> Optional[CoordinatorLease]:
        return self._lease

    def acquire(
        self,
        owner: LeaseIdentity,
        *,
        now: datetime,
        ttl: timedelta,
    ) -> CoordinatorLease:
        self._validate_clock_and_ttl(now, ttl)
        current = self._lease
        if current is not None and current.status == "active":
            if not current.is_live(now):
                self._lease = replace(current, status="expired")
            elif current.owner != owner:
                raise LeaseConflictError("another process owns the live coordinator lease")
            else:
                self._lease = replace(current, heartbeat_at=now, expires_at=now + ttl)
                return self._lease
        return self._grant(owner, now=now, ttl=ttl)

    def heartbeat(
        self,
        owner: LeaseIdentity,
        fencing_token: int,
        *,
        now: datetime,
        ttl: timedelta,
    ) -> CoordinatorLease:
        self._validate_clock_and_ttl(now, ttl)
        current = self._authorize(owner, fencing_token, now=now)
        self._lease = replace(current, heartbeat_at=now, expires_at=now + ttl)
        return self._lease

    def release(
        self,
        owner: LeaseIdentity,
        fencing_token: int,
        *,
        now: datetime,
    ) -> CoordinatorLease:
        self._validate_clock(now)
        current = self._authorize(owner, fencing_token, now=now)
        self._lease = replace(current, status="released")
        return self._lease

    def expire(self, *, now: datetime) -> Optional[CoordinatorLease]:
        self._validate_clock(now)
        current = self._lease
        if current is not None and current.status == "active" and not current.is_live(now):
            self._lease = replace(current, status="expired")
        return self._lease

    def takeover(
        self,
        owner: LeaseIdentity,
        *,
        now: datetime,
        ttl: timedelta,
    ) -> CoordinatorLease:
        self._validate_clock_and_ttl(now, ttl)
        current = self._lease
        if current is not None and current.is_live(now):
            raise LeaseConflictError("coordinator lease is still live")
        if current is not None and current.status == "active":
            self._lease = replace(current, status="expired")
        return self._grant(owner, now=now, ttl=ttl)

    def assert_writer(
        self,
        owner: LeaseIdentity,
        fencing_token: int,
        *,
        now: datetime,
    ) -> None:
        self._validate_clock(now)
        self._authorize(owner, fencing_token, now=now)

    def _authorize(
        self,
        owner: LeaseIdentity,
        fencing_token: int,
        *,
        now: datetime,
    ) -> CoordinatorLease:
        current = self._lease
        if current is None or fencing_token != current.fencing_token or current.status == "released":
            raise LeaseFencedError("coordinator writer has been fenced")
        if current.owner != owner:
            raise LeaseNotOwnedError("process identity does not own coordinator lease")
        if current.status == "expired":
            raise LeaseExpiredError("coordinator lease has expired")
        if not current.is_live(now):
            self._lease = replace(current, status="expired")
            raise LeaseExpiredError("coordinator lease has expired")
        return current

    def _grant(
        self,
        owner: LeaseIdentity,
        *,
        now: datetime,
        ttl: timedelta,
    ) -> CoordinatorLease:
        self._last_epoch += 1
        self._last_fencing_token += 1
        self._lease = CoordinatorLease(
            owner=owner,
            epoch=self._last_epoch,
            fencing_token=self._last_fencing_token,
            status="active",
            acquired_at=now,
            heartbeat_at=now,
            expires_at=now + ttl,
        )
        return self._lease

    @staticmethod
    def _validate_clock(now: datetime) -> None:
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("now must be timezone-aware")

    @classmethod
    def _validate_clock_and_ttl(cls, now: datetime, ttl: timedelta) -> None:
        cls._validate_clock(now)
        if ttl <= timedelta(0):
            raise ValueError("ttl must be positive")
