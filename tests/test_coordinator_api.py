from __future__ import annotations

import json
from pathlib import Path
from typing import Dict

import pytest

from memwiki.coordinator_api import CoordinatorAPI, CoordinatorRuntime, RuntimeTick


class FakeRuntime(CoordinatorRuntime):
    def __init__(self) -> None:
        self.calls = 0

    def tick(self, state: Dict[str, object]) -> RuntimeTick:
        self.calls += 1
        terminal = self.calls == 2
        return RuntimeTick(
            state={**state, "status": "completed" if terminal else "running"},
            intents_applied=1,
            terminal=terminal,
        )


def test_initialize_status_stop_and_resume_are_durable(tmp_path: Path) -> None:
    api = CoordinatorAPI(tmp_path)
    initialized = api.initialize(run_id="run-1", max_workers=6)

    assert initialized["run_id"] == "run-1"
    assert initialized["status"] == "ready"
    assert CoordinatorAPI(tmp_path).status() == initialized

    stopped = api.stop(reason="operator request")
    assert stopped["desired_state"] == "stopped"
    assert stopped["stop_reason"] == "operator request"
    assert api.resume()["desired_state"] == "running"


def test_initialize_is_idempotent_and_rejects_conflicting_identity(tmp_path: Path) -> None:
    api = CoordinatorAPI(tmp_path)
    first = api.initialize(run_id="run-1", max_workers=3)
    assert api.initialize(run_id="run-1", max_workers=3) == first
    with pytest.raises(ValueError, match="already initialized"):
        api.initialize(run_id="run-2", max_workers=3)


def test_tick_and_run_delegate_to_explicit_runtime(tmp_path: Path) -> None:
    runtime = FakeRuntime()
    api = CoordinatorAPI(tmp_path, runtime=runtime)
    api.initialize(run_id="run-1")

    tick = api.tick()
    assert tick["status"] == "running"
    result = api.run(max_ticks=5)
    assert result["status"] == "completed"
    assert result["ticks"] == 1
    assert result["intents_applied"] == 1


def test_tick_requires_an_explicit_provider_neutral_runtime(tmp_path: Path) -> None:
    api = CoordinatorAPI(tmp_path)
    api.initialize(run_id="run-1")
    with pytest.raises(RuntimeError, match="runtime adapter is not configured"):
        api.tick()


def test_verify_detects_tampering_and_render_is_semantic_html(tmp_path: Path) -> None:
    api = CoordinatorAPI(tmp_path)
    api.initialize(run_id="run-1")
    assert api.verify()["valid"] is True

    rendered = api.render()
    html_path = Path(str(rendered["path"]))
    html = html_path.read_text(encoding="utf-8")
    assert "<!doctype html>" in html.lower()
    assert 'type="application/ld+json"' in html
    assert "run-1" in html

    state_path = tmp_path / ".memwiki" / "coordinator" / "control.json"
    payload = json.loads(state_path.read_text(encoding="utf-8"))
    payload["max_workers"] = 9
    state_path.write_text(json.dumps(payload), encoding="utf-8")
    result = api.verify()
    assert result["valid"] is False
    assert result["errors"]


def test_migrate_returns_machine_readable_noop_for_current_schema(tmp_path: Path) -> None:
    api = CoordinatorAPI(tmp_path)
    api.initialize(run_id="run-1")
    assert api.migrate() == {
        "actions": [],
        "from_version": 1,
        "migrated": False,
        "to_version": 1,
    }
