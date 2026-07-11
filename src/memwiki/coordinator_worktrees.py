from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Iterable, Optional, Tuple


class WorktreeError(RuntimeError):
    """A worker worktree operation could not be completed safely."""


class WorktreeDisposition(str, Enum):
    REMOVED = "removed"
    PRESERVED_ACTIVE = "preserved_active"
    PRESERVED_DIRTY = "preserved_dirty"
    PRESERVED_FAILED = "preserved_failed"
    PRESERVED_UNMERGED = "preserved_unmerged"
    MISSING = "missing"


@dataclass(frozen=True)
class WorkerWorktree:
    path: Path
    branch: str
    base_revision: str
    head_revision: str
    read_only: bool = False


@dataclass(frozen=True)
class WorktreeInspection:
    worktree: WorkerWorktree
    registered: bool
    exists: bool
    dirty_paths: Tuple[str, ...]
    stale: bool


@dataclass(frozen=True)
class WorktreeCleanupResult:
    worktree: WorkerWorktree
    disposition: WorktreeDisposition
    dirty_paths: Tuple[str, ...] = ()


_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")


class WorktreeIsolationAdapter:
    """Create and recover Git worktrees without touching the primary checkout."""

    def __init__(self, repository: Path, worktree_root: Path) -> None:
        self.repository = _repository_root(repository)
        self.worktree_root = worktree_root.expanduser().resolve()
        if _contains(self.repository, self.worktree_root):
            raise WorktreeError("worktree root must not be inside the repository")
        if self.worktree_root.exists() and self.worktree_root.is_symlink():
            raise WorktreeError("worktree root must not be a symlink")

    def shared_read_only(self) -> WorkerWorktree:
        branch = _git(self.repository, "symbolic-ref", "--short", "HEAD").stdout.strip()
        head = _git(self.repository, "rev-parse", "HEAD").stdout.strip()
        return WorkerWorktree(
            path=self.repository,
            branch=branch,
            base_revision=head,
            head_revision=head,
            read_only=True,
        )

    def create(
        self, slice_id: str, attempt_id: str, *, base_revision: str
    ) -> WorkerWorktree:
        name = _worker_name(slice_id, attempt_id)
        branch = f"codex/worker/{name}"
        path = (self.worktree_root / name).resolve()
        self._require_confined(path)
        base = _resolve_commit(self.repository, base_revision)
        if _branch_exists(self.repository, branch) or path.exists() or self._registered(path):
            raise WorktreeError(f"worker branch or worktree already exists: {branch}")

        self.worktree_root.mkdir(parents=True, exist_ok=True)
        result = _git(
            self.repository,
            "worktree",
            "add",
            "-b",
            branch,
            str(path),
            base,
            check=False,
        )
        if result.returncode != 0:
            raise WorktreeError(f"could not create worker worktree: {result.stderr.strip()}")
        head = _git(path, "rev-parse", "HEAD").stdout.strip()
        if head != base:
            raise WorktreeError("created worktree does not match the requested base revision")
        return WorkerWorktree(path, branch, base, head)

    def inspect(
        self, worktree: WorkerWorktree, *, active_branches: Iterable[str]
    ) -> WorktreeInspection:
        self._require_confined(worktree.path)
        exists = worktree.path.is_dir()
        registered = self._registered(worktree.path)
        dirty = _dirty_paths(worktree.path) if exists and registered else ()
        active = set(active_branches)
        return WorktreeInspection(
            worktree=worktree,
            registered=registered,
            exists=exists,
            dirty_paths=dirty,
            stale=worktree.branch not in active,
        )

    def cleanup(
        self, worktree: WorkerWorktree, *, successful: bool
    ) -> WorktreeCleanupResult:
        self._require_confined(worktree.path)
        if not successful:
            return WorktreeCleanupResult(worktree, WorktreeDisposition.PRESERVED_FAILED)
        inspection = self.inspect(worktree, active_branches=())
        if not inspection.exists and not inspection.registered:
            return WorktreeCleanupResult(worktree, WorktreeDisposition.MISSING)
        if inspection.dirty_paths:
            return WorktreeCleanupResult(
                worktree,
                WorktreeDisposition.PRESERVED_DIRTY,
                inspection.dirty_paths,
            )
        if worktree.path.exists() and _head(worktree.path) != worktree.base_revision:
            return WorktreeCleanupResult(worktree, WorktreeDisposition.PRESERVED_UNMERGED)

        if inspection.registered:
            result = _git(
                self.repository,
                "worktree",
                "remove",
                str(worktree.path),
                check=False,
            )
            if result.returncode != 0:
                raise WorktreeError(f"could not remove clean worktree: {result.stderr.strip()}")
        if _branch_exists(self.repository, worktree.branch):
            result = _git(
                self.repository, "branch", "-d", worktree.branch, check=False
            )
            if result.returncode != 0:
                return WorktreeCleanupResult(
                    worktree, WorktreeDisposition.PRESERVED_UNMERGED
                )
        return WorktreeCleanupResult(worktree, WorktreeDisposition.REMOVED)

    def verify_changed_paths(
        self,
        worktree: WorkerWorktree,
        *,
        owned_paths: Iterable[str],
        forbidden_paths: Iterable[str],
    ) -> Tuple[str, ...]:
        self._require_confined(worktree.path)
        if not worktree.path.is_dir() or not self._registered(worktree.path):
            raise WorktreeError("worker worktree is not available")
        owned = tuple(_relative_path(value) for value in owned_paths)
        forbidden = tuple(_relative_path(value) for value in forbidden_paths)
        if not owned:
            raise WorktreeError("at least one owned path is required for writing work")
        changed = _dirty_paths(worktree.path)
        for path in changed:
            if any(_path_overlap(path, denied) for denied in forbidden):
                raise WorktreeError(f"worker edited forbidden path: {path}")
            if not any(_path_overlap(path, allowed) for allowed in owned):
                raise WorktreeError(f"worker edited outside owned paths: {path}")
        return changed

    def recover(
        self, worktree: WorkerWorktree, *, active_branches: Iterable[str]
    ) -> WorktreeCleanupResult:
        inspection = self.inspect(worktree, active_branches=active_branches)
        if not inspection.stale:
            return WorktreeCleanupResult(
                worktree,
                WorktreeDisposition.PRESERVED_ACTIVE,
                inspection.dirty_paths,
            )
        return self.cleanup(worktree, successful=True)

    def _registered(self, path: Path) -> bool:
        target = path.resolve()
        return target in _registered_worktrees(self.repository)

    def _require_confined(self, path: Path) -> None:
        resolved = path.expanduser().resolve()
        if not _contains(self.worktree_root, resolved) or resolved == self.worktree_root:
            raise WorktreeError("worktree path is outside the configured worktree root")
        current = resolved
        while current != self.worktree_root:
            if current.exists() and current.is_symlink():
                raise WorktreeError("worktree path must not traverse a symlink")
            current = current.parent


