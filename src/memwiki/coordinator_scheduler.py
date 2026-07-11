from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import PurePosixPath
from typing import Mapping, Optional, Sequence, Tuple

from memwiki.coordinator_discovery import (
    DiscoveryDecision,
    DiscoveryProposal,
    replenish_queue,
)
from memwiki.coordinator_graph import GraphEvaluation, evaluate_dependency_graph
from memwiki.coordinator_lease import CoordinatorLease, LeaseIdentity
from memwiki.coordinator_models import SliceRecord
from memwiki.coordinator_workers import WorkerOutcome


@dataclass(frozen=True)
class SliceLeaseSnapshot:
    assignment_id: str
    slice_id: str
    owned_paths: Tuple[str, ...]
    status: str
    expires_at: datetime

    def __post_init__(self) -> None:
        if not self.assignment_id or not self.slice_id:
            raise ValueError("slice lease identity must be non-empty")
        if self.status not in {"offered", "active", "released", "expired", "superseded"}:
            raise ValueError("unknown slice lease status")
        _aware(self.expires_at, "slice lease expiry")
        for path in self.owned_paths:
            _path_parts(path)

    def is_live(self, now: datetime) -> bool:
        return self.status in {"offered", "active"} and now < self.expires_at


@dataclass(frozen=True)
class WorkerObservation:
    assignment_id: str
    slice_id: str
    state: str
    outcome: Optional[WorkerOutcome]

    def __post_init__(self) -> None:
        if not self.assignment_id or not self.slice_id:
            raise ValueError("worker observation identity must be non-empty")
        if self.state not in {"awaiting_acknowledgement", "running", "finished"}:
            raise ValueError("worker observation state is invalid")
        if self.state == "finished" and self.outcome is None:
            raise ValueError("finished worker observation requires an outcome")
        if self.state != "finished" and self.outcome is not None:
            raise ValueError("non-terminal worker observation cannot include an outcome")


@dataclass(frozen=True)
class SchedulerInput:
    slices: Tuple[SliceRecord, ...]
    slice_leases: Tuple[SliceLeaseSnapshot, ...]
    worker_observations: Tuple[WorkerObservation, ...]
    discovery_proposals: Tuple[DiscoveryProposal, ...]
    coordinator_lease: CoordinatorLease
    owner: LeaseIdentity
    fencing_token: int
    max_workers: int
    ready_order: Tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if isinstance(self.max_workers, bool) or not 1 <= self.max_workers <= 6:
            raise ValueError("max_workers must be from 1 through 6")
        if len(self.ready_order) != len(set(self.ready_order)):
            raise ValueError("ready_order must not contain duplicates")


@dataclass(frozen=True)
class SchedulerIntent:
    kind: str
    slice_id: Optional[str]
    reference_id: Optional[str]
    reason: str
    details: Mapping[str, object]


@dataclass(frozen=True)
class DispatchIntent:
    slice_id: str
    owned_paths: Tuple[str, ...]


@dataclass(frozen=True)
class SchedulerEvent:
    event_id: str
    ordinal: int
    kind: str
    slice_id: Optional[str]
    recorded_at: datetime


@dataclass(frozen=True)
class SchedulerDecision:
    graph: GraphEvaluation
    slices: Tuple[SliceRecord, ...]
    discovery_decisions: Tuple[DiscoveryDecision, ...]
    admitted_slice_ids: Tuple[str, ...]
    intents: Tuple[SchedulerIntent, ...]
    events: Tuple[SchedulerEvent, ...]
    dispatches: Tuple[DispatchIntent, ...]
    deferred: Tuple[str, ...]


