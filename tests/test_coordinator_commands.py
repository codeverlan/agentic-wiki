import json
from pathlib import Path

from memwiki.coordinator_commands import CoordinatorCommandGateway
from memwiki.coordinator_events import CoordinatorEvent
from memwiki.coordinator_journal import CoordinatorJournal


def test_gateway_appends_commands_and_returns_canonical_projection(tmp_path: Path) -> None:
    gateway = CoordinatorCommandGateway(tmp_path)

    start = gateway.append(
        run_id="run-1",
        event_type="run.started",
        payload={"status": "active", "max_workers": 6},
        actor={"type": "coordinator", "id": "coordinator-1"},
        idempotency_key="run-1:start",
        occurred_at="2026-07-12T22:00:00-04:00",
    )
    proposed = gateway.append(
        run_id="run-1",
        event_type="slice.proposed",
        payload={
            "slice_id": "ADC-001",
            "title": "Canonical queue",
            "status": "proposed",
            "depends_on": [],
            "owned_paths": ["src/memwiki/coordinator_commands.py"],
            "required": True,
        },
        actor={"type": "coordinator", "id": "coordinator-1"},
        idempotency_key="run-1:slice:ADC-001",
        occurred_at="2026-07-12T22:01:00-04:00",
    )

    assert start.appended is True
    assert start.projection.revision == 1
    assert proposed.appended is True
    assert proposed.projection.slices["ADC-001"]["status"] == "proposed"
    assert gateway.status(run_id="run-1") == proposed.projection


def test_gateway_duplicate_command_returns_same_revision(tmp_path: Path) -> None:
    gateway = CoordinatorCommandGateway(tmp_path)
    command = {
        "run_id": "run-1",
        "event_type": "run.started",
        "payload": {"status": "active", "max_workers": 6},
        "actor": {"type": "coordinator", "id": "coordinator-1"},
        "idempotency_key": "run-1:start",
        "occurred_at": "2026-07-12T22:00:00-04:00",
    }

    first = gateway.append(**command)
    duplicate = gateway.append(**command)

    assert first.appended is True
    assert duplicate.appended is False
    assert duplicate.event == first.event
    assert duplicate.projection.revision == first.projection.revision
    assert gateway.event_count(run_id="run-1") == 1
    assert gateway.rebuild(run_id="run-1") == first.projection


def test_gateway_keeps_multiple_runs_in_confined_independent_journals(tmp_path: Path) -> None:
    gateway = CoordinatorCommandGateway(tmp_path)

    first = gateway.append(
        run_id="run-one",
        event_type="run.started",
        payload={"status": "active", "max_workers": 6},
        actor={"type": "coordinator", "id": "coordinator-1"},
        idempotency_key="run-one:start",
    )
    second = gateway.append(
        run_id="../../run-two",
        event_type="run.started",
        payload={"status": "active", "max_workers": 4},
        actor={"type": "coordinator", "id": "coordinator-1"},
        idempotency_key="run-two:start",
    )

    assert first.projection.revision == second.projection.revision == 1
    assert gateway.status(run_id="run-one").run_id == "run-one"
    assert gateway.status(run_id="../../run-two").run_id == "../../run-two"
    registry = gateway.runs()
    assert {item["run_id"] for item in registry} == {"run-one", "../../run-two"}
    for item in registry:
        run_path = (tmp_path / ".memwiki" / "coordinator" / item["path"]).resolve()
        run_path.relative_to((tmp_path / ".memwiki" / "coordinator").resolve())
        assert (run_path / "events.journal").is_file()


def test_gateway_preserves_and_indexes_legacy_single_run_journal(tmp_path: Path) -> None:
    directory = tmp_path / ".memwiki" / "coordinator"
    legacy = CoordinatorJournal(directory / "events.journal")
    event = CoordinatorEvent.create(
        run_id="legacy-run",
        sequence=1,
        projection_revision=1,
        event_type="run.started",
        payload={"status": "active", "max_workers": 6},
        actor={"type": "test", "id": "pytest"},
        idempotency_key="legacy:start",
        prior_hash=None,
    )
    legacy.append_event(event)
    gateway = CoordinatorCommandGateway(tmp_path)

    assert gateway.status(run_id="legacy-run").revision == 1
    gateway.append(
        run_id="new-run",
        event_type="run.started",
        payload={"status": "active", "max_workers": 3},
        actor={"type": "coordinator", "id": "coordinator-1"},
        idempotency_key="new:start",
    )

    registry = json.loads((directory / "runs.json").read_text(encoding="utf-8"))
    paths = {item["run_id"]: item["path"] for item in registry["runs"]}
    assert paths["legacy-run"] == "."
    assert paths["new-run"].startswith("runs/")
    assert {item["run_id"] for item in gateway.runs()} == {"legacy-run", "new-run"}
