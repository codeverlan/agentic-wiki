from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from .coordinator_events import CoordinatorEvent, verify_event_chain
from .coordinator_journal import CoordinatorJournal


@dataclass(frozen=True)
class CoordinatorProjection:
    schema_version: int
    run_id: str
    revision: int
    status: str
    max_workers: int
    slices: Dict[str, Dict[str, Any]]
    blockers: List[Dict[str, Any]]
    stop_requests: List[Dict[str, Any]]
    last_event_hash: Optional[str]
    assignments: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    workers: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    worker_reports: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    runtime_observations: List[Dict[str, Any]] = field(default_factory=list)
    resource_observations: List[Dict[str, Any]] = field(default_factory=list)
    memory_proposals: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    validations: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    completion_evaluations: List[Dict[str, Any]] = field(default_factory=list)
    supervision_requests: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    incidents: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    capability_invocations: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_json_bytes(self) -> bytes:
        return json.dumps(
            self.to_dict(),
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")


def reduce_events(events: Iterable[CoordinatorEvent]) -> CoordinatorProjection:
    stream = list(events)
    verify_event_chain(stream)
    if not stream:
        raise ValueError("cannot reduce an empty coordinator event stream")
    run_id = stream[0].run_id
    status = "proposed"
    max_workers = 1
    slices: Dict[str, Dict[str, Any]] = {}
    blockers: List[Dict[str, Any]] = []
    stop_requests: List[Dict[str, Any]] = []
    assignments: Dict[str, Dict[str, Any]] = {}
    workers: Dict[str, Dict[str, Any]] = {}
    worker_reports: Dict[str, Dict[str, Any]] = {}
    runtime_observations: List[Dict[str, Any]] = []
    resource_observations: List[Dict[str, Any]] = []
    memory_proposals: Dict[str, Dict[str, Any]] = {}
    validations: Dict[str, Dict[str, Any]] = {}
    completion_evaluations: List[Dict[str, Any]] = []
    supervision_requests: Dict[str, Dict[str, Any]] = {}
    incidents: Dict[str, Dict[str, Any]] = {}
    capability_invocations: Dict[str, Dict[str, Any]] = {}
    schema_version = 1
    for event in stream:
        if event.run_id != run_id:
            raise ValueError("event stream contains multiple run IDs")
        payload = event.payload
        if event.event_type in {
            "slice.proposed",
            "slice.admitted",
            "slice.assigned",
            "slice.transitioned",
            "worker.started",
            "worker.heartbeat",
            "worker.reported",
            "worker.released",
            "runtime.observed",
            "resource.observed",
            "memory.proposed",
            "memory.dispositioned",
            "validation.recorded",
            "completion.evaluated",
            "supervision.requested",
            "supervision.dispositioned",
            "incident.recorded",
            "capability.recorded",
            "run.completed",
        }:
            schema_version = 2
        if event.event_type == "run.started":
            status = str(payload.get("status", "active"))
            max_workers = int(payload.get("max_workers", 1))
        elif event.event_type == "run.status_changed":
            status = str(payload["status"])
        elif event.event_type in {"slice.registered", "slice.proposed"}:
            slice_id = str(payload["slice_id"])
            if slice_id in slices:
                raise ValueError(f"duplicate slice registration: {slice_id}")
            slices[slice_id] = dict(payload)
        elif event.event_type in {"slice.status_changed", "slice.admitted", "slice.transitioned"}:
            slice_id = str(payload["slice_id"])
            if slice_id not in slices:
                raise ValueError(f"unknown slice in status event: {slice_id}")
            slices[slice_id] = {**slices[slice_id], **payload}
        elif event.event_type == "slice.assigned":
            slice_id = str(payload["slice_id"])
            if slice_id not in slices:
                raise ValueError(f"unknown slice in assignment event: {slice_id}")
            assignments[slice_id] = dict(payload)
            slices[slice_id] = {**slices[slice_id], **payload}
        elif event.event_type == "worker.started":
            worker_id = str(payload["worker_id"])
            workers[worker_id] = {**workers.get(worker_id, {}), **payload, "status": "active"}
        elif event.event_type == "worker.heartbeat":
            worker_id = str(payload["worker_id"])
            workers[worker_id] = {**workers.get(worker_id, {}), **payload}
        elif event.event_type == "worker.reported":
            report_id = str(payload["report_id"])
            worker_id = str(payload["worker_id"])
            worker_reports[report_id] = dict(payload)
            workers[worker_id] = {**workers.get(worker_id, {}), **payload}
        elif event.event_type == "worker.released":
            worker_id = str(payload["worker_id"])
            workers[worker_id] = {**workers.get(worker_id, {}), **payload, "status": "released"}
        elif event.event_type == "runtime.observed":
            runtime_observations.append(dict(payload))
        elif event.event_type == "resource.observed":
            resource_observations.append(dict(payload))
        elif event.event_type == "memory.proposed":
            memory_proposals[str(payload["proposal_id"])] = dict(payload)
        elif event.event_type == "memory.dispositioned":
            proposal_id = str(payload["proposal_id"])
            if proposal_id not in memory_proposals:
                raise ValueError(f"unknown memory proposal: {proposal_id}")
            memory_proposals[proposal_id] = {**memory_proposals[proposal_id], **payload}
        elif event.event_type == "validation.recorded":
            validations[str(payload["validation_id"])] = dict(payload)
        elif event.event_type == "completion.evaluated":
            completion_evaluations.append(dict(payload))
        elif event.event_type == "supervision.requested":
            supervision_requests[str(payload["request_id"])] = dict(payload)
        elif event.event_type == "supervision.dispositioned":
            request_id = str(payload["request_id"])
            if request_id not in supervision_requests:
                raise ValueError(f"unknown supervision request: {request_id}")
            supervision_requests[request_id] = {**supervision_requests[request_id], **payload}
        elif event.event_type == "incident.recorded":
            incidents[str(payload["incident_id"])] = dict(payload)
        elif event.event_type == "capability.recorded":
            capability_invocations[str(payload["invocation_id"])] = dict(payload)
        elif event.event_type == "blocker.recorded":
            blockers.append(dict(payload))
        elif event.event_type == "stop.requested":
            stop_requests.append(dict(payload))
        elif event.event_type == "run.completed":
            status = str(payload.get("status", "completed"))
    return CoordinatorProjection(
        schema_version=schema_version,
        run_id=run_id,
        revision=stream[-1].projection_revision,
        status=status,
        max_workers=max_workers,
        slices=slices,
        blockers=blockers,
        stop_requests=stop_requests,
        last_event_hash=stream[-1].event_hash,
        assignments=assignments,
        workers=workers,
        worker_reports=worker_reports,
        runtime_observations=runtime_observations,
        resource_observations=resource_observations,
        memory_proposals=memory_proposals,
        validations=validations,
        completion_evaluations=completion_evaluations,
        supervision_requests=supervision_requests,
        incidents=incidents,
        capability_invocations=capability_invocations,
    )


class ProjectionCache:
    def __init__(self, path: Path) -> None:
        self.path = path

    def load_or_rebuild(self, journal: CoordinatorJournal, *, run_id: str) -> CoordinatorProjection:
        events = journal.read_events(run_id=run_id)
        expected_hash = events[-1].event_hash if events else None
        cached = self._read(expected_hash)
        if cached is not None:
            return cached
        projection = reduce_events(events)
        self._write(projection)
        return projection

    def _read(self, expected_hash: Optional[str]) -> Optional[CoordinatorProjection]:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            if payload.pop("journal_hash") != expected_hash:
                return None
            projection = CoordinatorProjection(**payload)
            if projection.last_event_hash != expected_hash:
                return None
            return projection
        except (FileNotFoundError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            return None

    def _write(self, projection: CoordinatorProjection) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {**projection.to_dict(), "journal_hash": projection.last_event_hash}
        content = json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True).encode("utf-8") + b"\n"
        temporary = self.path.with_name(self.path.name + ".tmp")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(descriptor, content)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        os.replace(temporary, self.path)
        parent = os.open(self.path.parent, os.O_RDONLY)
        try:
            os.fsync(parent)
        finally:
            os.close(parent)
