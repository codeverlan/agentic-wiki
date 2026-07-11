from __future__ import annotations

import json
import multiprocessing
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from memwiki.coordinator_checkpoints import CheckpointManager
from memwiki.coordinator_completion import evaluate_completion
from memwiki.coordinator_events import CoordinatorEvent
from memwiki.coordinator_journal import CoordinatorJournal, TornJournalError
from memwiki.coordinator_lease import CoordinatorLease, LeaseIdentity
from memwiki.coordinator_migrations import (
    ArtifactPaths,
    CoordinatorMigrationManager,
    MigrationRegistry,
    MigrationStep,
)
from memwiki.coordinator_models import SliceRecord
from memwiki.coordinator_projection import reduce_events
from memwiki.coordinator_scheduler import SchedulerInput, reconciliation_tick

NOW = datetime(2026, 7, 11, 12, 0, tzinfo=timezone.utc)


def _append_records(path: str, worker: int, count: int) -> None:
    journal = CoordinatorJournal(Path(path))
    for ordinal in range(count):
        journal.append_record({"worker": worker, "ordinal": ordinal, "schema_version": 1})


def _event(sequence: int, prior_hash: str | None, event_type: str, payload: dict[str, object]) -> CoordinatorEvent:
    return CoordinatorEvent.create(
        run_id="fault-run",
        sequence=sequence,
        projection_revision=sequence,
        event_type=event_type,
        payload=payload,
        actor={"kind": "coordinator"},
        idempotency_key=f"fault-{sequence}",
        prior_hash=prior_hash,
        occurred_at=f"2026-07-11T12:00:{sequence:02d}+00:00",
    )


def test_six_process_concurrent_append_loses_no_records(tmp_path: Path) -> None:
    path = tmp_path / "concurrent.journal"
    context = multiprocessing.get_context("spawn")
    workers = [context.Process(target=_append_records, args=(str(path), worker, 30)) for worker in range(6)]
    for process in workers:
        process.start()
    for process in workers:
        process.join(timeout=20)
        assert process.exitcode == 0

    records = CoordinatorJournal(path).read_records()
    identities = {(record["worker"], record["ordinal"]) for record in records}
    assert len(records) == 180
    assert len(identities) == 180


@pytest.mark.parametrize("tail", [b"0000", b'00000030:{"partial":', b"XXXXXXXX:bad\n"])
def test_torn_tail_is_detected_and_complete_prefix_is_quarantined(tmp_path: Path, tail: bytes) -> None:
    journal = CoordinatorJournal(tmp_path / "torn.journal")
    journal.append_record({"schema_version": 1, "safe": True})
    prefix = journal.path.read_bytes()
    with journal.path.open("ab") as stream:
        stream.write(tail)

    with pytest.raises(TornJournalError):
        journal.read_records()
    quarantine = journal.quarantine_torn_tail()
    assert quarantine is not None
    assert journal.path.read_bytes() == prefix
    assert quarantine.read_bytes() == tail


def test_stale_coordinator_fencing_never_produces_dispatch_intents() -> None:
    owner = LeaseIdentity("host", 43, "coordinator")
    lease = CoordinatorLease(owner, 3, 10, "active", NOW, NOW, NOW + timedelta(minutes=1))
    state = SchedulerInput(
        slices=(SliceRecord("S-1", "slice", "waiting", [], ["src/one"], True),),
        slice_leases=(),
        worker_observations=(),
        discovery_proposals=(),
        coordinator_lease=lease,
        owner=owner,
        fencing_token=9,
        max_workers=6,
    )
    with pytest.raises(ValueError, match="fencing token"):
        reconciliation_tick(state, now=NOW)


@pytest.mark.parametrize("crash_point", ["after_archive", "after_live_replace"])
def test_checkpoint_restart_matches_uninterrupted_projection(tmp_path: Path, crash_point: str) -> None:
    journal = CoordinatorJournal(tmp_path / "events.journal")
    first = _event(1, None, "run.started", {"status": "active", "max_workers": 6})
    second = _event(2, first.event_hash, "slice.registered", {"slice_id": "S-1", "status": "ready"})
    journal.append_event(first)
    journal.append_event(second)
    expected = reduce_events((first, second))
    manager = CheckpointManager(tmp_path / "checkpoints", journal)
    checkpoint = manager.create(
        projection=expected,
        authority={"fencing_token": 1},
        active_attempts=[],
        active_leases=[],
        in_flight_receipts=[],
        budgets={},
        git_state={},
        continuation_cursor={},
    )

    def crash(point: str) -> None:
        if point == crash_point:
            raise RuntimeError("injected crash")

    with pytest.raises(RuntimeError, match="injected crash"):
        manager.compact(checkpoint, crash_hook=crash)
    resumed = CheckpointManager(tmp_path / "checkpoints", journal).resume(run_id="fault-run")
    assert resumed.projection.to_json_bytes() == expected.to_json_bytes()


def test_interrupted_migration_preserves_original_and_backup(tmp_path: Path) -> None:
    paths = ArtifactPaths(
        journal=tmp_path / "events.journal",
        state=tmp_path / "state.json",
        checkpoint=tmp_path / "checkpoint.json",
        projection=tmp_path / "projection.json",
        evidence_directory=tmp_path / "evidence",
        backup_directory=tmp_path / "backups",
    )
    registry = MigrationRegistry(current_versions={"events": 2, "state": 2, "checkpoints": 2})
    registry.register(
        MigrationStep("state", 1, 2, lambda value: {**value, "schema_version": 2}, "upgrade")
    )
    manager = CoordinatorMigrationManager(paths, registry)
    original = b'{"schema_version":1,"value":"preserve"}\n'
    paths.state.write_bytes(original)

    def fail_replace(_source: Path, _target: Path) -> None:
        raise OSError("disk fault")

    with pytest.raises(OSError, match="disk fault"):
        manager.apply(manager.plan(), occurred_at=NOW.isoformat(), replace=fail_replace)
    assert paths.state.read_bytes() == original
    assert next(paths.backup_directory.glob("*.bak")).read_bytes() == original


@pytest.mark.parametrize(
    "queue_key", ["ready", "active", "stale", "waiting_resolvable", "undisposed_reports"]
)
def test_malformed_or_nonempty_completion_state_never_false_positives(queue_key: str) -> None:
    snapshot: dict[str, object] = {
        "schema_version": 1,
        "run_id": "fault-run",
        "objective": "must not complete",
        "queue": {queue_key: ["S-1"]},
        "evidence_hashes": {},
    }
    result = evaluate_completion(snapshot)
    assert result.complete is False
    assert result.failed_conditions


def test_completion_input_mutation_does_not_change_result_determinism() -> None:
    snapshot = {"schema_version": 1, "run_id": "fault-run", "queue": {}, "evidence_hashes": {}}
    first = evaluate_completion(snapshot)
    serialized = json.loads(json.dumps(snapshot))
    second = evaluate_completion(serialized)
    assert first == second
