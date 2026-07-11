from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from memwiki.cli import app

runner = CliRunner()


def invoke(workspace: Path, *arguments: str) -> tuple[int, dict[str, object]]:
    result = runner.invoke(app, ["--workspace", str(workspace), "coordinator", *arguments])
    payload = json.loads(result.stdout) if result.stdout else {}
    return result.exit_code, payload


def test_cli_initialize_status_stop_resume_verify_migrate_and_render(tmp_path: Path) -> None:
    code, initialized = invoke(tmp_path, "initialize", "--run-id", "run-cli", "--max-workers", "4")
    assert code == 0
    assert initialized["run_id"] == "run-cli"

    assert invoke(tmp_path, "status") == (0, initialized)
    assert invoke(tmp_path, "stop", "--reason", "pause")[1]["desired_state"] == "stopped"
    assert invoke(tmp_path, "resume")[1]["desired_state"] == "running"
    assert invoke(tmp_path, "verify")[1]["valid"] is True
    assert invoke(tmp_path, "migrate")[1]["migrated"] is False
    rendered = invoke(tmp_path, "render")[1]
    assert Path(str(rendered["path"])).exists()


def test_cli_tick_and_run_report_missing_runtime_as_json(tmp_path: Path) -> None:
    invoke(tmp_path, "initialize", "--run-id", "run-cli")
    for command in ("tick", "run"):
        code, payload = invoke(tmp_path, command)
        assert code == 1
        assert payload["error"] == "coordinator runtime adapter is not configured"
        assert payload["operation"] == command


def test_memwiki_and_agentic_wiki_entrypoints_share_the_same_cli(tmp_path: Path) -> None:
    first = runner.invoke(app, ["--workspace", str(tmp_path), "coordinator", "initialize", "--run-id", "same"])
    second = runner.invoke(app, ["--workspace", str(tmp_path), "coordinator", "status"])
    assert first.exit_code == second.exit_code == 0
    assert json.loads(first.stdout) == json.loads(second.stdout)
