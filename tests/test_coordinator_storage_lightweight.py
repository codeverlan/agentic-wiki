from __future__ import annotations

from pathlib import Path

from test_coordinator_storage import StorageAdapterConformance

from memwiki.coordinator_storage import LogicalArtifact, StorageAdapter
from memwiki.coordinator_storage_lightweight import (
    LightweightStorageAdapter,
    discover_lightweight_storage,
)


class TestLightweightStorageAdapter(StorageAdapterConformance):
    make_adapter = staticmethod(LightweightStorageAdapter)


def test_lightweight_adapter_maps_project_local_artifacts(tmp_path: Path) -> None:
    adapter = LightweightStorageAdapter(tmp_path)

    paths = adapter.write_artifact(
        LogicalArtifact("runtime/coordinator-state"),
        {"status": "running"},
        "<html>running</html>",
    )

    expected_root = (
        tmp_path / ".agent-development" / "implementation-artifacts"
    ).resolve()
    assert isinstance(adapter, StorageAdapter)
    assert adapter.project_root == tmp_path.resolve()
    assert adapter.artifact_root == expected_root
    assert paths.json_path == expected_root / "runtime/coordinator-state.json"
    assert paths.html_path == expected_root / "runtime/coordinator-state.html"


def test_discovery_selects_project_without_bmad(tmp_path: Path) -> None:
    adapter = discover_lightweight_storage(tmp_path)

    assert adapter is not None
    assert adapter.artifact_root == (
        tmp_path / ".agent-development" / "implementation-artifacts"
    ).resolve()


def test_discovery_declines_project_with_bmad(tmp_path: Path) -> None:
    (tmp_path / "_bmad").mkdir()

    assert discover_lightweight_storage(tmp_path) is None
    assert not (tmp_path / ".agent-development").exists()


def test_adapter_does_not_create_bmad_or_project_skill_copies(tmp_path: Path) -> None:
    adapter = LightweightStorageAdapter(tmp_path)
    adapter.write_artifact(LogicalArtifact("queues/execution"), {}, "<html></html>")

    assert not (tmp_path / "_bmad").exists()
    assert not (tmp_path / "skills").exists()
    assert not (tmp_path / ".codex" / "skills").exists()


def test_existing_project_files_are_preserved(tmp_path: Path) -> None:
    existing = tmp_path / "README.md"
    existing.write_text("project content\n", encoding="utf-8")

    adapter = LightweightStorageAdapter(tmp_path)
    adapter.write_artifact(LogicalArtifact("state"), {"revision": 1}, "<html></html>")

    assert existing.read_text(encoding="utf-8") == "project content\n"


def test_explicit_adapter_remains_available_when_bmad_exists(tmp_path: Path) -> None:
    (tmp_path / "_bmad").mkdir()

    adapter = LightweightStorageAdapter(tmp_path)

    assert adapter.artifact_root == (
        tmp_path / ".agent-development" / "implementation-artifacts"
    ).resolve()
