from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

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
        self.journal = CoordinatorJournal(self.directory / "events.journal")
        self.cache = ProjectionCache(self.directory / "projection.json")

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
        event, appended = self.journal.append_command(
            run_id=run_id,
            event_type=event_type,
            payload=payload,
            actor=actor,
            idempotency_key=idempotency_key,
            occurred_at=occurred_at,
            correlation_id=correlation_id,
            causation_id=causation_id,
        )
        projection = self.cache.load_or_rebuild(self.journal, run_id=run_id)
        return CoordinatorCommandResult(event, appended, projection)

    def status(self, *, run_id: str) -> CoordinatorProjection:
        return self.cache.load_or_rebuild(self.journal, run_id=run_id)


__all__ = ["CoordinatorCommandGateway", "CoordinatorCommandResult"]
