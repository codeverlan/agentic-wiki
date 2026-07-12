import json
import multiprocessing
from pathlib import Path
from typing import Optional

import pytest

from memwiki.coordinator_events import CoordinatorEvent
from memwiki.coordinator_journal import CoordinatorJournal, TornJournalError


def event(sequence: int, prior_hash: Optional[str] = None) -> CoordinatorEvent:
    return CoordinatorEvent.create(
        run_id="run-1",
        sequence=sequence,
        projection_revision=sequence,
        event_type="test.event",
        payload={"sequence": sequence},
        actor={"type": "test", "id": "pytest"},
        idempotency_key=f"key-{sequence}",
        prior_hash=prior_hash,
        event_id=f"event-{sequence}",
        occurred_at=f"2026-07-11T11:{sequence:02d}:00-04:00",
    )


def append_worker(path: str, worker: int, count: int) -> None:
    journal = CoordinatorJournal(Path(path))
    for index in range(count):
        journal.append_record({"worker": worker, "index": index})


def test_append_and_read_complete_records(tmp_path: Path) -> None:
    journal = CoordinatorJournal(tmp_path / "events.journal")
    first = event(1)
    second = event(2, first.event_hash)

    journal.append_event(first)
    journal.append_event(second)

    assert journal.read_events(run_id="run-1") == [first, second]


def test_concurrent_appenders_lose_no_records(tmp_path: Path) -> None:
    path = tmp_path / "concurrent.journal"
    processes = [multiprocessing.Process(target=append_worker, args=(str(path), worker, 25)) for worker in range(4)]
    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=10)
        assert process.exitcode == 0

    records = CoordinatorJournal(path).read_records()
    assert len(records) == 100
    assert {(record["worker"], record["index"]) for record in records} == {
        (worker, index) for worker in range(4) for index in range(25)
    }


def test_torn_tail_can_be_quarantined_without_losing_prior_records(tmp_path: Path) -> None:
    path = tmp_path / "torn.journal"
    journal = CoordinatorJournal(path)
    journal.append_record({"complete": 1})
    with path.open("ab") as stream:
        stream.write(b'00000020:{"incomplete":')

    with pytest.raises(TornJournalError):
        journal.read_records()

    quarantine = journal.quarantine_torn_tail()

    assert journal.read_records() == [{"complete": 1}]
    assert quarantine is not None
    assert quarantine.read_bytes() == b'00000020:{"incomplete":'


def test_corruption_in_complete_record_is_never_silently_truncated(tmp_path: Path) -> None:
    path = tmp_path / "corrupt.journal"
    path.write_bytes(b"00000005:abcde\n")

    with pytest.raises(TornJournalError, match="invalid JSON"):
        CoordinatorJournal(path).quarantine_torn_tail()


def test_record_payload_is_canonical_json(tmp_path: Path) -> None:
    path = tmp_path / "canonical.journal"
    CoordinatorJournal(path).append_record({"z": 1, "a": 2})

    raw = path.read_bytes()
    length, payload = raw.rstrip(b"\n").split(b":", 1)
    assert int(length) == len(payload)
    assert payload == json.dumps({"a": 2, "z": 1}, separators=(",", ":"), sort_keys=True).encode()


def test_append_command_allocates_sequence_and_is_idempotent(tmp_path: Path) -> None:
    journal = CoordinatorJournal(tmp_path / "commands.journal")

    first, first_appended = journal.append_command(
        run_id="run-1",
        event_type="run.started",
        payload={"status": "active", "max_workers": 6},
        actor={"type": "coordinator", "id": "coordinator-1"},
        idempotency_key="start-run-1",
        occurred_at="2026-07-12T22:00:00-04:00",
    )
    duplicate, duplicate_appended = journal.append_command(
        run_id="run-1",
        event_type="run.started",
        payload={"status": "active", "max_workers": 6},
        actor={"type": "coordinator", "id": "coordinator-1"},
        idempotency_key="start-run-1",
        occurred_at="2026-07-12T22:00:00-04:00",
    )
    second, second_appended = journal.append_command(
        run_id="run-1",
        event_type="slice.proposed",
        payload={"slice_id": "ADC-001", "status": "proposed"},
        actor={"type": "coordinator", "id": "coordinator-1"},
        idempotency_key="propose-ADC-001",
        occurred_at="2026-07-12T22:01:00-04:00",
    )

    assert first_appended is True
    assert duplicate_appended is False
    assert duplicate == first
    assert second_appended is True
    assert second.sequence == 2
    assert second.prior_hash == first.event_hash
    assert journal.read_events(run_id="run-1") == [first, second]


def test_append_command_rejects_idempotency_key_reuse_with_different_payload(tmp_path: Path) -> None:
    journal = CoordinatorJournal(tmp_path / "commands.journal")
    common = {
        "run_id": "run-1",
        "event_type": "run.started",
        "actor": {"type": "coordinator", "id": "coordinator-1"},
        "idempotency_key": "start-run-1",
        "occurred_at": "2026-07-12T22:00:00-04:00",
    }
    journal.append_command(payload={"status": "active", "max_workers": 6}, **common)

    with pytest.raises(ValueError, match="idempotency key"):
        journal.append_command(payload={"status": "blocked", "max_workers": 6}, **common)
