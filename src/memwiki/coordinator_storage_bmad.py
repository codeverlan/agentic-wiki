from __future__ import annotations

from pathlib import Path
from typing import Optional

from memwiki.coordinator_storage import FileArtifactStorage


class BmadArtifactStorage(FileArtifactStorage):
    """Coordinator artifact storage for a project initialized with BMAD."""

    def __init__(
        self,
        project_root: Path,
        *,
        artifact_root: Optional[Path] = None,
    ) -> None:
        root = Path(project_root).absolute()
        self._assert_path_has_no_symlinks(root)
        if not root.is_dir() or not (root / "_bmad").is_dir():
            raise ValueError(f"BMAD project marker not found at {root}")

        selected_root = (
            Path(artifact_root).absolute()
            if artifact_root is not None
            else root / "_bmad-output" / "implementation-artifacts"
        )
        self._assert_path_has_no_symlinks(selected_root)
        self._project_root = root.resolve(strict=True)
        super().__init__(selected_root)

    @property
    def project_root(self) -> Path:
        return self._project_root

    @classmethod
    def discover(
        cls,
        start: Path,
        *,
        artifact_root: Optional[Path] = None,
    ) -> "BmadArtifactStorage":
        """Find the nearest enclosing BMAD project and bind its artifact root."""

        candidate = Path(start).absolute()
        if candidate.is_file():
            candidate = candidate.parent
        for directory in (candidate, *candidate.parents):
            marker = directory / "_bmad"
            if marker.is_symlink():
                raise ValueError("BMAD project marker must not be a symbolic link")
            if marker.is_dir():
                return cls(directory, artifact_root=artifact_root)
        raise ValueError(f"no enclosing BMAD project found from {candidate}")

    def _prepare_parent(self, parent: Path) -> None:
        relative = parent.relative_to(self.artifact_root)
        current = self.artifact_root
        for part in relative.parts:
            current = current / part
            if current.exists() and not current.is_dir():
                raise ValueError(f"artifact path collision at {current}")
        super()._prepare_parent(parent)

    @staticmethod
    def _assert_path_has_no_symlinks(path: Path) -> None:
        absolute = path.absolute()
        current = Path(absolute.anchor)
        for part in absolute.parts[1:]:
            current = current / part
            if current.is_symlink():
                raise ValueError("BMAD artifact path must not contain a symbolic link")
            if not current.exists():
                break
