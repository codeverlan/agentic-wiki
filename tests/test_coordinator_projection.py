import json
from pathlib import Path

from memwiki.coordinator_events import CoordinatorEvent
from memwiki.coordinator_journal import CoordinatorJournal
from memwiki.coordinator_projection import ProjectionCache, reduce_events


def chain() -> list[CoordinatorEvent]:
    events = []
    prior = None
    definitions = [
        ("run.started", {"status": "active", "max_workers": 6}),
        ("slice.registered", {"slice_id": "AC-001", "status": "ready"}),
        ("slice.status_changed", {"slice_id": "AC-001", "status": "active"}),
    ]
    for sequence, (event_type, payload) in enumerate(definitions, start=1):
        current = CoordinatorEvent.create(
            run_id="run-1",
            sequence=sequence,
            projection_revision=sequence,
            event_type=event_type,
            payload=payload,
            actor={"type": "test", "id": "pytest"},
            idempotency_key=f"key-{sequence}",
            prior_hash=prior,
            event_id=f"event-{sequence}",
            occurred_at=f"2026-07-11T12:0{sequence}:00-04:00",
        )
        events.append(current)
        prior = current.event_hash
    return events


def test_replay_is_deterministic_and_byte_equivalent() -> None:
    first = reduce_events(chain())
    second = reduce_events(chain())

    assert first == second
    assert first.to_json_bytes() == second.to_json_bytes()
    assert first.slices["AC-001"]["status"] == "active"


def test_projection_cache_repairs_missing_or_corrupt_snapshot(tmp_path: Path) -> None:
    journal = CoordinatorJournal(tmp_path / "events.journal")
    for item in chain():
        journal.append_event(item)
    cache = ProjectionCache(tmp_path / "projection.json")

    rebuilt = cache.load_or_rebuild(journal, run_id="run-1")
    assert cache.path.exists()
    cache.path.unlink()
    assert cache.load_or_rebuild(journal, run_id="run-1") == rebuilt
    cache.path.write_text("not json", encoding="utf-8")
    assert cache.load_or_rebuild(journal, run_id="run-1") == rebuilt


def test_projection_cache_rejects_stale_snapshot_and_replays(tmp_path: Path) -> None:
    journal = CoordinatorJournal(tmp_path / "events.journal")
    events = chain()
    journal.append_event(events[0])
    cache = ProjectionCache(tmp_path / "projection.json")
    stale = cache.load_or_rebuild(journal, run_id="run-1")
    journal.append_event(events[1])
    journal.append_event(events[2])

    current = cache.load_or_rebuild(journal, run_id="run-1")

    assert current.revision == 3
    assert current != stale
    assert json.loads(cache.path.read_text())["journal_hash"] == events[-1].event_hash
