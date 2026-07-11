from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import PurePosixPath
from typing import Dict, Iterable, Mapping, Optional, Tuple


class AssignmentError(RuntimeError):
    """An assignment cannot be issued or acknowledged safely."""


def _nonempty(value: str, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    return value


def _strings(values: Iterable[str], label: str, *, required: bool = True) -> Tuple[str, ...]:
    result = tuple(values)
    if required and not result:
        raise ValueError(f"{label} must not be empty")
    if any(not isinstance(item, str) or not item.strip() for item in result):
        raise ValueError(f"{label} must contain non-empty strings")
    if len(result) != len(set(result)):
        raise ValueError(f"{label} must not contain duplicates")
    return result


def _aware(value: datetime, label: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware")
    return value


def _path(value: str, label: str) -> str:
    _nonempty(value, label)
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise ValueError(f"{label} must be a confined relative path")
    return value.rstrip("/")


def _overlap(left: str, right: str) -> bool:
    left_parts = PurePosixPath(left).parts
    right_parts = PurePosixPath(right).parts
    common = min(len(left_parts), len(right_parts))
    return left_parts[:common] == right_parts[:common]


@dataclass(frozen=True)
class BoundedContextManifest:
    artifacts: Tuple[str, ...]
    context_hashes: Tuple[str, ...]
    token_budget: int

    @classmethod
    def create(
        cls,
        *,
        artifacts: Iterable[str],
        context_hashes: Iterable[str],
        token_budget: int,
    ) -> "BoundedContextManifest":
        artifact_values = _strings(artifacts, "context artifacts")
        hash_values = _strings(context_hashes, "context hashes")
        if len(artifact_values) != len(hash_values):
            raise ValueError("context artifacts and hashes must have equal lengths")
        if isinstance(token_budget, bool) or not isinstance(token_budget, int) or token_budget < 1:
            raise ValueError("context token_budget must be a positive integer")
        return cls(artifact_values, hash_values, token_budget)

    def to_dict(self) -> Dict[str, object]:
        return {
            "artifacts": list(self.artifacts),
            "context_hashes": list(self.context_hashes),
            "token_budget": self.token_budget,
        }


@dataclass(frozen=True)
class ReportContract:
    destination: str
    required_fields: Tuple[str, ...]
    schema_version: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "destination", _path(self.destination, "report destination"))
        object.__setattr__(self, "required_fields", _strings(self.required_fields, "report required_fields"))
        if isinstance(self.schema_version, bool) or self.schema_version < 1:
            raise ValueError("report schema_version must be a positive integer")

    def to_dict(self) -> Dict[str, object]:
        return {
            "destination": self.destination,
            "required_fields": list(self.required_fields),
            "schema_version": self.schema_version,
        }


@dataclass(frozen=True)
class AssignmentEnvelope:
    assignment_id: str
    slice_id: str
    worker_id: str
    context: BoundedContextManifest
    owned_paths: Tuple[str, ...]
    forbidden_paths: Tuple[str, ...]
    base_revision: str
    required_tests: Tuple[str, ...]
    permissions: Tuple[str, ...]
    lease_policy: str
    report_contract: ReportContract
    issued_at: datetime
    acceptance_deadline: datetime
    assignment_hash: str

    @classmethod
    def create(
        cls,
        *,
        assignment_id: str,
        slice_id: str,
        worker_id: str,
        context: BoundedContextManifest,
        owned_paths: Iterable[str],
        forbidden_paths: Iterable[str],
        base_revision: str,
        required_tests: Iterable[str],
        permissions: Iterable[str],
        lease_policy: str,
        report_contract: ReportContract,
        issued_at: datetime,
        acceptance_deadline: datetime,
    ) -> "AssignmentEnvelope":
        if not isinstance(context, BoundedContextManifest):
            raise ValueError("context must be a bounded context manifest")
        if not isinstance(report_contract, ReportContract):
            raise ValueError("report_contract must be a report contract")
        owned = tuple(_path(item, "owned_paths") for item in _strings(owned_paths, "owned_paths"))
        forbidden = tuple(
            _path(item, "forbidden_paths")
            for item in _strings(forbidden_paths, "forbidden_paths", required=False)
        )
        if any(_overlap(left, right) for left in owned for right in forbidden):
            raise ValueError("owned_paths and forbidden_paths must not overlap")
        issued = _aware(issued_at, "issued_at")
        deadline = _aware(acceptance_deadline, "acceptance_deadline")
        if deadline <= issued:
            raise ValueError("acceptance_deadline must be later than issued_at")
        envelope = cls(
            assignment_id=_nonempty(assignment_id, "assignment_id"),
            slice_id=_nonempty(slice_id, "slice_id"),
            worker_id=_nonempty(worker_id, "worker_id"),
            context=context,
            owned_paths=owned,
            forbidden_paths=forbidden,
            base_revision=_nonempty(base_revision, "base_revision"),
            required_tests=_strings(required_tests, "required_tests"),
            permissions=_strings(permissions, "permissions"),
            lease_policy=_nonempty(lease_policy, "lease_policy"),
            report_contract=report_contract,
            issued_at=issued,
            acceptance_deadline=deadline,
            assignment_hash="",
        )
        return envelope.rehash()

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> "AssignmentEnvelope":
        expected = {
            "assignment_id", "slice_id", "worker_id", "context", "owned_paths",
            "forbidden_paths", "base_revision", "required_tests", "permissions",
            "lease_policy", "report_contract", "issued_at", "acceptance_deadline",
            "assignment_hash",
        }
        if set(payload) != expected:
            raise ValueError("assignment payload fields do not match the contract")
        context_payload = payload["context"]
        report_payload = payload["report_contract"]
        if not isinstance(context_payload, dict) or not isinstance(report_payload, dict):
            raise ValueError("assignment context and report_contract must be objects")
        try:
            result = cls.create(
                assignment_id=str(payload["assignment_id"]),
                slice_id=str(payload["slice_id"]),
                worker_id=str(payload["worker_id"]),
                context=BoundedContextManifest.create(
                    artifacts=context_payload["artifacts"],
                    context_hashes=context_payload["context_hashes"],
                    token_budget=context_payload["token_budget"],
                ),
                owned_paths=payload["owned_paths"],  # type: ignore[arg-type]
                forbidden_paths=payload["forbidden_paths"],  # type: ignore[arg-type]
                base_revision=str(payload["base_revision"]),
                required_tests=payload["required_tests"],  # type: ignore[arg-type]
                permissions=payload["permissions"],  # type: ignore[arg-type]
                lease_policy=str(payload["lease_policy"]),
                report_contract=ReportContract(
                    destination=str(report_payload["destination"]),
                    required_fields=tuple(report_payload["required_fields"]),
                    schema_version=int(report_payload["schema_version"]),
                ),
                issued_at=datetime.fromisoformat(str(payload["issued_at"])),
                acceptance_deadline=datetime.fromisoformat(str(payload["acceptance_deadline"])),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"malformed assignment payload: {exc}") from exc
        if payload["assignment_hash"] != result.assignment_hash:
            raise ValueError("assignment_hash does not match assignment contents")
        return result

    def creation_values(self) -> Dict[str, object]:
        return {
            "assignment_id": self.assignment_id,
            "slice_id": self.slice_id,
            "worker_id": self.worker_id,
            "context": self.context,
            "owned_paths": self.owned_paths,
            "forbidden_paths": self.forbidden_paths,
            "base_revision": self.base_revision,
            "required_tests": self.required_tests,
            "permissions": self.permissions,
            "lease_policy": self.lease_policy,
            "report_contract": self.report_contract,
            "issued_at": self.issued_at,
            "acceptance_deadline": self.acceptance_deadline,
        }

    def rehash(self) -> "AssignmentEnvelope":
        digest = hashlib.sha256(self._canonical_bytes()).hexdigest()
        return replace(self, assignment_hash=f"sha256:{digest}")

    def _canonical_bytes(self) -> bytes:
        return json.dumps(self._content_dict(), sort_keys=True, separators=(",", ":")).encode()

    def _content_dict(self) -> Dict[str, object]:
        value = self.to_dict()
        value.pop("assignment_hash")
        return value

    def to_dict(self) -> Dict[str, object]:
        return {
            "assignment_id": self.assignment_id,
            "slice_id": self.slice_id,
            "worker_id": self.worker_id,
            "context": self.context.to_dict(),
            "owned_paths": list(self.owned_paths),
            "forbidden_paths": list(self.forbidden_paths),
            "base_revision": self.base_revision,
            "required_tests": list(self.required_tests),
            "permissions": list(self.permissions),
            "lease_policy": self.lease_policy,
            "report_contract": self.report_contract.to_dict(),
            "issued_at": self.issued_at.isoformat(),
            "acceptance_deadline": self.acceptance_deadline.isoformat(),
            "assignment_hash": self.assignment_hash,
        }


@dataclass(frozen=True)
class AssignmentResponse:
    assignment_id: str
    worker_id: str
    assignment_hash: str
    decision: str
    responded_at: datetime
    reason: Optional[str]

    def __post_init__(self) -> None:
        _nonempty(self.assignment_id, "assignment_id")
        _nonempty(self.worker_id, "worker_id")
        _nonempty(self.assignment_hash, "assignment_hash")
        _aware(self.responded_at, "responded_at")
        if self.decision not in {"acknowledge", "reject"}:
            raise ValueError("response decision must be acknowledge or reject")
        if self.decision == "acknowledge" and self.reason is not None:
            raise ValueError("acknowledgement reason must be null")
        if self.decision == "reject":
            _nonempty(self.reason or "", "rejection reason")

    @classmethod
    def acknowledge(
        cls, *, assignment_id: str, worker_id: str, assignment_hash: str, responded_at: datetime
    ) -> "AssignmentResponse":
        return cls._create(assignment_id, worker_id, assignment_hash, "acknowledge", responded_at, None)

    @classmethod
    def reject(
        cls,
        *,
        assignment_id: str,
        worker_id: str,
        assignment_hash: str,
        responded_at: datetime,
        reason: str,
    ) -> "AssignmentResponse":
        return cls._create(assignment_id, worker_id, assignment_hash, "reject", responded_at, reason)

    @classmethod
    def _create(
        cls,
        assignment_id: str,
        worker_id: str,
        assignment_hash: str,
        decision: str,
        responded_at: datetime,
        reason: Optional[str],
    ) -> "AssignmentResponse":
        if decision == "reject":
            reason = _nonempty(reason or "", "rejection reason")
        return cls(
            _nonempty(assignment_id, "assignment_id"),
            _nonempty(worker_id, "worker_id"),
            _nonempty(assignment_hash, "assignment_hash"),
            decision,
            _aware(responded_at, "responded_at"),
            reason,
        )


@dataclass(frozen=True)
class AssignmentState:
    envelope: AssignmentEnvelope
    status: str
    response: Optional[AssignmentResponse] = None


class AssignmentAuthority:
    """Issues assignment packets and arbitrates acknowledgement ownership."""

    def __init__(self) -> None:
        self._assignments: Dict[str, AssignmentState] = {}
        self._path_owners: Dict[str, str] = {}

    def issue(self, envelope: AssignmentEnvelope) -> AssignmentEnvelope:
        if envelope.assignment_hash != envelope.rehash().assignment_hash:
            raise AssignmentError("assignment_hash does not match assignment contents")
        if envelope.assignment_id in self._assignments:
            raise AssignmentError("assignment_id has already been issued")
        for candidate in envelope.owned_paths:
            for owned, assignment_id in self._path_owners.items():
                if _overlap(candidate, owned):
                    raise AssignmentError(f"path is already owned by {assignment_id}: {owned}")
        self._assignments[envelope.assignment_id] = AssignmentState(envelope, "pending")
        for path in envelope.owned_paths:
            self._path_owners[path] = envelope.assignment_id
        return envelope

    def respond(self, response: AssignmentResponse) -> AssignmentState:
        state = self._assignments.get(response.assignment_id)
        if state is None or state.status != "pending":
            raise AssignmentError("assignment is not pending")
        envelope = state.envelope
        if response.worker_id != envelope.worker_id:
            raise AssignmentError("response worker does not match assignment worker")
        if response.assignment_hash != envelope.assignment_hash:
            raise AssignmentError("response assignment_hash does not match assignment")
        if response.responded_at < envelope.issued_at:
            raise AssignmentError("response arrived before assignment was issued")
        if response.responded_at > envelope.acceptance_deadline:
            self._set_terminal(response.assignment_id, "timed_out", response)
            raise AssignmentError("acknowledgement arrived after acceptance deadline")
        status = "acknowledged" if response.decision == "acknowledge" else "rejected"
        result = self._set_terminal(response.assignment_id, status, response)
        if status == "acknowledged":
            for path in envelope.owned_paths:
                self._path_owners[path] = envelope.assignment_id
        return result

    def expire_pending(self, *, now: datetime) -> Tuple[str, ...]:
        _aware(now, "now")
        expired = []
        for assignment_id, state in tuple(self._assignments.items()):
            if state.status == "pending" and now > state.envelope.acceptance_deadline:
                self._set_terminal(assignment_id, "timed_out", None)
                expired.append(assignment_id)
        return tuple(sorted(expired))

    def owner_of(self, path: str) -> Optional[str]:
        candidate = _path(path, "path")
        for owned, assignment_id in self._path_owners.items():
            if _overlap(candidate, owned):
                return assignment_id
        return None

    def _set_terminal(
        self,
        assignment_id: str,
        status: str,
        response: Optional[AssignmentResponse],
    ) -> AssignmentState:
        previous = self._assignments[assignment_id]
        result = AssignmentState(previous.envelope, status, response)
        self._assignments[assignment_id] = result
        for path, owner in tuple(self._path_owners.items()):
            if owner == assignment_id:
                del self._path_owners[path]
        return result
