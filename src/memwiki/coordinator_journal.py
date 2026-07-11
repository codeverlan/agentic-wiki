from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .coordinator_events import CoordinatorEvent, verify_event_chain


class TornJournalError(ValueError):
    def __init__(self, message: str, *, valid_offset: int = 0, tail_offset: int = 0) -> None:
        super().__init__(message)
        self.valid_offset = valid_offset
        self.tail_offset = tail_offset


def _encode_record(record: Dict[str, Any]) -> bytes:
    try:
        payload = json.dumps(
            record,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError("journal record must be JSON serializable") from exc
    return f"{len(payload):08d}:".encode("ascii") + payload + b"\n"


class CoordinatorJournal:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.lock_path = path.with_suffix(path.suffix + ".lock")

    def append_event(self, event: CoordinatorEvent) -> None:
        self.append_record(event.to_dict())

    def append_record(self, record: Dict[str, Any]) -> None:
        framed = _encode_record(record)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.lock_path.open("a+b") as lock_stream:
            fcntl.flock(lock_stream.fileno(), fcntl.LOCK_EX)
            try:
                descriptor = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
                try:
                    written = 0
                    while written < len(framed):
                        written += os.write(descriptor, framed[written:])
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
                self._sync_parent()
            finally:
                fcntl.flock(lock_stream.fileno(), fcntl.LOCK_UN)

    def read_records(self) -> List[Dict[str, Any]]:
        if not self.path.exists():
            return []
        records, error = self._scan(self.path.read_bytes())
        if error is not None:
            raise error
        return records

    def read_events(self, *, run_id: Optional[str] = None) -> List[CoordinatorEvent]:
        events = [CoordinatorEvent.from_dict(record) for record in self.read_records()]
        verify_event_chain(events, run_id=run_id)
        return events

    def quarantine_torn_tail(self) -> Optional[Path]:
        if not self.path.exists():
            return None
        with self.lock_path.open("a+b") as lock_stream:
            fcntl.flock(lock_stream.fileno(), fcntl.LOCK_EX)
            try:
                raw = self.path.read_bytes()
                _, error = self._scan(raw)
                if error is None:
                    return None
                if "invalid JSON" in str(error) and error.tail_offset < len(raw):
                    raise error
                tail = raw[error.valid_offset :]
                quarantine = self.path.with_name(self.path.name + ".torn")
                self._atomic_write(quarantine, tail)
                self._atomic_write(self.path, raw[: error.valid_offset])
                return quarantine
            finally:
                fcntl.flock(lock_stream.fileno(), fcntl.LOCK_UN)

    def _scan(self, raw: bytes) -> Tuple[List[Dict[str, Any]], Optional[TornJournalError]]:
        records: List[Dict[str, Any]] = []
        offset = 0
        while offset < len(raw):
            start = offset
            if len(raw) - offset < 9 or raw[offset + 8 : offset + 9] != b":":
                return records, TornJournalError("torn journal frame header", valid_offset=start, tail_offset=offset)
            length_bytes = raw[offset : offset + 8]
            if not length_bytes.isdigit():
                return records, TornJournalError("invalid journal frame length", valid_offset=start, tail_offset=offset)
            length = int(length_bytes)
            payload_start = offset + 9
            payload_end = payload_start + length
            if payload_end >= len(raw) or raw[payload_end : payload_end + 1] != b"\n":
                return records, TornJournalError(
                    "torn journal frame payload",
                    valid_offset=start,
                    tail_offset=payload_start,
                )
            try:
                decoded = json.loads(raw[payload_start:payload_end])
            except (UnicodeDecodeError, json.JSONDecodeError):
                return records, TornJournalError(
                    "invalid JSON in complete journal record",
                    valid_offset=start,
                    tail_offset=payload_start,
                )
            if not isinstance(decoded, dict):
                return records, TornJournalError(
                    "journal record must contain an object",
                    valid_offset=start,
                    tail_offset=payload_start,
                )
            records.append(decoded)
            offset = payload_end + 1
        return records, None

    def _atomic_write(self, path: Path, content: bytes) -> None:
        temporary = path.with_name(path.name + ".tmp")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            written = 0
            while written < len(content):
                written += os.write(descriptor, content[written:])
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        os.replace(temporary, path)
        self._sync_parent()

    def _sync_parent(self) -> None:
        descriptor = os.open(self.path.parent, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
