from __future__ import annotations

import fcntl
import hashlib
import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from .coordinator_events import CoordinatorEvent, verify_event_chain
from .coordinator_journal import CoordinatorJournal
from .coordinator_projection import CoordinatorProjection, reduce_events


class CheckpointCorruptionError(ValueError):
    """Raised when checkpoint state cannot be proven against its journal."""


CrashHook = Callable[[str], None]
Replace = Callable[[Path, Path], None]


def _canonical_json(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError("checkpoint values must be JSON serializable") from exc


def _digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


@dataclass(frozen=True)
class CoordinatorCheckpoint:
    schema_version: int
    run_id: str
    journal_offset: int
    journal_hash: str
    journal_prefix_digest: str
    projection_revision: int
    projection_hash: str
    projection: Dict[str, Any]
    authority: Dict[str, Any]
    active_attempts: List[Dict[str, Any]]
    active_leases: List[Dict[str, Any]]
    in_flight_receipts: List[Dict[str, Any]]
    budgets: Dict[str, Any]
    git_state: Dict[str, Any]
    continuation_cursor: Dict[str, Any]
    checkpoint_hash: str

    def unsigned_dict(self) -> Dict[str, Any]:
        value = asdict(self)
        del value["checkpoint_hash"]
        return value

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: object) -> "CoordinatorCheckpoint":
        if not isinstance(value, dict):
            raise CheckpointCorruptionError("checkpoint must contain an object")
        fields = set(cls.__dataclass_fields__)
        if set(value) != fields:
            raise CheckpointCorruptionError("checkpoint fields do not match schema")
        try:
            checkpoint = cls(**value)
        except TypeError as exc:
            raise CheckpointCorruptionError("checkpoint fields are invalid") from exc
        checkpoint._validate_shape()
        if _digest(_canonical_json(checkpoint.unsigned_dict())) != checkpoint.checkpoint_hash:
            raise CheckpointCorruptionError("checkpoint hash does not match contents")
        return checkpoint

    def _validate_shape(self) -> None:
        if self.schema_version != 1:
            raise CheckpointCorruptionError("unsupported checkpoint schema version")
        if not self.run_id or self.journal_offset < 1 or self.projection_revision < 1:
            raise CheckpointCorruptionError("checkpoint identity or offsets are invalid")
        for value in (
            self.journal_hash,
            self.journal_prefix_digest,
            self.projection_hash,
            self.checkpoint_hash,
        ):
            if not isinstance(value, str) or len(value) != 64:
                raise CheckpointCorruptionError("checkpoint digest is invalid")
        if not isinstance(self.projection, dict):
            raise CheckpointCorruptionError("checkpoint projection is invalid")


@dataclass(frozen=True)
class ResumeAcknowledgement:
    acknowledged: bool
    checkpoint: CoordinatorCheckpoint
    projection: CoordinatorProjection
    replayed_events: int


