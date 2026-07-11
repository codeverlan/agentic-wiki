from __future__ import annotations

import os
import subprocess
from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path, PurePosixPath
from typing import Any, Optional, Sequence, Tuple


class GitRepositoryKind(str, Enum):
    REPOSITORY = "repository"
    NOT_REPOSITORY = "not_repository"


class GitOperation(str, Enum):
    NONE = "none"
    MERGE = "merge"
    REBASE = "rebase"
    CHERRY_PICK = "cherry_pick"
    BISECT = "bisect"


class PathOwnership(str, Enum):
    OWNED = "owned"
    UNOWNED = "unowned"


@dataclass(frozen=True)
class GitRemote:
    name: str
    fetch_url: str
    push_url: str


@dataclass(frozen=True)
class GitWorktree:
    path: str
    head: Optional[str]
    branch: Optional[str]
    detached: bool
    bare: bool
    locked: bool
    prunable: bool


@dataclass(frozen=True)
class PathOwnershipRecord:
    path: str
    ownership: PathOwnership


@dataclass(frozen=True)
class CandidatePathRecord:
    path: str
    runnable: bool
    overlapping_dirty_paths: Tuple[str, ...]


@dataclass(frozen=True)
class GitRepositoryState:
    kind: GitRepositoryKind
    root: Optional[str]
    git_dir: Optional[str]
    branch: Optional[str]
    head: Optional[str]
    detached: bool
    clean: Optional[bool]
    conflicted: bool
    operation: GitOperation
    ahead: Optional[int]
    behind: Optional[int]
    remotes: Tuple[GitRemote, ...]
    worktrees: Tuple[GitWorktree, ...]
    staged: Tuple[str, ...]
    unstaged: Tuple[str, ...]
    untracked: Tuple[str, ...]
    ignored: Tuple[str, ...]
    conflicts: Tuple[str, ...]
    path_ownership: Tuple[PathOwnershipRecord, ...]
    candidate_paths: Tuple[CandidatePathRecord, ...]

    def to_dict(self) -> dict[str, Any]:
        return _enum_values(asdict(self))


