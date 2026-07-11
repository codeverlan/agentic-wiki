from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import PurePosixPath
from typing import Any, Dict, List, Optional, Set

SLICE_STATUSES = {
    "proposed",
    "ready",
    "waiting",
    "active",
    "validating",
    "completed",
    "integrated",
    "blocked",
    "failed",
    "cancelled",
    "superseded",
}

SLICE_TRANSITIONS = {
    "proposed": {"ready", "waiting", "blocked", "cancelled", "superseded"},
    "ready": {"active", "blocked", "cancelled", "superseded"},
    "waiting": {"ready", "blocked", "cancelled", "superseded"},
    "active": {"validating", "blocked", "failed", "cancelled", "superseded"},
    "validating": {"completed", "active", "blocked", "failed", "cancelled"},
    "completed": {"integrated", "active", "blocked", "failed"},
    "blocked": {"ready", "cancelled", "failed", "superseded"},
    "failed": {"ready", "cancelled", "superseded"},
    "integrated": set(),
    "cancelled": set(),
    "superseded": set(),
}


def validate_slice_transition(current: str, requested: str) -> None:
    if current not in SLICE_STATUSES or requested not in SLICE_STATUSES:
        raise ValueError(f"Unknown slice status in transition: {current} -> {requested}")
    if requested not in SLICE_TRANSITIONS[current]:
        raise ValueError(f"Illegal slice transition: {current} -> {requested}")


def _object(value: object, label: str) -> Dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return value


def _strict_fields(value: Dict[str, Any], required: Set[str], label: str) -> None:
    missing = sorted(required - set(value))
    unknown = sorted(set(value) - required)
    if missing:
        raise ValueError(f"{label} missing required fields: {', '.join(missing)}")
    if unknown:
        raise ValueError(f"{label} has unknown fields: {', '.join(unknown)}")


def _string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    return value


def _timestamp(value: object, label: str) -> str:
    text = _string(value, label)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError(f"{label} must be an ISO-8601 timestamp") from None
    if parsed.tzinfo is None:
        raise ValueError(f"{label} must be an ISO-8601 timestamp with an offset")
    return text


