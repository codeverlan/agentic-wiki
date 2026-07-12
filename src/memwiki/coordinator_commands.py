from __future__ import annotations

import fcntl
import hashlib
import json
import os
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .coordinator_events import CoordinatorEvent
from .coordinator_journal import CoordinatorJournal
from .coordinator_projection import CoordinatorProjection, ProjectionCache


@dataclass(frozen=True)
class CoordinatorCommandResult:
    event: CoordinatorEvent
    appended: bool
    projection: CoordinatorProjection

    def to_dict(self) -> Dict[str, Any]:
        return {
            "appended": self.appended,
            "event": self.event.to_dict(),
            "projection": self.projection.to_dict(),
        }


class CoordinatorCommandGateway:
    """Atomic public mutation boundary for the canonical coordinator journal."""

    def __init__(self, workspace: Path | str) -> None:
        self.workspace = Path(workspace).expanduser().resolve()
        self.directory = self.workspace / ".memwiki" / "coordinator"
        self.registry_path = self.directory / "runs.json"
        self.registry_lock_path = self.directory / "runs.json.lock"

    @staticmethod
    def _run_key(run_id: str) -> str:
        if not isinstance(run_id, str) or not run_id.strip():
            raise ValueError("run_id must be a non-empty string")
        return hashlib.sha256(run_id.encode("utf-8")).hexdigest()[:24]

    def _legacy_run_id(self) -> Optional[str]:
        journal = CoordinatorJournal(self.directory / "events.journal")
        if not journal.path.exists():
            return None
        events = journal.read_events()
        if not events:
            return None
        run_ids = {event.run_id for event in events}
        if len(run_ids) != 1:
            raise ValueError("legacy coordinator journal contains multiple run IDs")
        return next(iter(run_ids))

    def _surfaces(self, run_id: str) -> Tuple[CoordinatorJournal, ProjectionCache, str]:
        if self._legacy_run_id() == run_id:
            return (
                CoordinatorJournal(self.directory / "events.journal"),
                ProjectionCache(self.directory / "projection.json"),
                ".",
            )
        run_key = self._run_key(run_id)
        relative = Path("runs") / run_key
        return (
            CoordinatorJournal(self.directory / relative / "events.journal"),
            ProjectionCache(self.directory / relative / "projection.json"),
            relative.as_posix(),
        )

    def _record_run(
        self, projection: CoordinatorProjection, relative: str, *, updated_at: Optional[str] = None
    ) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        with self.registry_lock_path.open("a+b") as lock_stream:
            fcntl.flock(lock_stream.fileno(), fcntl.LOCK_EX)
            try:
                try:
                    payload = json.loads(self.registry_path.read_text(encoding="utf-8"))
                except (FileNotFoundError, json.JSONDecodeError):
                    payload = {"schema_version": 1, "runs": []}
                if not isinstance(payload, dict) or not isinstance(payload.get("runs"), list):
                    raise ValueError("coordinator run registry is invalid")
                records = [item for item in payload["runs"] if isinstance(item, dict)]
                prior = next(
                    (item for item in records if item.get("run_id") == projection.run_id), None
                )
                observed_at = updated_at or datetime.now(timezone.utc).isoformat()
                if prior is not None and prior.get("last_event_hash") == projection.last_event_hash:
                    observed_at = str(prior.get("updated_at", observed_at))
                current = {
                    "run_id": projection.run_id,
                    "path": relative,
                    "revision": projection.revision,
                    "status": projection.status,
                    "last_event_hash": projection.last_event_hash,
                    "updated_at": observed_at,
                }
                records = [item for item in records if item.get("run_id") != projection.run_id]
                records.append(current)
                content = json.dumps(
                    {"schema_version": 1, "runs": sorted(records, key=lambda item: str(item["run_id"]))},
                    ensure_ascii=True,
                    indent=2,
                    sort_keys=True,
                ).encode("utf-8") + b"\n"
                temporary = self.registry_path.with_name(self.registry_path.name + ".tmp")
                descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                try:
                    written = 0
                    while written < len(content):
                        written += os.write(descriptor, content[written:])
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
                os.replace(temporary, self.registry_path)
            finally:
                fcntl.flock(lock_stream.fileno(), fcntl.LOCK_UN)

    def append(
        self,
        *,
        run_id: str,
        event_type: str,
        payload: Dict[str, Any],
        actor: Dict[str, Any],
        idempotency_key: str,
        occurred_at: Optional[str] = None,
        correlation_id: Optional[str] = None,
        causation_id: Optional[str] = None,
    ) -> CoordinatorCommandResult:
        journal, cache, relative = self._surfaces(run_id)
        event, appended = journal.append_command(
            run_id=run_id,
            event_type=event_type,
            payload=payload,
            actor=actor,
            idempotency_key=idempotency_key,
            occurred_at=occurred_at,
            correlation_id=correlation_id,
            causation_id=causation_id,
        )
        projection = cache.load_or_rebuild(journal, run_id=run_id)
        self._record_run(projection, relative, updated_at=event.occurred_at)
        return CoordinatorCommandResult(event, appended, projection)

    def status(self, *, run_id: str) -> CoordinatorProjection:
        journal, cache, relative = self._surfaces(run_id)
        projection = cache.load_or_rebuild(journal, run_id=run_id)
        events = journal.read_events(run_id=run_id)
        self._record_run(projection, relative, updated_at=events[-1].occurred_at)
        return projection

    def rebuild(self, *, run_id: str) -> CoordinatorProjection:
        """Discard a disposable run projection and rebuild it from its journal."""
        journal, cache, _ = self._surfaces(run_id)
        with suppress(FileNotFoundError):
            cache.path.unlink()
        return self.status(run_id=run_id)

    def event_count(self, *, run_id: str) -> int:
        journal, _, _ = self._surfaces(run_id)
        return len(journal.read_events(run_id=run_id))

    def runs(self) -> List[Dict[str, Any]]:
        """Return the project-local run registry, newest observation first."""
        legacy_run_id = self._legacy_run_id()
        try:
            payload = json.loads(self.registry_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            if legacy_run_id is None:
                return []
            self.status(run_id=legacy_run_id)
            payload = json.loads(self.registry_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or not isinstance(payload.get("runs"), list):
            raise ValueError("coordinator run registry is invalid")
        if legacy_run_id is not None and not any(
            isinstance(item, dict) and item.get("run_id") == legacy_run_id for item in payload["runs"]
        ):
            self.status(run_id=legacy_run_id)
            payload = json.loads(self.registry_path.read_text(encoding="utf-8"))
        records = [dict(item) for item in payload["runs"] if isinstance(item, dict)]
        return sorted(records, key=lambda item: str(item.get("updated_at", "")), reverse=True)


__all__ = ["CoordinatorCommandGateway", "CoordinatorCommandResult"]
