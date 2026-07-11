from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
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
    for event in stream:
        if event.run_id != run_id:
            raise ValueError("event stream contains multiple run IDs")
        payload = event.payload
        if event.event_type == "run.started":
            status = str(payload.get("status", "active"))
            max_workers = int(payload.get("max_workers", 1))
        elif event.event_type == "run.status_changed":
            status = str(payload["status"])
        elif event.event_type == "slice.registered":
            slice_id = str(payload["slice_id"])
            if slice_id in slices:
                raise ValueError(f"duplicate slice registration: {slice_id}")
            slices[slice_id] = dict(payload)
        elif event.event_type == "slice.status_changed":
            slice_id = str(payload["slice_id"])
            if slice_id not in slices:
                raise ValueError(f"unknown slice in status event: {slice_id}")
            slices[slice_id] = {**slices[slice_id], **payload}
        elif event.event_type == "blocker.recorded":
            blockers.append(dict(payload))
        elif event.event_type == "stop.requested":
            stop_requests.append(dict(payload))
    return CoordinatorProjection(
        schema_version=1,
        run_id=run_id,
        revision=stream[-1].projection_revision,
        status=status,
        max_workers=max_workers,
        slices=slices,
        blockers=blockers,
        stop_requests=stop_requests,
        last_event_hash=stream[-1].event_hash,
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