class CheckpointManager:
    def __init__(self, directory: Path, journal: CoordinatorJournal) -> None:
        self.directory = directory
        self.journal = journal
        self.checkpoint_path = directory / "checkpoint.json"
        self.archive_dir = directory / "archive"

    def create(
        self,
        *,
        projection: CoordinatorProjection,
        authority: Dict[str, Any],
        active_attempts: List[Dict[str, Any]],
        active_leases: List[Dict[str, Any]],
        in_flight_receipts: List[Dict[str, Any]],
        budgets: Dict[str, Any],
        git_state: Dict[str, Any],
        continuation_cursor: Dict[str, Any],
        replace: Replace = os.replace,
    ) -> CoordinatorCheckpoint:
        raw = self._assembled_journal()
        events = self._events(raw)
        if not events or events[-1].event_hash != projection.last_event_hash:
            raise ValueError("projection does not match journal head")
        expected = reduce_events(events)
        if expected.to_json_bytes() != projection.to_json_bytes():
            raise ValueError("projection does not match journal head")
        projection_dict = projection.to_dict()
        unsigned: Dict[str, Any] = {
            "schema_version": 1,
            "run_id": projection.run_id,
            "journal_offset": len(raw),
            "journal_hash": events[-1].event_hash,
            "journal_prefix_digest": _digest(raw),
            "projection_revision": projection.revision,
            "projection_hash": _digest(projection.to_json_bytes()),
            "projection": projection_dict,
            "authority": authority,
            "active_attempts": active_attempts,
            "active_leases": active_leases,
            "in_flight_receipts": in_flight_receipts,
            "budgets": budgets,
            "git_state": git_state,
            "continuation_cursor": continuation_cursor,
        }
        normalized = json.loads(_canonical_json(unsigned))
        checkpoint = CoordinatorCheckpoint(**normalized, checkpoint_hash=_digest(_canonical_json(normalized)))
        self._atomic_json(self.checkpoint_path, checkpoint.to_dict(), replace=replace)
        return checkpoint

    def load_verified(self) -> CoordinatorCheckpoint:
        try:
            value = json.loads(self.checkpoint_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CheckpointCorruptionError("checkpoint is missing or invalid JSON") from exc
        checkpoint = CoordinatorCheckpoint.from_dict(value)
        projection = self._projection(checkpoint.projection)
        if _digest(projection.to_json_bytes()) != checkpoint.projection_hash:
            raise CheckpointCorruptionError("checkpoint projection hash mismatch")
        if projection.run_id != checkpoint.run_id or projection.revision != checkpoint.projection_revision:
            raise CheckpointCorruptionError("checkpoint projection identity mismatch")
        prefix, _ = self._logical_journal(checkpoint)
        self._verify_prefix(checkpoint, prefix)
        return checkpoint

    def compact(
        self,
        checkpoint: Optional[CoordinatorCheckpoint] = None,
        *,
        crash_hook: Optional[CrashHook] = None,
    ) -> Path:
        expected = checkpoint or self.load_verified()
        verified = self.load_verified()
        if verified != expected:
            raise CheckpointCorruptionError("checkpoint changed before compaction")
        self.archive_dir.mkdir(parents=True, exist_ok=True)
        archive = self._archive_path(expected)
        with self.journal.lock_path.open("a+b") as lock_stream:
            fcntl.flock(lock_stream.fileno(), fcntl.LOCK_EX)
            try:
                live = self.journal.path.read_bytes() if self.journal.path.exists() else b""
                logical = self._assembled_journal(live=live)
                if archive.exists():
                    prefix = archive.read_bytes()
                elif len(logical) >= expected.journal_offset:
                    prefix = logical[: expected.journal_offset]
                else:
                    raise CheckpointCorruptionError("journal is shorter than checkpoint offset")
                self._verify_prefix(expected, prefix)
                if not archive.exists():
                    self._atomic_bytes(archive, prefix)
                else:
                    self._verify_prefix(expected, archive.read_bytes())
                if crash_hook is not None:
                    crash_hook("after_archive")
                tail = logical[expected.journal_offset :]
                if tail and not self._tail_follows_checkpoint(tail, expected):
                    raise CheckpointCorruptionError("live journal does not follow checkpoint archive")
                self.journal._atomic_write(self.journal.path, tail)
                if crash_hook is not None:
                    crash_hook("after_live_replace")
                return archive
            finally:
                fcntl.flock(lock_stream.fileno(), fcntl.LOCK_UN)

    def resume(self, *, run_id: str) -> ResumeAcknowledgement:
        checkpoint = self.load_verified()
        if checkpoint.run_id != run_id:
            raise CheckpointCorruptionError("checkpoint run ID mismatch")
        prefix, tail = self._logical_journal(checkpoint)
        events = self._events(prefix + tail, run_id=run_id)
        prefix_events = self._events(prefix, run_id=run_id)
        saved_projection = self._projection(checkpoint.projection)
        if reduce_events(prefix_events).to_json_bytes() != saved_projection.to_json_bytes():
            raise CheckpointCorruptionError("checkpoint projection does not match journal prefix")
        projection = reduce_events(events)
        return ResumeAcknowledgement(
            acknowledged=True,
            checkpoint=checkpoint,
            projection=projection,
            replayed_events=len(events) - len(prefix_events),
        )

    def _logical_journal(self, checkpoint: CoordinatorCheckpoint) -> Tuple[bytes, bytes]:
        archive = self._archive_path(checkpoint)
        if archive.exists():
            prefix = archive.read_bytes()
        else:
            logical = self._assembled_journal()
            if len(logical) < checkpoint.journal_offset:
                raise CheckpointCorruptionError("journal is shorter than checkpoint offset")
            prefix = logical[: checkpoint.journal_offset]
        logical = self._assembled_journal()
        if not logical.startswith(prefix):
            raise CheckpointCorruptionError("journal archive is not a logical prefix")
        return prefix, logical[len(prefix) :]

    def _assembled_journal(self, *, live: Optional[bytes] = None) -> bytes:
        current = live
        if current is None:
            current = self.journal.path.read_bytes() if self.journal.path.exists() else b""
        archives = sorted(self.archive_dir.glob("*.journal"), key=self._archive_offset)
        if not archives:
            return current
        base = archives[-1].read_bytes()
        overlap = min(len(base), len(current))
        while overlap and base[-overlap:] != current[:overlap]:
            overlap -= 1
        return base + current[overlap:]

    @staticmethod
    def _archive_offset(path: Path) -> int:
        try:
            return int(path.name.split("-", 1)[0])
        except ValueError as exc:
            raise CheckpointCorruptionError("archive filename is invalid") from exc

    def _legacy_logical_journal(self, checkpoint: CoordinatorCheckpoint) -> Tuple[bytes, bytes]:
        live = self.journal.path.read_bytes() if self.journal.path.exists() else b""
        if len(live) < checkpoint.journal_offset:
            raise CheckpointCorruptionError("journal is shorter than checkpoint offset")
        return live[: checkpoint.journal_offset], live[checkpoint.journal_offset :]

    def _verify_prefix(self, checkpoint: CoordinatorCheckpoint, prefix: bytes) -> None:
        if len(prefix) != checkpoint.journal_offset or _digest(prefix) != checkpoint.journal_prefix_digest:
            raise CheckpointCorruptionError("journal prefix does not match checkpoint")
        events = self._events(prefix, run_id=checkpoint.run_id)
        if not events or events[-1].event_hash != checkpoint.journal_hash:
            raise CheckpointCorruptionError("journal hash does not match checkpoint")

    def _tail_follows_checkpoint(self, tail: bytes, checkpoint: CoordinatorCheckpoint) -> bool:
        records, error = self.journal._scan(tail)
        if error is not None or not records:
            return False
        try:
            first = CoordinatorEvent.from_dict(records[0])
        except ValueError:
            return False
        return first.prior_hash == checkpoint.journal_hash

    def _events(self, raw: bytes, *, run_id: Optional[str] = None) -> List[CoordinatorEvent]:
        records, error = self.journal._scan(raw)
        if error is not None:
            raise CheckpointCorruptionError(str(error)) from error
        try:
            events = [CoordinatorEvent.from_dict(record) for record in records]
            verify_event_chain(events, run_id=run_id)
        except ValueError as exc:
            raise CheckpointCorruptionError("journal event chain is invalid") from exc
        return events

    def _archive_path(self, checkpoint: CoordinatorCheckpoint) -> Path:
        name = f"{checkpoint.journal_offset:020d}-{checkpoint.journal_prefix_digest}.journal"
        return self.archive_dir / name

    @staticmethod
    def _projection(value: Dict[str, Any]) -> CoordinatorProjection:
        try:
            return CoordinatorProjection(**value)
        except TypeError as exc:
            raise CheckpointCorruptionError("checkpoint projection schema is invalid") from exc

    def _atomic_json(self, path: Path, value: object, *, replace: Replace) -> None:
        self._atomic_bytes(path, _canonical_json(value) + b"\n", replace=replace)

    @staticmethod
    def _atomic_bytes(path: Path, content: bytes, *, replace: Replace = os.replace) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + ".tmp")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            written = 0
            while written < len(content):
                written += os.write(descriptor, content[written:])
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        replace(temporary, path)
        parent = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(parent)
        finally:
            os.close(parent)
