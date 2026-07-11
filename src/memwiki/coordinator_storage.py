from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Dict, Mapping, Protocol, runtime_checkable


@dataclass(frozen=True)
class LogicalArtifact:
    """Provider-neutral artifact name without a physical file extension."""

    name: str

    def __post_init__(self) -> None:
        path = PurePosixPath(self.name)
        invalid = (
            not self.name
            or self.name == "."
            or path.is_absolute()
            or ".." in path.parts
            or "\\" in self.name
            or any(part in {"", "."} for part in path.parts)
            or bool(path.suffix)
        )
        if invalid:
            raise ValueError(
                "artifact name must be a confined logical artifact name without an extension"
            )


@dataclass(frozen=True)
class ArtifactPaths:
    json_path: Path
    html_path: Path


@runtime_checkable
class StorageAdapter(Protocol):
    """Logical storage contract used by the coordinator runtime."""

    @property
    def artifact_root(self) -> Path: ...

    def paths_for(self, artifact: LogicalArtifact) -> ArtifactPaths: ...

    def exists(self, artifact: LogicalArtifact) -> bool: ...

    def read_json(self, artifact: LogicalArtifact) -> Dict[str, Any]: ...

    def write_artifact(
        self,
        artifact: LogicalArtifact,
        source: Mapping[str, Any],
        generated_html: str,
    ) -> ArtifactPaths: ...


class FileArtifactStorage:
    """Rooted filesystem implementation shared by concrete layout adapters.

    JSON is the authoritative source. It is atomically replaced before its HTML
    projection, so an interrupted view write can always be regenerated.
    """

    def __init__(self, artifact_root: Path) -> None:
        root = Path(artifact_root)
        if root.is_symlink():
            raise ValueError("artifact root must not be a symbolic link")
        root.mkdir(parents=True, exist_ok=True)
        self._artifact_root = root.resolve(strict=True)

    @property
    def artifact_root(self) -> Path:
        return self._artifact_root

    def paths_for(self, artifact: LogicalArtifact) -> ArtifactPaths:
        relative = Path(*PurePosixPath(artifact.name).parts)
        base = self._artifact_root / relative
        paths = ArtifactPaths(
            json_path=base.with_suffix(".json"),
            html_path=base.with_suffix(".html"),
        )
        self._assert_confined(paths.json_path)
        self._assert_confined(paths.html_path)
        return paths

    def exists(self, artifact: LogicalArtifact) -> bool:
        path = self.paths_for(artifact).json_path
        self._assert_no_symlinks(path)
        return path.is_file()

    def read_json(self, artifact: LogicalArtifact) -> Dict[str, Any]:
        path = self.paths_for(artifact).json_path
        self._assert_no_symlinks(path)
        with path.open("r", encoding="utf-8") as handle:
            value = json.load(handle)
        if not isinstance(value, dict):
            raise ValueError("coordinator artifact JSON source must be an object")
        return value

    def write_artifact(
        self,
        artifact: LogicalArtifact,
        source: Mapping[str, Any],
        generated_html: str,
    ) -> ArtifactPaths:
        if not isinstance(generated_html, str):
            raise TypeError("generated HTML view must be a string")
        paths = self.paths_for(artifact)
        self._prepare_parent(paths.json_path.parent)
        json_bytes = (
            json.dumps(dict(source), indent=2, sort_keys=True, ensure_ascii=True) + "\n"
        ).encode("utf-8")
        html_bytes = (generated_html.rstrip("\n") + "\n").encode("utf-8")

        self._atomic_replace(paths.json_path, json_bytes)
        self._atomic_replace(paths.html_path, html_bytes)
        return paths

    def _prepare_parent(self, parent: Path) -> None:
        self._assert_confined(parent)
        relative = parent.relative_to(self._artifact_root)
        current = self._artifact_root
        for part in relative.parts:
            current = current / part
            if current.is_symlink():
                raise ValueError("artifact path must not contain a symbolic link")
            current.mkdir(exist_ok=True)
        self._assert_no_symlinks(parent)

    def _atomic_replace(self, target: Path, content: bytes) -> None:
        self._assert_confined(target)
        self._assert_no_symlinks(target)
        descriptor, temporary_name = tempfile.mkstemp(
            dir=target.parent,
            prefix=f".{target.name}.",
            suffix=".tmp",
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            temporary.replace(target)
            self._fsync_directory(target.parent)
        finally:
            temporary.unlink(missing_ok=True)

    def _assert_confined(self, path: Path) -> None:
        try:
            path.relative_to(self._artifact_root)
        except ValueError:
            raise ValueError("artifact path escapes the configured artifact root") from None

    def _assert_no_symlinks(self, path: Path) -> None:
        self._assert_confined(path)
        current = self._artifact_root
        for part in path.relative_to(self._artifact_root).parts:
            current = current / part
            if current.is_symlink():
                raise ValueError("artifact path must not contain a symbolic link")
            if not current.exists():
                break

    @staticmethod
    def _fsync_directory(directory: Path) -> None:
        descriptor = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
