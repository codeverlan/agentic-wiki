from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict

import pytest

from memwiki.coordinator_checkpoints import (
    CheckpointCorruptionError,
    CheckpointManager,
)
from memwiki.coordinator_events import CoordinatorEvent
from memwiki.coordinator_journal import CoordinatorJournal
from memwiki.coordinator_projection import reduce_events


def _event(sequence: int, prior_hash: str | None, event_type: str, payload: Dict[str, Any]) -> CoordinatorEvent:
    return CoordinatorEvent.create(
        run_id="run-1",
        sequence=sequence,
        projection_revision=sequence,
        event_type=event_type,
        payload=payload,
        actor={"kind": "coordinator"},
        idempotency_key=f"key-{sequence}",
        prior_hash=prior_hash,
        occurred_at=f"2026-07-11T12:00:0{sequence}+00:00",
    )


def _journal(path: Path, count: int = 2) -> CoordinatorJournal:
    journal = CoordinatorJournal(path)
    first = _event(1, None, "run.started", {"status": "active", "max_workers": 6})
    journal.append_event(first)
    if count > 1:
        journal.append_event(
            _event(2, first.event_hash, "slice.registered", {"slice_id": "AC-001", "status": "ready"})
        )
    return journal


def _state() -> Dict[str, Any]:
    return {
        "authority": {"epoch": 4, "fencing_token": "fence-4"},
        "active_attempts": [{"attempt_id": "attempt-1", "slice_id": "AC-001"}],
        "active_leases": [{"lease_id": "lease-1", "attempt_id": "attempt-1"}],
        "in_flight_receipts": [{"receipt_id": "receipt-1", "status": "started"}],
        "budgets": {"tokens": 900, "cost_micros": 12},
        "git_state": {"head": "abc123", "branch": "codex/test"},
        "continuation_cursor": {"next_slice": "AC-002"},
    }


def test_checkpoint_round_trip_contains_required_durable_state(tmp_path: Path) -> None:
    journal = _journal(tmp_path / "journal.log")
    manager = CheckpointManager(tmp_path / "checkpoints", journal)
    projection = reduce_events(journal.read_events(run_id="run-1"))

    checkpoint = manager.create(projection=projection, **_state())
    loaded = manager.load_verified()

    assert loaded == checkpoint
    assert checkpoint.journal_offset == journal.path.stat().st_size
    assert checkpoint.journal_hash == projection.last_event_hash
    assert checkpoint.projection_revision == 2
    assert checkpoint.projection_hash == hashlib.sha256(projection.to_json_bytes()).hexdigest()
    assert loaded.active_attempts[0]["attempt_id"] == "attempt-1"
    assert not (tmp_path / "checkpoints" / "checkpoint.json.tmp").exists()


@pytest.mark.parametrize("field", ["journal_offset", "journal_hash", "projection_hash", "run_id"])
def test_refuses_tampered_checkpoint(tmp_path: Path, field: str) -> None:
    journal = _journal(tmp_path / "journal.log")
    manager = CheckpointManager(tmp_path / "checkpoints", journal)
    manager.create(projection=reduce_events(journal.read_events()), **_state())
    payload = json.loads(manager.checkpoint_path.read_text())
    payload[field] = 999 if field == "journal_offset" else "tampered"
    manager.checkpoint_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(CheckpointCorruptionError):
        manager.load_verified()


def test_compaction_archives_verified_prefix_and_resume_replays_tail(tmp_path: Path) -> None:
    journal = _journal(tmp_path / "journal.log")
    manager = CheckpointManager(tmp_path / "checkpoints", journal)
    checkpoint = manager.create(projection=reduce_events(journal.read_events()), **_state())
    manager.compact(checkpoint)
    second = journal.read_events()[0] if journal.read_events() else None
    assert second is None

    prior = checkpoint.journal_hash
    journal.append_event(
        _event(3, prior, "slice.status_changed", {"slice_id": "AC-001", "status": "completed"})
    )
    resumed = CheckpointManager(tmp_path / "checkpoints", journal).resume(run_id="run-1")

    assert resumed.acknowledged is True
    assert resumed.replayed_events == 1
    assert resumed.projection.revision == 3
    assert resumed.projection.slices["AC-001"]["status"] == "completed"
    assert len(list(manager.archive_dir.glob("*.journal"))) == 1


