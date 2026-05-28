from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

WORKSPACE_DIRS = [
    "raw",
    "wiki",
    "drafts",
    "assets",
    "manifests",
    "schemas",
    "docs",
    ".memwiki",
    ".memwiki/extracted",
    ".memwiki/index",
]


@dataclass(frozen=True)
class Workspace:
    root: Path

    def __post_init__(self) -> None:
        object.__setattr__(self, "root", self.root.resolve())

    @property
    def config_path(self) -> Path:
        return self.root / ".memwiki" / "config.toml"

    def path(self, relative: str) -> Path:
        return self.root / relative

    def ensure_dirs(self) -> None:
        for name in WORKSPACE_DIRS:
            (self.root / name).mkdir(parents=True, exist_ok=True)

    def require(self) -> None:
        if not self.config_path.exists():
            raise RuntimeError(f"Not a memwiki workspace: {self.root}")
