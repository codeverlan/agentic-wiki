from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

import pytest

from memwiki.coordinator_storage import (
    FileArtifactStorage,
    LogicalArtifact,
    StorageAdapter,
)


class StorageAdapterConformance:
    """Shared behavioral suite for every coordinator storage adapter."""

    make_adapter: Callable[[Path], StorageAdapter]

    def test_logical_artifact_round_trip(self, tmp_path: Path) -> None:
        adapter = self.make_adapter(tmp_path / "artifacts")
        artifact = LogicalArtifact("runtime/coordinator-state")

        paths = adapter.write_artifact(
            artifact,
            {"status": "running", "sequence": 7},
            "<!doctype html><html><body>running</body></html>",
        )

        assert adapter.read_json(artifact) == {"sequence": 7, "status": "running"}
        assert paths.json_path.name == "coordinator-state.json"
        assert paths.html_path.name == "coordinator-state.html"
        assert paths.html_path.read_text(encoding="utf-8").endswith("</html>\n")

    def test_same_behavior_under_a_different_root(self, tmp_path: Path) -> None:
        payload = {"items": [{"id": "AC-008"}]}
        artifact = LogicalArtifact("queues/execution")
        results = []

        for root in (tmp_path / "first", tmp_path / "nested" / "second"):
            adapter = self.make_adapter(root)
            paths = adapter.write_artifact(artifact, payload, "<html>queue</html>")
            results.append(
                (
                    adapter.read_json(artifact),
                    paths.json_path.relative_to(root).as_posix(),
                    paths.html_path.relative_to(root).as_posix(),
                )
            )

        assert results[0] == results[1]

    def test_json_is_authoritative_when_html_generation_is_interrupted(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        adapter = self.make_adapter(tmp_path / "artifacts")
        artifact = LogicalArtifact("runtime/state")
        adapter.write_artifact(artifact, {"revision": 1}, "<html>one</html>")

        original_replace = Path.replace

        def fail_html_replace(source: Path, target: Path) -> Path:
            if target.suffix == ".html":
                raise OSError("simulated view failure")
            return original_replace(source, target)

        monkeypatch.setattr(Path, "replace", fail_html_replace)

        with pytest.raises(OSError, match="simulated view failure"):
            adapter.write_artifact(artifact, {"revision": 2}, "<html>two</html>")

        assert adapter.read_json(artifact) == {"revision": 2}


class TestFileArtifactStorage(StorageAdapterConformance):
    make_adapter = staticmethod(FileArtifactStorage)


@pytest.mark.parametrize(
    "name",
    ["", ".", "../escape", "/absolute", "queue/../../escape", "queue/item.json", "queue\\item"],
)
def test_logical_artifact_rejects_unconfined_or_physical_names(name: str) -> None:
    with pytest.raises(ValueError, match="confined logical artifact name"):
        LogicalArtifact(name)


def test_storage_rejects_symlinked_parent(tmp_path: Path) -> None:
    root = tmp_path / "artifacts"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (root / "runtime").symlink_to(outside, target_is_directory=True)
    adapter = FileArtifactStorage(root)

    with pytest.raises(ValueError, match="symbolic link"):
        adapter.write_artifact(LogicalArtifact("runtime/state"), {"safe": True}, "<html></html>")

    assert list(outside.iterdir()) == []


def test_storage_rejects_symlinked_artifact_file(tmp_path: Path) -> None:
    root = tmp_path / "artifacts"
    root.mkdir()
    target = tmp_path / "outside.json"
    target.write_text(json.dumps({"secret": True}), encoding="utf-8")
    (root / "state.json").symlink_to(target)
    adapter = FileArtifactStorage(root)

    with pytest.raises(ValueError, match="symbolic link"):
        adapter.read_json(LogicalArtifact("state"))


def test_failed_json_replace_preserves_previous_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter = FileArtifactStorage(tmp_path / "artifacts")
    artifact = LogicalArtifact("state")
    adapter.write_artifact(artifact, {"revision": 1}, "<html>one</html>")

    def fail_replace(_source: Path, _target: Path) -> Path:
        raise OSError("simulated atomic replace failure")

    monkeypatch.setattr(Path, "replace", fail_replace)
    with pytest.raises(OSError, match="simulated atomic replace failure"):
        adapter.write_artifact(artifact, {"revision": 2}, "<html>two</html>")

    assert adapter.read_json(artifact) == {"revision": 1}
    assert not list((tmp_path / "artifacts").glob(".*.tmp"))