def _enum_values(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {key: _enum_values(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_enum_values(item) for item in value]
    return value


def _run(path: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=path,
        check=check,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="surrogateescape",
    )


def _normalize_input_path(path: Path) -> Path:
    resolved = path.resolve()
    return resolved.parent if resolved.is_file() else resolved


def _normalize_relative_path(value: str) -> str:
    normalized = PurePosixPath(value.replace(os.sep, "/")).as_posix()
    if normalized in {"", "."} or normalized == ".." or normalized.startswith("../"):
        raise ValueError(f"path must be repository-relative: {value!r}")
    return normalized.rstrip("/")


def _overlaps(left: str, right: str) -> bool:
    return left == right or left.startswith(f"{right}/") or right.startswith(f"{left}/")


def _operation(git_dir: Path, common_dir: Path) -> GitOperation:
    if (git_dir / "MERGE_HEAD").exists():
        return GitOperation.MERGE
    if (git_dir / "rebase-merge").exists() or (git_dir / "rebase-apply").exists():
        return GitOperation.REBASE
    if (git_dir / "CHERRY_PICK_HEAD").exists():
        return GitOperation.CHERRY_PICK
    if (git_dir / "BISECT_LOG").exists() or (common_dir / "BISECT_LOG").exists():
        return GitOperation.BISECT
    return GitOperation.NONE


def _status(root: Path) -> tuple[
    Optional[str], Optional[int], Optional[int], set[str], set[str], set[str], set[str]
]:
    output = _run(
        root,
        "status",
        "--porcelain=v2",
        "--branch",
        "--untracked-files=all",
        "-z",
    ).stdout
    branch: Optional[str] = None
    ahead: Optional[int] = 0
    behind: Optional[int] = 0
    staged: set[str] = set()
    unstaged: set[str] = set()
    untracked: set[str] = set()
    conflicts: set[str] = set()
    records = output.split("\0")
    index = 0
    while index < len(records):
        record = records[index]
        index += 1
        if not record:
            continue
        if record.startswith("# branch.head "):
            value = record.removeprefix("# branch.head ")
            branch = None if value == "(detached)" else value
        elif record.startswith("# branch.ab "):
            ahead_text, behind_text = record.removeprefix("# branch.ab ").split()
            ahead, behind = int(ahead_text[1:]), int(behind_text[1:])
        elif record.startswith("1 ") or record.startswith("2 "):
            fields = record.split(" ", 8 if record.startswith("1 ") else 9)
            xy = fields[1]
            path = fields[-1]
            if xy[0] != ".":
                staged.add(path)
            if xy[1] != ".":
                unstaged.add(path)
            if record.startswith("2 "):
                index += 1  # The next NUL record is the original rename path.
        elif record.startswith("u "):
            path = record.split(" ", 10)[-1]
            conflicts.add(path)
            staged.add(path)
            unstaged.add(path)
        elif record.startswith("? "):
            untracked.add(record[2:])
    return branch, ahead, behind, staged, unstaged, untracked, conflicts


def _ignored(root: Path) -> set[str]:
    output = _run(
        root,
        "status",
        "--porcelain=v1",
        "--ignored",
        "--untracked-files=all",
        "-z",
    ).stdout
    return {record[3:] for record in output.split("\0") if record.startswith("!! ")}


def _remotes(root: Path) -> Tuple[GitRemote, ...]:
    names = sorted(filter(None, _run(root, "remote").stdout.splitlines()))
    return tuple(
        GitRemote(
            name=name,
            fetch_url=_run(root, "remote", "get-url", name).stdout.strip(),
            push_url=_run(root, "remote", "get-url", "--push", name).stdout.strip(),
        )
        for name in names
    )


def _worktrees(root: Path) -> Tuple[GitWorktree, ...]:
    blocks = _run(root, "worktree", "list", "--porcelain", "-z").stdout.split("\0\0")
    worktrees = []
    for block in blocks:
        fields: dict[str, str] = {}
        flags: set[str] = set()
        for line in filter(None, block.split("\0")):
            key, _, value = line.partition(" ")
            if value:
                fields[key] = value
            else:
                flags.add(key)
        if "worktree" not in fields:
            continue
        branch = fields.get("branch")
        if branch and branch.startswith("refs/heads/"):
            branch = branch.removeprefix("refs/heads/")
        worktrees.append(
            GitWorktree(
                path=Path(fields["worktree"]).resolve().as_posix(),
                head=fields.get("HEAD"),
                branch=branch,
                detached="detached" in flags,
                bare="bare" in flags,
                locked="locked" in flags,
                prunable="prunable" in flags,
            )
        )
    return tuple(sorted(worktrees, key=lambda item: item.path))


def _non_repository() -> GitRepositoryState:
    return GitRepositoryState(
        kind=GitRepositoryKind.NOT_REPOSITORY,
        root=None,
        git_dir=None,
        branch=None,
        head=None,
        detached=False,
        clean=None,
        conflicted=False,
        operation=GitOperation.NONE,
        ahead=None,
        behind=None,
        remotes=(),
        worktrees=(),
        staged=(),
        unstaged=(),
        untracked=(),
        ignored=(),
        conflicts=(),
        path_ownership=(),
        candidate_paths=(),
    )


def classify_git_repository(
    path: Path,
    *,
    owned_paths: Sequence[str] = (),
    candidate_paths: Sequence[str] = (),
) -> GitRepositoryState:
    """Return a deterministic, read-only snapshot of repository and path state."""
    query_path = _normalize_input_path(path)
    probe = _run(query_path, "rev-parse", "--show-toplevel", check=False)
    if probe.returncode != 0:
        return _non_repository()

    root = Path(probe.stdout.strip()).resolve()
    git_dir_text = _run(root, "rev-parse", "--absolute-git-dir").stdout.strip()
    common_dir_text = _run(root, "rev-parse", "--git-common-dir").stdout.strip()
    git_dir = Path(git_dir_text).resolve()
    common_dir = Path(common_dir_text)
    if not common_dir.is_absolute():
        common_dir = (root / common_dir).resolve()

    branch, ahead, behind, staged, unstaged, untracked, conflicts = _status(root)
    ignored = _ignored(root)
    head_result = _run(root, "rev-parse", "--verify", "HEAD", check=False)
    head = head_result.stdout.strip() if head_result.returncode == 0 else None
    dirty_paths = staged | unstaged | untracked | conflicts
    normalized_owned = tuple(sorted({_normalize_relative_path(item) for item in owned_paths}))
    ownership = tuple(
        PathOwnershipRecord(
            path=item,
            ownership=(
                PathOwnership.OWNED
                if any(_overlaps(item, owned) for owned in normalized_owned)
                else PathOwnership.UNOWNED
            ),
        )
        for item in sorted(dirty_paths)
    )
    candidates = []
    for candidate in sorted({_normalize_relative_path(item) for item in candidate_paths}):
        overlap = tuple(sorted(item for item in dirty_paths if _overlaps(candidate, item)))
        candidates.append(CandidatePathRecord(candidate, not overlap, overlap))

    return GitRepositoryState(
        kind=GitRepositoryKind.REPOSITORY,
        root=root.as_posix(),
        git_dir=git_dir.as_posix(),
        branch=branch,
        head=head,
        detached=branch is None and head is not None,
        clean=not dirty_paths,
        conflicted=bool(conflicts),
        operation=_operation(git_dir, common_dir),
        ahead=ahead,
        behind=behind,
        remotes=_remotes(root),
        worktrees=_worktrees(root),
        staged=tuple(sorted(staged)),
        unstaged=tuple(sorted(unstaged)),
        untracked=tuple(sorted(untracked)),
        ignored=tuple(sorted(ignored)),
        conflicts=tuple(sorted(conflicts)),
        path_ownership=ownership,
        candidate_paths=tuple(candidates),
    )