def test_repeated_checkpoint_and_compaction_preserve_complete_chain(tmp_path: Path) -> None:
    journal = _journal(tmp_path / "journal.log")
    manager = CheckpointManager(tmp_path / "checkpoints", journal)
    first = manager.create(projection=reduce_events(journal.read_events()), **_state())
    manager.compact(first)
    journal.append_event(
        _event(3, first.journal_hash, "slice.status_changed", {"slice_id": "AC-001", "status": "completed"})
    )
    current = manager.resume(run_id="run-1").projection

    second = manager.create(projection=current, **_state())
    manager.compact(second)
    resumed = manager.resume(run_id="run-1")

    assert second.journal_offset > first.journal_offset
    assert resumed.projection.to_dict() == current.to_dict()
    assert resumed.replayed_events == 0
    assert len(list(manager.archive_dir.glob("*.journal"))) == 2


@pytest.mark.parametrize("crash_point", ["after_archive", "after_live_replace"])
def test_resume_survives_crash_at_each_compaction_boundary(tmp_path: Path, crash_point: str) -> None:
    journal = _journal(tmp_path / "journal.log")
    manager = CheckpointManager(tmp_path / "checkpoints", journal)
    checkpoint = manager.create(projection=reduce_events(journal.read_events()), **_state())

    def crash(point: str) -> None:
        if point == crash_point:
            raise RuntimeError("simulated crash")

    with pytest.raises(RuntimeError, match="simulated crash"):
        manager.compact(checkpoint, crash_hook=crash)

    resumed = CheckpointManager(tmp_path / "checkpoints", journal).resume(run_id="run-1")
    assert resumed.projection.to_dict() == reduce_events(_journal_events_from_fresh_copy(tmp_path)).to_dict()
    assert resumed.replayed_events == 0


def _journal_events_from_fresh_copy(tmp_path: Path) -> list[CoordinatorEvent]:
    archive = next((tmp_path / "checkpoints" / "archive").glob("*.journal"))
    fresh = tmp_path / "fresh.log"
    fresh.write_bytes(archive.read_bytes())
    return CoordinatorJournal(fresh).read_events(run_id="run-1")


def test_compaction_refuses_changed_or_corrupt_prefix(tmp_path: Path) -> None:
    journal = _journal(tmp_path / "journal.log")
    manager = CheckpointManager(tmp_path / "checkpoints", journal)
    checkpoint = manager.create(projection=reduce_events(journal.read_events()), **_state())
    raw = bytearray(journal.path.read_bytes())
    raw[12] ^= 1
    journal.path.write_bytes(raw)

    with pytest.raises(CheckpointCorruptionError):
        manager.compact(checkpoint)


def test_resume_refuses_corrupt_archive_and_projection_mismatch(tmp_path: Path) -> None:
    journal = _journal(tmp_path / "journal.log")
    manager = CheckpointManager(tmp_path / "checkpoints", journal)
    checkpoint = manager.create(projection=reduce_events(journal.read_events()), **_state())
    archive = manager.compact(checkpoint)
    archive.write_bytes(archive.read_bytes()[:-1] + b"x")

    with pytest.raises(CheckpointCorruptionError):
        manager.resume(run_id="run-1")


def test_atomic_checkpoint_crash_preserves_previous_checkpoint(tmp_path: Path) -> None:
    journal = _journal(tmp_path / "journal.log")
    manager = CheckpointManager(tmp_path / "checkpoints", journal)
    original = manager.create(projection=reduce_events(journal.read_events()), **_state())

    def fail_replace(_source: Path, _target: Path) -> None:
        raise OSError("simulated replace failure")

    with pytest.raises(OSError, match="replace failure"):
        manager.create(
            projection=reduce_events(journal.read_events()),
            replace=fail_replace,
            **{**_state(), "continuation_cursor": {"next_slice": "AC-099"}},
        )

    assert manager.load_verified() == original


def test_checkpoint_rejects_non_serializable_or_mismatched_projection(tmp_path: Path) -> None:
    journal = _journal(tmp_path / "journal.log")
    manager = CheckpointManager(tmp_path / "checkpoints", journal)
    projection = reduce_events(journal.read_events())

    with pytest.raises(ValueError, match="JSON serializable"):
        manager.create(projection=projection, **{**_state(), "git_state": {"bad": object()}})

    with pytest.raises(ValueError, match="journal head"):
        manager.create(
            projection=projection.__class__(**{**projection.to_dict(), "last_event_hash": "wrong"}),
            **_state(),
        )
