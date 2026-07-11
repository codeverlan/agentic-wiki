from __future__ import annotations

from pathlib import Path
from typing import Optional

from .coordinator_storage import FileArtifactStorage

LIGHTWEIGHT_ARTIFACT_DIRECTORY = Path(
    ".agent-development/implementation-artifacts"
)


class LightweightStorageAdapter(FileArtifactStorage):
    """Project-local coordinator storage for projects that do not use BMAD."""

    def __init__(self, project_root: Path) -> None:
        root = Path(project_root)
        if root.is_symlink():
            raise ValueError("project root must not be a symbolic link")
        root.mkdir(parents=True, exist_ok=True)
        self._project_root = root.resolve(strict=True)
        super().__init__(self._project_root / LIGHTWEIGHT_ARTIFACT_DIRECTORY)

    @property
    def project_root(self) -> Path:
        return self._project_root


def discover_lightweight_storage(
    project_root: Path,
) -> Optional[LightweightStorageAdapter]:
    """Select lightweight storage when the project has no BMAD runtime."""

    root = Path(project_root)
    bmad_root = root / "_bmad"
    if bmad_root.exists() or bmad_root.is_symlink():
        return None
    return LightweightStorageAdapter(root)