def reconciliation_tick(state: SchedulerInput, *, now: datetime) -> SchedulerDecision:
    """Return one deterministic reconciliation decision without performing side effects."""
    _aware(now, "now")
    _verify_coordinator(state, now)
    known = {item.slice_id for item in state.slices}
    _validate_references(state, known)

    replenishment = replenish_queue(state.slices, state.discovery_proposals)
    slices = replenishment.slices
    intents = []
    for decision in replenishment.decisions:
        intents.append(
            SchedulerIntent(
                "admit_discovery" if decision.disposition == "admitted" else "record_discovery",
                decision.slice_id,
                decision.proposal_id,
                decision.reason,
                {"disposition": decision.disposition},
            )
        )

    live_leases = []
    expired_slice_ids = set()
    for lease in sorted(state.slice_leases, key=lambda item: item.assignment_id):
        if lease.status in {"offered", "active"} and not lease.is_live(now):
            expired_slice_ids.add(lease.slice_id)
            intents.append(
                SchedulerIntent(
                    "expire_lease", lease.slice_id, lease.assignment_id, "slice_lease_expired", {}
                )
            )
            intents.append(
                SchedulerIntent(
                    "retry", lease.slice_id, lease.assignment_id, "worker_lease_expired", {}
                )
            )
        elif lease.is_live(now):
            live_leases.append(lease)

    terminal_observations = set()
    for observation in sorted(state.worker_observations, key=lambda item: item.assignment_id):
        if observation.state != "finished":
            continue
        terminal_observations.add(observation.assignment_id)
        assert observation.outcome is not None
        intents.append(_outcome_intent(observation))

    live_leases = [
        lease
        for lease in live_leases
        if lease.assignment_id not in terminal_observations and lease.slice_id not in expired_slice_ids
    ]
    graph = evaluate_dependency_graph(slices)
    capacity = max(0, state.max_workers - len(live_leases))
    occupied_paths = [path for lease in live_leases for path in lease.owned_paths]
    dispatches = []
    deferred = []
    nodes = {item.slice_id: item for item in slices}
    ready_rank = {slice_id: index for index, slice_id in enumerate(state.ready_order)}
    ordered_ready = sorted(graph.ready, key=lambda item: (ready_rank.get(item, len(ready_rank)), item))
    for slice_id in ordered_ready:
        node = nodes[slice_id]
        if capacity == 0 or _conflicts(node.owned_paths, occupied_paths):
            deferred.append(slice_id)
            continue
        dispatch = DispatchIntent(slice_id, tuple(node.owned_paths))
        dispatches.append(dispatch)
        occupied_paths.extend(node.owned_paths)
        capacity -= 1
        intents.append(SchedulerIntent("dispatch", slice_id, None, "ready_and_available", {}))

    events = tuple(_event(intent, index, now) for index, intent in enumerate(intents, start=1))
    return SchedulerDecision(
        graph=graph,
        slices=slices,
        discovery_decisions=replenishment.decisions,
        admitted_slice_ids=replenishment.admitted_ids,
        intents=tuple(intents),
        events=events,
        dispatches=tuple(dispatches),
        deferred=tuple(deferred),
    )


def _verify_coordinator(state: SchedulerInput, now: datetime) -> None:
    lease = state.coordinator_lease
    if lease.owner != state.owner:
        raise ValueError("coordinator lease owner does not match tick owner")
    if lease.fencing_token != state.fencing_token:
        raise ValueError("coordinator fencing token is obsolete")
    if not lease.is_live(now):
        raise ValueError("coordinator lease is not live")


def _validate_references(state: SchedulerInput, known: set[str]) -> None:
    assignments = set()
    for lease in state.slice_leases:
        if lease.slice_id not in known:
            raise ValueError(f"slice lease references unknown slice: {lease.slice_id}")
        if lease.assignment_id in assignments:
            raise ValueError(f"duplicate assignment id: {lease.assignment_id}")
        assignments.add(lease.assignment_id)
    observed = set()
    for item in state.worker_observations:
        if item.slice_id not in known:
            raise ValueError(f"worker observation references unknown slice: {item.slice_id}")
        if item.assignment_id in observed:
            raise ValueError(f"duplicate worker observation: {item.assignment_id}")
        observed.add(item.assignment_id)


def _outcome_intent(observation: WorkerObservation) -> SchedulerIntent:
    assert observation.outcome is not None
    outcome = observation.outcome
    kind, reason = {
        "success": ("validate", "worker_report_ready"),
        "retryable": ("retry", "worker_retryable"),
        "blocked": ("open_blocker", "worker_blocked"),
        "supervision": ("request_supervision", "worker_requires_supervision"),
        "unavailable": ("open_blocker", "expected_capability_unavailable"),
        "malformed": ("retry", "worker_report_malformed"),
    }[outcome.kind]
    return SchedulerIntent(
        kind,
        observation.slice_id,
        observation.assignment_id,
        reason,
        {"outcome_kind": outcome.kind, "outcome_reason": outcome.reason},
    )


def _event(intent: SchedulerIntent, ordinal: int, now: datetime) -> SchedulerEvent:
    identity = {
        "ordinal": ordinal,
        "kind": intent.kind,
        "slice_id": intent.slice_id,
        "reference_id": intent.reference_id,
        "reason": intent.reason,
        "details": dict(intent.details),
        "recorded_at": now.isoformat(),
    }
    encoded = json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
    return SchedulerEvent(
        f"scheduler-event:{hashlib.sha256(encoded).hexdigest()}",
        ordinal,
        intent.kind,
        intent.slice_id,
        now,
    )


def _aware(value: datetime, label: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware")


def _path_parts(value: str) -> Tuple[str, ...]:
    path = PurePosixPath(value)
    if path.is_absolute() or not path.parts or ".." in path.parts:
        raise ValueError("owned path must be a confined relative path")
    return tuple(part.casefold() for part in path.parts)


def _conflicts(paths: Sequence[str], occupied: Sequence[str]) -> bool:
    return any(_overlap(left, right) for left in paths for right in occupied)


def _overlap(left: str, right: str) -> bool:
    left_parts = _path_parts(left)
    right_parts = _path_parts(right)
    common = min(len(left_parts), len(right_parts))
    return left_parts[:common] == right_parts[:common]
