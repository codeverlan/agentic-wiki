from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType

import pytest

PLUGIN_ROOT = Path("/Users/tyler-lcsw/plugins/agent-development-coordinator")
HELPER_PATH = PLUGIN_ROOT / "scripts" / "coordinator.py"


def _helper() -> ModuleType:
    spec = importlib.util.spec_from_file_location("agent_dev_coordinator_helper", HELPER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_preflight_selects_bmad_and_lightweight_roots(tmp_path: Path) -> None:
    helper = _helper()
    lightweight = helper.preflight(tmp_path)
    assert lightweight["adapter"] == "lightweight"
    assert lightweight["artifact_root"] == str(
        tmp_path / ".agent-development" / "implementation-artifacts"
    )

    (tmp_path / "_bmad").mkdir()
    bmad = helper.preflight(tmp_path)
    assert bmad["adapter"] == "bmad"
    assert bmad["artifact_root"] == str(tmp_path / "_bmad-output" / "implementation-artifacts")


def test_plugin_root_and_descendants_cannot_be_project_roots() -> None:
    helper = _helper()
    with pytest.raises(ValueError, match="plugin source"):
        helper.preflight(PLUGIN_ROOT)
    with pytest.raises(ValueError, match="plugin source"):
        helper.preflight(PLUGIN_ROOT / "scripts")


def test_cli_command_uses_public_memwiki_surface(tmp_path: Path) -> None:
    helper = _helper()
    command = helper.coordinator_command(tmp_path, "initialize", "--run-id", "smoke")
    assert Path(command[0]).name == "memwiki"
    assert command[1:3] == ["--workspace", str(tmp_path.resolve())]
    assert command[3:] == ["coordinator", "initialize", "--run-id", "smoke"]


def test_output_path_must_stay_in_project(tmp_path: Path) -> None:
    helper = _helper()
    with pytest.raises(ValueError, match="project root"):
        helper.confined_output(tmp_path, PLUGIN_ROOT / "status.html")


def test_preflight_is_read_only_and_contains_no_environment_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    helper = _helper()
    monkeypatch.setenv("COORDINATOR_TEST_SECRET", "never-persist-this")
    before = sorted(path.relative_to(tmp_path) for path in tmp_path.rglob("*"))
    result = helper.preflight(tmp_path)
    after = sorted(path.relative_to(tmp_path) for path in tmp_path.rglob("*"))
    assert before == after
    assert "never-persist-this" not in json.dumps(result)


def test_immutable_snapshot_detects_plugin_mutation(tmp_path: Path) -> None:
    helper = _helper()
    plugin = tmp_path / "plugin"
    plugin.mkdir()
    source = plugin / "file.txt"
    source.write_text("before", encoding="utf-8")
    snapshot = helper.plugin_snapshot(plugin)
    helper.assert_plugin_unchanged(plugin, snapshot)
    source.write_text("after", encoding="utf-8")
    with pytest.raises(RuntimeError, match="plugin source changed"):
        helper.assert_plugin_unchanged(plugin, snapshot)


def test_immutable_snapshot_ignores_git_metadata(tmp_path: Path) -> None:
    helper = _helper()
    plugin = tmp_path / "plugin"
    (plugin / ".git").mkdir(parents=True)
    (plugin / "skill.md").write_text("stable", encoding="utf-8")
    git_ref = plugin / ".git" / "HEAD"
    git_ref.write_text("before", encoding="utf-8")
    snapshot = helper.plugin_snapshot(plugin)

    git_ref.write_text("after", encoding="utf-8")

    helper.assert_plugin_unchanged(plugin, snapshot)


def test_helpers_never_accept_credential_arguments() -> None:
    source = HELPER_PATH.read_text(encoding="utf-8")
    forbidden = ("--token", "--password", "--api-key", "credential_value")
    assert not any(value in source for value in forbidden)
