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


def lifecycle_chain() -> list[CoordinatorEvent]:
    events = []
    prior = None
    definitions = [
        ("run.started", {"status": "active", "max_workers": 6}),
        (
            "slice.proposed",
            {
                "slice_id": "ADC-001",
                "title": "Canonical coordinator queue",
                "status": "proposed",
                "depends_on": [],
                "owned_paths": ["src/memwiki/coordinator_projection.py"],
                "required": True,
            },
        ),
        ("slice.admitted", {"slice_id": "ADC-001", "status": "ready"}),
        (
            "slice.assigned",
            {
                "slice_id": "ADC-001",
                "status": "active",
                "worker_id": "worker-1",
                "attempt_id": "attempt-1",
                "context_package_id": "sha256:context",
            },
        ),
        (
            "worker.started",
            {"worker_id": "worker-1", "slice_id": "ADC-001", "attempt_id": "attempt-1"},
        ),
        (
            "worker.heartbeat",
            {"worker_id": "worker-1", "lease_id": "lease-1", "status": "active"},
        ),
        (
            "runtime.observed",
            {"observation_id": "runtime-1", "freshness": "live", "goal_status": "active"},
        ),
        (
            "resource.observed",
            {
                "measurement_id": "tokens-1",
                "metric": "tokens",
                "value": None,
                "quality": "unavailable",
                "source": "codex-worker-interface",
            },
        ),
        (
            "worker.reported",
            {
                "report_id": "report-1",
                "worker_id": "worker-1",
                "slice_id": "ADC-001",
                "attempt_id": "attempt-1",
                "status": "succeeded",
                "report_hash": "sha256:report",
            },
        ),
        (
            "memory.proposed",
            {"proposal_id": "memory-1", "slice_id": "ADC-001", "status": "proposed"},
        ),
        (
            "memory.dispositioned",
            {"proposal_id": "memory-1", "status": "accepted", "disposition_id": "memory-d-1"},
        ),
        (
            "validation.recorded",
            {"validation_id": "validation-1", "slice_id": "ADC-001", "status": "passed"},
        ),
        ("slice.transitioned", {"slice_id": "ADC-001", "status": "integrated"}),
        (
            "completion.evaluated",
            {"evaluation_id": "completion-1", "status": "complete", "passed": True},
        ),
        ("run.completed", {"status": "completed"}),
    ]
    for sequence, (event_type, payload) in enumerate(definitions, start=1):
        event = CoordinatorEvent.create(
            run_id="run-lifecycle",
            sequence=sequence,
            projection_revision=sequence,
            event_type=event_type,
            payload=payload,
            actor={"type": "test", "id": "pytest"},
            idempotency_key=f"lifecycle-{sequence}",
            prior_hash=prior,
            occurred_at=f"2026-07-12T18:{sequence:02d}:00-04:00",
        )
        events.append(event)
        prior = event.event_hash
    return events


def test_replay_is_deterministic_and_byte_equivalent() -> None:
    first = reduce_events(chain())
    second = reduce_events(chain())

    assert first == second
    assert first.to_json_bytes() == second.to_json_bytes()
    assert first.slices["AC-001"]["status"] == "active"


def test_lifecycle_events_build_one_canonical_project_projection() -> None:
    projection = reduce_events(lifecycle_chain())

    assert projection.schema_version == 2
    assert projection.status == "completed"
    assert projection.slices["ADC-001"]["status"] == "integrated"
    assert projection.assignments["ADC-001"]["worker_id"] == "worker-1"
    assert projection.workers["worker-1"]["status"] == "succeeded"
    assert projection.worker_reports["report-1"]["report_hash"] == "sha256:report"
    assert projection.runtime_observations[-1]["freshness"] == "live"
    assert projection.resource_observations[-1]["quality"] == "unavailable"
    assert projection.memory_proposals["memory-1"]["status"] == "accepted"
    assert projection.validations["validation-1"]["status"] == "passed"
    assert projection.completion_evaluations[-1]["status"] == "complete"


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
