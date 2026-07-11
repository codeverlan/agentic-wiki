from __future__ import annotations

from pathlib import Path

import pytest
from test_coordinator_storage import StorageAdapterConformance

from memwiki.coordinator_storage import LogicalArtifact, StorageAdapter
from memwiki.coordinator_storage_bmad import BmadArtifactStorage


class TestBmadArtifactStorage(StorageAdapterConformance):
    @staticmethod
    def make_adapter(root: Path) -> StorageAdapter:
        project_root = root.parent / f"{root.name}-project"
        (project_root / "_bmad").mkdir(parents=True)
        return BmadArtifactStorage(project_root, artifact_root=root)


def test_discovers_bmad_project_from_nested_path(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    nested = project_root / "src" / "feature"
    (project_root / "_bmad").mkdir(parents=True)
    nested.mkdir(parents=True)

    adapter = BmadArtifactStorage.discover(nested)

    assert adapter.project_root == project_root.resolve()
    assert adapter.artifact_root == (
        project_root / "_bmad-output" / "implementation-artifacts"
    ).resolve()


def test_discovery_requires_bmad_marker(tmp_path: Path) -> None:
    nested = tmp_path / "not-bmad" / "src"
    nested.mkdir(parents=True)

    with pytest.raises(ValueError, match="BMAD project"):
        BmadArtifactStorage.discover(nested)


def test_explicit_override_selects_artifact_root(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    override = tmp_path / "durable" / "implementation-artifacts"
    (project_root / "_bmad").mkdir(parents=True)

    adapter = BmadArtifactStorage(project_root, artifact_root=override)
    paths = adapter.write_artifact(
        LogicalArtifact("runtime/state"), {"revision": 1}, "<html>state</html>"
    )

    assert adapter.artifact_root == override.resolve()
    assert paths.json_path == override.resolve() / "runtime" / "state.json"


def test_construction_preserves_existing_bmad_artifacts(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    artifact_root = project_root / "_bmad-output" / "implementation-artifacts"
    (project_root / "_bmad").mkdir(parents=True)
    artifact_root.mkdir(parents=True)
    existing = artifact_root / "user-authored.html"
    existing.write_text("<html>keep me</html>\n", encoding="utf-8")

    adapter = BmadArtifactStorage(project_root)
    adapter.write_artifact(
        LogicalArtifact("coordinator/runtime"),
        {"status": "running"},
        "<html>running</html>",
    )

    assert existing.read_text(encoding="utf-8") == "<html>keep me</html>\n"


def test_rejects_file_directory_collision_without_modifying_file(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "project"
    artifact_root = project_root / "_bmad-output" / "implementation-artifacts"
    (project_root / "_bmad").mkdir(parents=True)
    artifact_root.mkdir(parents=True)
    collision = artifact_root / "runtime"
    collision.write_text("user content\n", encoding="utf-8")
    adapter = BmadArtifactStorage(project_root)

    with pytest.raises(ValueError, match="collision"):
        adapter.write_artifact(
            LogicalArtifact("runtime/state"), {"status": "running"}, "<html></html>"
        )

    assert collision.read_text(encoding="utf-8") == "user content\n"


def test_rejects_symlink_in_default_bmad_output_path(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    outside = tmp_path / "outside"
    (project_root / "_bmad").mkdir(parents=True)
    outside.mkdir()
    (project_root / "_bmad-output").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError, match="symbolic link"):
        BmadArtifactStorage(project_root)

    assert list(outside.iterdir()) == []


def test_rejects_explicit_symlinked_artifact_root(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    outside = tmp_path / "outside"
    (project_root / "_bmad").mkdir(parents=True)
    outside.mkdir()
    override = tmp_path / "artifacts"
    override.symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError, match="symbolic link"):
        BmadArtifactStorage(project_root, artifact_root=override)