def _git(
    path: Path, *args: str, check: bool = True
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=path,
        check=check,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="surrogateescape",
    )


def _repository_root(path: Path) -> Path:
    result = _git(path.expanduser().resolve(), "rev-parse", "--show-toplevel", check=False)
    if result.returncode != 0:
        raise WorktreeError("repository path is not inside a Git worktree")
    return Path(result.stdout.strip()).resolve()


def _resolve_commit(repository: Path, revision: str) -> str:
    result = _git(repository, "rev-parse", "--verify", f"{revision}^{{commit}}", check=False)
    if result.returncode != 0:
        raise WorktreeError("base revision does not exist or is not a commit")
    return result.stdout.strip()


def _worker_name(slice_id: str, attempt_id: str) -> str:
    values = (slice_id, attempt_id)
    if any(not value or not _IDENTIFIER.fullmatch(value) or ".." in value for value in values):
        raise WorktreeError("slice and attempt identifier must be safe non-empty identifiers")
    slug = "-".join(values).replace("/", "-").replace("_", "-").replace(".", "-")
    slug = re.sub(r"-+", "-", slug).strip("-").lower()
    if not slug:
        raise WorktreeError("slice and attempt identifier must produce a name")
    return slug


def _contains(parent: Path, child: Path) -> bool:
    try:
        child.relative_to(parent)
    except ValueError:
        return False
    return True


def _relative_path(value: str) -> str:
    path = Path(value)
    if not value or path.is_absolute() or ".." in path.parts or value in {".", ""}:
        raise WorktreeError("ownership paths must be confined repository-relative paths")
    return path.as_posix().rstrip("/")


def _path_overlap(left: str, right: str) -> bool:
    left_parts = Path(left).parts
    right_parts = Path(right).parts
    common = min(len(left_parts), len(right_parts))
    return left_parts[:common] == right_parts[:common]


def _branch_exists(repository: Path, branch: str) -> bool:
    return (
        _git(
            repository,
            "show-ref",
            "--verify",
            "--quiet",
            f"refs/heads/{branch}",
            check=False,
        ).returncode
        == 0
    )


def _registered_worktrees(repository: Path) -> Tuple[Path, ...]:
    output = _git(repository, "worktree", "list", "--porcelain").stdout
    return tuple(
        Path(line.removeprefix("worktree ")).resolve()
        for line in output.splitlines()
        if line.startswith("worktree ")
    )


def _dirty_paths(path: Path) -> Tuple[str, ...]:
    output = _git(path, "status", "--porcelain=v1", "-z").stdout
    paths = []
    for record in output.split("\0"):
        if not record:
            continue
        value = record[3:]
        if " -> " in value:
            value = value.split(" -> ", 1)[1]
        paths.append(value)
    return tuple(sorted(set(paths)))


def _head(path: Path) -> Optional[str]:
    result = _git(path, "rev-parse", "HEAD", check=False)
    return result.stdout.strip() if result.returncode == 0 else None
