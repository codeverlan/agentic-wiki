from __future__ import annotations

import importlib.util
import json
from argparse import Namespace
from pathlib import Path
from types import ModuleType

PLUGIN_ROOT = Path("/Users/tyler-lcsw/plugins/agent-development-coordinator")
SCRIPT = PLUGIN_ROOT / "scripts" / "start_project.py"


def _module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("agent_dev_start_project", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_plugin_start_project_executes_initialize_apply_and_readiness(tmp_path: Path) -> None:
    helper = _module()
    initialized = helper.execute(
        Namespace(
            project_root=tmp_path,
            operation="initialize",
            project_id="plugin-pilot",
            entry_path="from-scratch",
            project_type="web-application",
            phi_answer="no",
            recorded_at="2026-07-12T15:00:00-04:00",
        )
    )
    assert initialized["revision"] == 1

    update = tmp_path / "update.json"
    update.write_text(
        json.dumps(
            {
                "product": {
                    "purpose": "Build a synthetic scheduling tool",
                    "users": ["synthetic scheduler"],
                    "outcomes": ["shifts are assigned"],
                    "scope": ["schedule"],
                },
                "acceptance_criteria": [{"id": "AC-1", "statement": "Shift is assigned"}],
                "open_questions": [],
            }
        ),
        encoding="utf-8",
    )
    applied = helper.execute(
        Namespace(
            project_root=tmp_path,
            operation="apply",
            input=update,
            expected_revision=1,
            recorded_at="2026-07-12T15:01:00-04:00",
        )
    )
    assert applied["revision"] == 2
    readiness = helper.execute(Namespace(project_root=tmp_path, operation="readiness"))
    assert readiness["ready"] is True


def test_project_start_operations_do_not_mutate_plugin_source(tmp_path: Path) -> None:
    coordinator = PLUGIN_ROOT / "scripts" / "coordinator.py"
    spec = importlib.util.spec_from_file_location("agent_dev_coordinator", coordinator)
    assert spec is not None and spec.loader is not None
    guard = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(guard)
    before = guard.plugin_snapshot()

    helper = _module()
    helper.execute(
        Namespace(
            project_root=tmp_path,
            operation="initialize",
            project_id="immutable",
            entry_path="from-scratch",
            project_type="cli",
            phi_answer="no",
            recorded_at="2026-07-12T15:00:00-04:00",
        )
    )

    guard.assert_plugin_unchanged(PLUGIN_ROOT, before)
