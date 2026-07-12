from pathlib import Path

from memwiki.coordinator_commands import CoordinatorCommandGateway


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
