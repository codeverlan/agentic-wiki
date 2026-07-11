from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from memwiki.coordinator_git import (
    GitOperation,
    GitRepositoryKind,
    PathOwnership,
    classify_git_repository,
)


def git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        check=check,
        capture_output=True,
        text=True,
    )


def initialize_repository(path: Path) -> str:
    path.mkdir()
    git(path, "init", "-b", "main")
    git(path, "config", "user.name", "Test User")
    git(path, "config", "user.email", "test@example.invalid")
    (path / "tracked.txt").write_text("initial\n", encoding="utf-8")
    git(path, "add", "tracked.txt")
    git(path, "commit", "-m", "initial")
    return git(path, "rev-parse", "HEAD").stdout.strip()


def test_classifies_clean_repository_with_stable_structured_data(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    head = initialize_repository(repo)
    git(repo, "remote", "add", "origin", "https://example.invalid/repo.git")

    state = classify_git_repository(repo)

    assert state.kind is GitRepositoryKind.REPOSITORY
    assert state.clean is True
    assert state.conflicted is False
    assert state.detached is False
    assert state.branch == "main"
    assert state.head == head
    assert state.operation is GitOperation.NONE
    assert state.ahead == 0
    assert state.behind == 0
    assert state.staged == ()
    assert state.unstaged == ()
    assert state.untracked == ()
    assert state.ignored == ()
    assert state.remotes[0].name == "origin"
    assert state.remotes[0].fetch_url == "https://example.invalid/repo.git"
    assert state.worktrees[0].path == repo.resolve().as_posix()
    assert state.to_dict() == state.to_dict()


def test_classifies_dirty_paths_and_slice_ownership(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    initialize_repository(repo)
    (repo / ".gitignore").write_text("ignored.log\n", encoding="utf-8")
    (repo / "tracked.txt").write_text("staged\n", encoding="utf-8")
    git(repo, "add", "tracked.txt", ".gitignore")
    (repo / "tracked.txt").write_text("unstaged\n", encoding="utf-8")
    (repo / "owned").mkdir()
    (repo / "owned" / "new.txt").write_text("new\n", encoding="utf-8")
    (repo / "independent.txt").write_text("independent\n", encoding="utf-8")
    (repo / "ignored.log").write_text("ignored\n", encoding="utf-8")

    state = classify_git_repository(
        repo,
        owned_paths=("owned", "tracked.txt"),
        candidate_paths=("owned/new.txt", "docs"),
    )

    assert state.clean is False
    assert state.staged == (".gitignore", "tracked.txt")
    assert state.unstaged == ("tracked.txt",)
    assert state.untracked == ("independent.txt", "owned/new.txt")
    assert state.ignored == ("ignored.log",)
    ownership = {item.path: item.ownership for item in state.path_ownership}
    assert ownership["tracked.txt"] is PathOwnership.OWNED
    assert ownership["owned/new.txt"] is PathOwnership.OWNED
    assert ownership["independent.txt"] is PathOwnership.UNOWNED
    candidates = {item.path: item.runnable for item in state.candidate_paths}
    assert candidates == {"docs": True, "owned/new.txt": False}


def test_classifies_conflicts_and_merge_in_progress(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    initialize_repository(repo)
    git(repo, "switch", "-c", "other")
    (repo / "tracked.txt").write_text("other\n", encoding="utf-8")
    git(repo, "commit", "-am", "other")
    git(repo, "switch", "main")
    (repo / "tracked.txt").write_text("main\n", encoding="utf-8")
    git(repo, "commit", "-am", "main")
    git(repo, "merge", "other", check=False)

    state = classify_git_repository(repo)

    assert state.conflicted is True
    assert state.conflicts == ("tracked.txt",)
    assert state.operation is GitOperation.MERGE


@pytest.mark.parametrize(
    ("marker", "expected"),
    [
        ("rebase-merge", GitOperation.REBASE),
        ("CHERRY_PICK_HEAD", GitOperation.CHERRY_PICK),
        ("BISECT_LOG", GitOperation.BISECT),
    ],
)
def test_detects_in_progress_operations_from_git_markers(
    tmp_path: Path, marker: str, expected: GitOperation
) -> None:
    repo = tmp_path / "repo"
    initialize_repository(repo)
    git_dir = Path(git(repo, "rev-parse", "--git-dir").stdout.strip())
    git_dir = git_dir if git_dir.is_absolute() else repo / git_dir
    marker_path = git_dir / marker
    if marker == "rebase-merge":
        marker_path.mkdir()
    else:
        marker_path.write_text("marker\n", encoding="utf-8")

    assert classify_git_repository(repo).operation is expected


def test_classifies_detached_head_and_nested_repository(tmp_path: Path) -> None:
    outer = tmp_path / "outer"
    head = initialize_repository(outer)
    nested = outer / "nested"
    nested_head = initialize_repository(nested)
    git(nested, "checkout", "--detach", nested_head)

    state = classify_git_repository(nested / "tracked.txt")

    assert state.root == nested.resolve().as_posix()
    assert state.head == nested_head
    assert state.detached is True
    assert state.branch is None
    assert state.head == head or state.head == nested_head


def test_classifies_non_repository_without_raising(tmp_path: Path) -> None:
    state = classify_git_repository(tmp_path)

    assert state.kind is GitRepositoryKind.NOT_REPOSITORY
    assert state.root is None
    assert state.clean is None
    assert state.branch is None
    assert state.head is None
    assert state.operation is GitOperation.NONE
    assert state.to_dict()["kind"] == "not_repository"
