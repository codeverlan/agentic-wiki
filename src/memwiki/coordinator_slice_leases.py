from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from pathlib import PurePosixPath
from typing import Dict, Mapping, Optional, Tuple

from memwiki.coordinator_assignments import AssignmentEnvelope


class SliceLeaseError(RuntimeError):
    """Base error for slice lease failures."""


class SliceLeaseConflictError(SliceLeaseError):
    """A live slice or path is already leased."""


class SliceLeaseFencedError(SliceLeaseError):
    """A mutation was attempted with obsolete lease identity."""


class SliceLeaseStateError(SliceLeaseError):
    """A requested slice lease transition is invalid."""


def _aware(value: datetime, label: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware")
    return value


def _positive(value: int, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{label} must be a positive integer")
    return value


def _stored_positive(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{label} must be a positive integer")
    return _positive(value, label)


def _stored_strings(value: object, label: str) -> Tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValueError(f"{label} must be a list of strings")
    return tuple(value)


def _normalized_parts(value: str) -> Tuple[str, ...]:
    path = PurePosixPath(value)
    if path.is_absolute() or not path.parts or ".." in path.parts:
        raise ValueError("owned path must be a confined relative path")
    return tuple(part.casefold() for part in path.parts)


def _paths_overlap(left: str, right: str) -> bool:
    left_parts = _normalized_parts(left)
    right_parts = _normalized_parts(right)
    common = min(len(left_parts), len(right_parts))
    return left_parts[:common] == right_parts[:common]


@dataclass(frozen=True)
class SliceLease:
    assignment_id: str
    assignment_hash: str
    slice_id: str
    worker_id: str
    owned_paths: Tuple[str, ...]
    base_revision: str
    attempt: int
    coordinator_fencing_token: int
    generation: int
    lease_token: str
    status: str
    acquired_at: datetime
    acknowledged_at: Optional[datetime]
    heartbeat_at: datetime
    expires_at: datetime

    def is_live(self, now: datetime) -> bool:
        return self.status in {"offered", "active"} and now < self.expires_at

    def to_dict(self) -> Dict[str, object]:
        return {
            "assignment_id": self.assignment_id,
            "assignment_hash": self.assignment_hash,
            "slice_id": self.slice_id,
            "worker_id": self.worker_id,
            "owned_paths": list(self.owned_paths),
            "base_revision": self.base_revision,
            "attempt": self.attempt,
            "coordinator_fencing_token": self.coordinator_fencing_token,
            "generation": self.generation,
            "lease_token": self.lease_token,
            "status": self.status,
            "acquired_at": self.acquired_at.isoformat(),
            "acknowledged_at": (
                self.acknowledged_at.isoformat() if self.acknowledged_at is not None else None
            ),
            "heartbeat_at": self.heartbeat_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> "SliceLease":
        try:
            acknowledged = payload["acknowledged_at"]
            lease = cls(
                assignment_id=str(payload["assignment_id"]),
                assignment_hash=str(payload["assignment_hash"]),
                slice_id=str(payload["slice_id"]),
                worker_id=str(payload["worker_id"]),
                owned_paths=_stored_strings(payload["owned_paths"], "owned_paths"),
                base_revision=str(payload["base_revision"]),
                attempt=_stored_positive(payload["attempt"], "attempt"),
                coordinator_fencing_token=_stored_positive(
                    payload["coordinator_fencing_token"], "coordinator_fencing_token"
                ),
                generation=_stored_positive(payload["generation"], "generation"),
                lease_token=str(payload["lease_token"]),
                status=str(payload["status"]),
                acquired_at=_aware(datetime.fromisoformat(str(payload["acquired_at"])), "acquired_at"),
                acknowledged_at=(
                    None
                    if acknowledged is None
                    else _aware(datetime.fromisoformat(str(acknowledged)), "acknowledged_at")
                ),
                heartbeat_at=_aware(
                    datetime.fromisoformat(str(payload["heartbeat_at"])), "heartbeat_at"
                ),
                expires_at=_aware(datetime.fromisoformat(str(payload["expires_at"])), "expires_at"),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"malformed slice lease: {exc}") from exc
        if lease.status not in {"offered", "active", "released", "expired", "superseded"}:
            raise ValueError("invalid slice lease status")
        if not all((lease.assignment_id, lease.assignment_hash, lease.slice_id, lease.worker_id)):
            raise ValueError("slice lease identity fields must be non-empty")
        if not lease.owned_paths:
            raise ValueError("slice lease owned_paths must not be empty")
        for path in lease.owned_paths:
            _normalized_parts(path)
        return lease


@dataclass(frozen=True)
class ReportAdmission:
    preserve: bool
    integrable: bool
    reason: str


class SliceLeaseAuthority:
    """Coordinates slice ownership and path fencing across worker attempts."""

    def __init__(self) -> None:
        self._leases: Dict[str, SliceLease] = {}
        self._last_generation = 0

    @property
    def leases(self) -> Tuple[SliceLease, ...]:
        return tuple(self._leases[key] for key in sorted(self._leases))

    def acquire(
        self,
        assignment: AssignmentEnvelope,
        *,
        attempt: int,
        coordinator_fencing_token: int,
        now: datetime,
        ttl: timedelta,
    ) -> SliceLease:
        self._validate_request(assignment, attempt, coordinator_fencing_token, now, ttl)
        self._assert_coordinator_fence(coordinator_fencing_token)
        self.expire(now=now)
        self._assert_available(assignment)
        previous_attempts = [
            lease.attempt for lease in self._leases.values() if lease.slice_id == assignment.slice_id
        ]
        if previous_attempts and attempt <= max(previous_attempts):
            raise SliceLeaseStateError("attempt must increase for a reassigned slice")
        return self._grant(
            assignment,
            attempt=attempt,
            coordinator_fencing_token=coordinator_fencing_token,
            now=now,
            ttl=ttl,
        )

    def acknowledge(
        self,
        assignment_id: str,
        *,
        worker_id: str,
        lease_token: str,
        coordinator_fencing_token: int,
        now: datetime,
    ) -> SliceLease:
        lease = self._authorize(
            assignment_id, worker_id, lease_token, coordinator_fencing_token, now=now
        )
        if lease.status != "offered":
            raise SliceLeaseStateError("slice lease is not awaiting acknowledgement")
        updated = replace(lease, status="active", acknowledged_at=now, heartbeat_at=now)
        self._leases[assignment_id] = updated
        return updated

    def heartbeat(
        self,
        assignment_id: str,
        *,
        worker_id: str,
        lease_token: str,
        coordinator_fencing_token: int,
        now: datetime,
        ttl: timedelta,
    ) -> SliceLease:
        self._validate_ttl(ttl)
        lease = self._authorize(
            assignment_id, worker_id, lease_token, coordinator_fencing_token, now=now
        )
        if lease.status != "active":
            raise SliceLeaseStateError("slice lease must be acknowledged before heartbeat")
        updated = replace(lease, heartbeat_at=now, expires_at=now + ttl)
        self._leases[assignment_id] = updated
        return updated

    def release(
        self,
        assignment_id: str,
        *,
        worker_id: str,
        lease_token: str,
        coordinator_fencing_token: int,
        now: datetime,
    ) -> SliceLease:
        lease = self._authorize(
            assignment_id, worker_id, lease_token, coordinator_fencing_token, now=now
        )
        updated = replace(lease, status="released")
        self._leases[assignment_id] = updated
        return updated

    def expire(self, *, now: datetime) -> Tuple[str, ...]:
        _aware(now, "now")
        expired = []
        for assignment_id, lease in tuple(self._leases.items()):
            if lease.status in {"offered", "active"} and not lease.is_live(now):
                self._leases[assignment_id] = replace(lease, status="expired")
                expired.append(assignment_id)
        return tuple(sorted(expired))

    def takeover(
        self,
        assignment: AssignmentEnvelope,
        *,
        attempt: int,
        coordinator_fencing_token: int,
        now: datetime,
        ttl: timedelta,
    ) -> SliceLease:
        self._validate_request(assignment, attempt, coordinator_fencing_token, now, ttl)
        self._assert_coordinator_fence(coordinator_fencing_token)
        self.expire(now=now)
        same_slice = [
            lease for lease in self._leases.values() if lease.slice_id == assignment.slice_id
        ]
        if any(lease.is_live(now) for lease in same_slice):
            raise SliceLeaseConflictError("slice lease is still live")
        if same_slice and attempt <= max(lease.attempt for lease in same_slice):
            raise SliceLeaseStateError("attempt must increase for takeover")
        for lease in same_slice:
            if lease.status != "released":
                self._leases[lease.assignment_id] = replace(lease, status="superseded")
        self._assert_available(assignment)
        return self._grant(
            assignment,
            attempt=attempt,
            coordinator_fencing_token=coordinator_fencing_token,
            now=now,
            ttl=ttl,
        )

    def admit_report(
        self,
        assignment_id: str,
        *,
        worker_id: str,
        lease_token: str,
        coordinator_fencing_token: int,
        attempt: int,
        assignment_hash: str,
        received_at: datetime,
    ) -> ReportAdmission:
        _aware(received_at, "received_at")
        lease = self._leases.get(assignment_id)
        if lease is None:
            return ReportAdmission(True, False, "unknown lease")
        if lease.status == "superseded":
            return ReportAdmission(True, False, "superseded lease")
        exact_identity = (
            worker_id == lease.worker_id
            and lease_token == lease.lease_token
            and coordinator_fencing_token == lease.coordinator_fencing_token
            and attempt == lease.attempt
            and assignment_hash == lease.assignment_hash
        )
        if not exact_identity:
            return ReportAdmission(True, False, "lease identity mismatch")
        if lease.status != "active" or not lease.is_live(received_at):
            return ReportAdmission(True, False, "lease is not active")
        return ReportAdmission(True, True, "current live lease")

    def to_dict(self) -> Dict[str, object]:
        return {
            "last_generation": self._last_generation,
            "leases": [lease.to_dict() for lease in self.leases],
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> "SliceLeaseAuthority":
        if set(payload) != {"last_generation", "leases"}:
            raise ValueError("slice lease authority fields do not match the contract")
        authority = cls()
        try:
            last_generation = payload["last_generation"]
            if isinstance(last_generation, bool) or not isinstance(last_generation, int):
                raise ValueError("last_generation must be a non-negative integer")
            if last_generation < 0:
                raise ValueError("last_generation must be a non-negative integer")
            authority._last_generation = last_generation
            raw_leases = payload["leases"]
            if not isinstance(raw_leases, list):
                raise TypeError("leases must be a list")
            for raw in raw_leases:
                if not isinstance(raw, dict):
                    raise TypeError("lease must be an object")
                lease = SliceLease.from_dict(raw)
                if lease.assignment_id in authority._leases:
                    raise ValueError("duplicate assignment_id")
                authority._leases[lease.assignment_id] = lease
        except (TypeError, ValueError) as exc:
            raise ValueError(f"malformed slice lease authority: {exc}") from exc
        highest = max((lease.generation for lease in authority._leases.values()), default=0)
        if authority._last_generation < highest:
            raise ValueError("last_generation is behind persisted leases")
        return authority

    def _grant(
        self,
        assignment: AssignmentEnvelope,
        *,
        attempt: int,
        coordinator_fencing_token: int,
        now: datetime,
        ttl: timedelta,
    ) -> SliceLease:
        self._last_generation += 1
        token_material = ":".join(
            (
                assignment.assignment_hash,
                str(attempt),
                str(coordinator_fencing_token),
                str(self._last_generation),
            )
        ).encode()
        lease = SliceLease(
            assignment_id=assignment.assignment_id,
            assignment_hash=assignment.assignment_hash,
            slice_id=assignment.slice_id,
            worker_id=assignment.worker_id,
            owned_paths=assignment.owned_paths,
            base_revision=assignment.base_revision,
            attempt=attempt,
            coordinator_fencing_token=coordinator_fencing_token,
            generation=self._last_generation,
            lease_token=f"sha256:{hashlib.sha256(token_material).hexdigest()}",
            status="offered",
            acquired_at=now,
            acknowledged_at=None,
            heartbeat_at=now,
            expires_at=now + ttl,
        )
        self._leases[assignment.assignment_id] = lease
        return lease

    def _assert_available(self, assignment: AssignmentEnvelope) -> None:
        for lease in self._leases.values():
            if lease.status not in {"offered", "active"}:
                continue
            if lease.slice_id == assignment.slice_id:
                raise SliceLeaseConflictError(f"slice is already leased by {lease.assignment_id}")
            if any(
                _paths_overlap(candidate, owned)
                for candidate in assignment.owned_paths
                for owned in lease.owned_paths
            ):
                raise SliceLeaseConflictError(f"path is already leased by {lease.assignment_id}")

    def _assert_coordinator_fence(self, coordinator_fencing_token: int) -> None:
        highest = max(
            (lease.coordinator_fencing_token for lease in self._leases.values()), default=0
        )
        if coordinator_fencing_token < highest:
            raise SliceLeaseFencedError("coordinator fencing token moved backward")

    def _authorize(
        self,
        assignment_id: str,
        worker_id: str,
        lease_token: str,
        coordinator_fencing_token: int,
        *,
        now: datetime,
    ) -> SliceLease:
        _aware(now, "now")
        lease = self._leases.get(assignment_id)
        if lease is None:
            raise SliceLeaseFencedError("slice lease does not exist")
        if (
            lease.worker_id != worker_id
            or lease.lease_token != lease_token
            or lease.coordinator_fencing_token != coordinator_fencing_token
        ):
            raise SliceLeaseFencedError("slice lease identity has been fenced")
        if not lease.is_live(now):
            if lease.status in {"offered", "active"}:
                self._leases[assignment_id] = replace(lease, status="expired")
            raise SliceLeaseFencedError("slice lease is no longer live")
        return lease

    @staticmethod
    def _validate_ttl(ttl: timedelta) -> None:
        if ttl <= timedelta(0):
            raise ValueError("ttl must be positive")

    @classmethod
    def _validate_request(
        cls,
        assignment: AssignmentEnvelope,
        attempt: int,
        coordinator_fencing_token: int,
        now: datetime,
        ttl: timedelta,
    ) -> None:
        if not isinstance(assignment, AssignmentEnvelope):
            raise ValueError("assignment must be an AssignmentEnvelope")
        if assignment.assignment_hash != assignment.rehash().assignment_hash:
            raise ValueError("assignment hash does not match assignment contents")
        _positive(attempt, "attempt")
        _positive(coordinator_fencing_token, "coordinator_fencing_token")
        _aware(now, "now")
        cls._validate_ttl(ttl)