def _string_list(value: object, label: str) -> List[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        raise ValueError(f"{label} must be a list of non-empty strings")
    return list(value)


def _owned_path(value: str, label: str) -> str:
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise ValueError(f"{label} must be a confined relative path")
    return value


def _paths_overlap(left: str, right: str) -> bool:
    left_parts = PurePosixPath(left.rstrip("/")).parts
    right_parts = PurePosixPath(right.rstrip("/")).parts
    common = min(len(left_parts), len(right_parts))
    return left_parts[:common] == right_parts[:common]


@dataclass(frozen=True)
class SliceRecord:
    slice_id: str
    title: str
    status: str
    depends_on: List[str]
    owned_paths: List[str]
    required: bool

    @classmethod
    def from_dict(cls, payload: object, index: int) -> "SliceRecord":
        value = _object(payload, f"slice {index}")
        fields = {"slice_id", "title", "status", "depends_on", "owned_paths", "required"}
        _strict_fields(value, fields, f"slice {index}")
        status = _string(value["status"], f"slice {index} status")
        if status not in SLICE_STATUSES:
            raise ValueError(f"unknown slice status: {status}")
        paths = [
            _owned_path(item, f"slice {index} owned_paths")
            for item in _string_list(value["owned_paths"], f"slice {index} owned_paths")
        ]
        if not isinstance(value["required"], bool):
            raise ValueError(f"slice {index} required must be a boolean")
        return cls(
            slice_id=_string(value["slice_id"], f"slice {index} slice_id"),
            title=_string(value["title"], f"slice {index} title"),
            status=status,
            depends_on=_string_list(value["depends_on"], f"slice {index} depends_on"),
            owned_paths=paths,
            required=value["required"],
        )


@dataclass(frozen=True)
class AttemptRecord:
    attempt_id: str
    slice_id: str
    number: int
    status: str
    started_at: str

    @classmethod
    def from_dict(cls, payload: object, index: int) -> "AttemptRecord":
        value = _object(payload, f"attempt {index}")
        fields = {"attempt_id", "slice_id", "number", "status", "started_at"}
        _strict_fields(value, fields, f"attempt {index}")
        if not isinstance(value["number"], int) or isinstance(value["number"], bool) or value["number"] < 1:
            raise ValueError(f"attempt {index} number must be a positive integer")
        status = _string(value["status"], f"attempt {index} status")
        if status not in {"planned", "running", "succeeded", "failed", "cancelled", "outcome_unknown"}:
            raise ValueError(f"attempt {index} has unknown status: {status}")
        return cls(
            attempt_id=_string(value["attempt_id"], f"attempt {index} attempt_id"),
            slice_id=_string(value["slice_id"], f"attempt {index} slice_id"),
            number=value["number"],
            status=status,
            started_at=_timestamp(value["started_at"], f"attempt {index} started_at"),
        )


@dataclass(frozen=True)
class LeaseRecord:
    lease_id: str
    slice_id: str
    worker_id: str
    status: str
    epoch: int
    fencing_token: int
    acquired_at: str
    heartbeat_at: str
    expires_at: str
    owned_paths: List[str]

    @classmethod
    def from_dict(cls, payload: object, index: int) -> "LeaseRecord":
        value = _object(payload, f"lease {index}")
        fields = {
            "lease_id",
            "slice_id",
            "worker_id",
            "status",
            "epoch",
            "fencing_token",
            "acquired_at",
            "heartbeat_at",
            "expires_at",
            "owned_paths",
        }
        _strict_fields(value, fields, f"lease {index}")
        status = _string(value["status"], f"lease {index} status")
        if status not in {"pending", "active", "released", "stale", "superseded"}:
            raise ValueError(f"lease {index} has unknown status: {status}")
        for field in ["epoch", "fencing_token"]:
            if not isinstance(value[field], int) or isinstance(value[field], bool) or value[field] < 1:
                raise ValueError(f"lease {index} {field} must be a positive integer")
        paths = [
            _owned_path(item, f"lease {index} owned_paths")
            for item in _string_list(value["owned_paths"], f"lease {index} owned_paths")
        ]
        return cls(
            lease_id=_string(value["lease_id"], f"lease {index} lease_id"),
            slice_id=_string(value["slice_id"], f"lease {index} slice_id"),
            worker_id=_string(value["worker_id"], f"lease {index} worker_id"),
            status=status,
            epoch=value["epoch"],
            fencing_token=value["fencing_token"],
            acquired_at=_timestamp(value["acquired_at"], f"lease {index} acquired_at"),
            heartbeat_at=_timestamp(value["heartbeat_at"], f"lease {index} heartbeat_at"),
            expires_at=_timestamp(value["expires_at"], f"lease {index} expires_at"),
            owned_paths=paths,
        )


@dataclass(frozen=True)
class BudgetState:
    token_limit: Optional[int]
    tokens_used: int
    time_limit_seconds: Optional[int]
    started_at: str

    @classmethod
    def from_dict(cls, payload: object) -> "BudgetState":
        value = _object(payload, "budget")
        fields = {"token_limit", "tokens_used", "time_limit_seconds", "started_at"}
        _strict_fields(value, fields, "budget")
        for field in ["token_limit", "time_limit_seconds"]:
            item = value[field]
            if item is not None and (not isinstance(item, int) or isinstance(item, bool) or item < 1):
                raise ValueError(f"budget {field} must be null or a positive integer")
        if (
            not isinstance(value["tokens_used"], int)
            or isinstance(value["tokens_used"], bool)
            or value["tokens_used"] < 0
        ):
            raise ValueError("budget tokens_used must be a non-negative integer")
        return cls(
            token_limit=value["token_limit"],
            tokens_used=value["tokens_used"],
            time_limit_seconds=value["time_limit_seconds"],
            started_at=_timestamp(value["started_at"], "budget started_at"),
        )


@dataclass(frozen=True)
class CoordinatorRuntimeState:
    schema_version: int
    run_id: str
    revision: int
    status: str
    created_at: str
    updated_at: str
    max_workers: int
    slices: List[SliceRecord]
    attempts: List[AttemptRecord]
    leases: List[LeaseRecord]
    blockers: List[Dict[str, Any]]
    stop_requests: List[Dict[str, Any]]
    budget: BudgetState
    command_receipts: List[Dict[str, Any]]
    capability_invocations: List[Dict[str, Any]]
    supervision_requests: List[Dict[str, Any]]

    @classmethod
    def from_dict(cls, payload: object) -> "CoordinatorRuntimeState":
        value = _object(payload, "coordinator runtime state")
        fields = {
            "schema_version",
            "run_id",
            "revision",
            "status",
            "created_at",
            "updated_at",
            "max_workers",
            "slices",
            "attempts",
            "leases",
            "blockers",
            "stop_requests",
            "budget",
            "command_receipts",
            "capability_invocations",
            "supervision_requests",
        }
        _strict_fields(value, fields, "coordinator runtime state")
        if value["schema_version"] != 1:
            raise ValueError("coordinator runtime state schema_version must be 1")
        if not isinstance(value["revision"], int) or isinstance(value["revision"], bool) or value["revision"] < 0:
            raise ValueError("coordinator runtime state revision must be a non-negative integer")
        status = _string(value["status"], "coordinator runtime state status")
        if status not in {"active", "stopping", "blocked", "completed", "failed"}:
            raise ValueError(f"unknown run status: {status}")
        max_workers = value["max_workers"]
        if not isinstance(max_workers, int) or isinstance(max_workers, bool) or not 1 <= max_workers <= 6:
            raise ValueError("max_workers must be from 1 through 6")
        for field in [
            "slices",
            "attempts",
            "leases",
            "blockers",
            "stop_requests",
            "command_receipts",
            "capability_invocations",
            "supervision_requests",
        ]:
            if not isinstance(value[field], list):
                raise ValueError(f"coordinator runtime state {field} must be a list")
        slices = [SliceRecord.from_dict(item, index) for index, item in enumerate(value["slices"], start=1)]
        attempts = [AttemptRecord.from_dict(item, index) for index, item in enumerate(value["attempts"], start=1)]
        leases = [LeaseRecord.from_dict(item, index) for index, item in enumerate(value["leases"], start=1)]
        _validate_runtime_relationships(slices, attempts, leases)
        return cls(
            schema_version=1,
            run_id=_string(value["run_id"], "coordinator runtime state run_id"),
            revision=value["revision"],
            status=status,
            created_at=_timestamp(value["created_at"], "created_at"),
            updated_at=_timestamp(value["updated_at"], "updated_at"),
            max_workers=max_workers,
            slices=slices,
            attempts=attempts,
            leases=leases,
            blockers=[_object(item, f"blocker {index}") for index, item in enumerate(value["blockers"], start=1)],
            stop_requests=[
                _object(item, f"stop request {index}") for index, item in enumerate(value["stop_requests"], start=1)
            ],
            budget=BudgetState.from_dict(value["budget"]),
            command_receipts=[
                _object(item, f"command receipt {index}")
                for index, item in enumerate(value["command_receipts"], start=1)
            ],
            capability_invocations=[
                _object(item, f"capability invocation {index}")
                for index, item in enumerate(value["capability_invocations"], start=1)
            ],
            supervision_requests=[
                _object(item, f"supervision request {index}")
                for index, item in enumerate(value["supervision_requests"], start=1)
            ],
        )

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _validate_runtime_relationships(
    slices: List[SliceRecord],
    attempts: List[AttemptRecord],
    leases: List[LeaseRecord],
) -> None:
    slice_ids = [item.slice_id for item in slices]
    if len(slice_ids) != len(set(slice_ids)):
        raise ValueError("duplicate slice_id in coordinator runtime state")
    known = set(slice_ids)
    graph: Dict[str, List[str]] = {}
    for item in slices:
        if item.slice_id in item.depends_on:
            raise ValueError(f"slice {item.slice_id} must not depend on itself")
        unknown = sorted(set(item.depends_on) - known)
        if unknown:
            raise ValueError(f"slice {item.slice_id} references unknown dependency: {', '.join(unknown)}")
        graph[item.slice_id] = item.depends_on
    visiting: Set[str] = set()
    visited: Set[str] = set()

    def visit(slice_id: str) -> None:
        if slice_id in visiting:
            raise ValueError("coordinator runtime state contains a dependency cycle")
        if slice_id in visited:
            return
        visiting.add(slice_id)
        for dependency in graph[slice_id]:
            visit(dependency)
        visiting.remove(slice_id)
        visited.add(slice_id)

    for slice_id in sorted(graph):
        visit(slice_id)
    attempt_ids = [item.attempt_id for item in attempts]
    if len(attempt_ids) != len(set(attempt_ids)):
        raise ValueError("duplicate attempt_id in coordinator runtime state")
    for attempt in attempts:
        if attempt.slice_id not in known:
            raise ValueError(f"attempt {attempt.attempt_id} references unknown slice")
    lease_ids = [item.lease_id for item in leases]
    if len(lease_ids) != len(set(lease_ids)):
        raise ValueError("duplicate lease_id in coordinator runtime state")
    for lease in leases:
        if lease.slice_id not in known:
            raise ValueError(f"lease {lease.lease_id} references unknown slice")
    active_paths: List[tuple[str, str]] = []
    for item in slices:
        if item.status in {"active", "validating"}:
            for path in item.owned_paths:
                for other_id, other_path in active_paths:
                    if _paths_overlap(path, other_path):
                        raise ValueError(
                            "overlapping active path ownership: "
                            f"{item.slice_id}:{path} conflicts with {other_id}:{other_path}"
                        )
                active_paths.append((item.slice_id, path))
