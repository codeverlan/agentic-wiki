from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from memwiki.coordinator_worktrees import (
    WorktreeDisposition,
    WorktreeError,
    WorktreeIsolationAdapter,
)


def git(path: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=path, check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    root.mkdir()
    git(root, "init", "-b", "main")
    git(root, "config", "user.name", "Test User")
    git(root, "config", "user.email", "test@example.invalid")
    (root / "tracked.txt").write_text("base\n", encoding="utf-8")
    git(root, "add", "tracked.txt")
    git(root, "commit", "-m", "base")
    return root


def test_create_writing_worktree_is_deterministic_and_preserves_main_dirt(
    repository: Path, tmp_path: Path
) -> None:
    (repository / "unowned.txt").write_text("preserve\n", encoding="utf-8")
    base = git(repository, "rev-parse", "HEAD")
    adapter = WorktreeIsolationAdapter(repository, tmp_path / "worker-trees")

    result = adapter.create("AC-015", "attempt/one", base_revision=base)

    assert result.branch == "codex/worker/ac-015-attempt-one"
    assert result.path == (tmp_path / "worker-trees" / "ac-015-attempt-one").resolve()
    assert git(result.path, "rev-parse", "HEAD") == base
    assert (repository / "unowned.txt").read_text(encoding="utf-8") == "preserve\n"
    assert "?? unowned.txt" in git(repository, "status", "--short")


def test_read_only_uses_shared_checkout_without_creating_branch(
    repository: Path, tmp_path: Path
) -> None:
    adapter = WorktreeIsolationAdapter(repository, tmp_path / "worker-trees")
    before = git(repository, "branch", "--format=%(refname:short)").splitlines()

    result = adapter.shared_read_only()

    assert result.path == repository.resolve()
    assert result.branch == "main"
    assert result.read_only is True
    assert git(repository, "branch", "--format=%(refname:short)").splitlines() == before


def test_rejects_wrong_or_unknown_base_revision(repository: Path, tmp_path: Path) -> None:
    adapter = WorktreeIsolationAdapter(repository, tmp_path / "worker-trees")

    with pytest.raises(WorktreeError, match="base revision does not exist"):
        adapter.create("slice", "attempt", base_revision="0" * 40)


def test_branch_collision_is_rejected(repository: Path, tmp_path: Path) -> None:
    base = git(repository, "rev-parse", "HEAD")
    adapter = WorktreeIsolationAdapter(repository, tmp_path / "worker-trees")
    adapter.create("slice", "attempt", base_revision=base)

    with pytest.raises(WorktreeError, match="already exists"):
        adapter.create("slice", "attempt", base_revision=base)


def test_worktree_root_and_identifiers_are_confined(repository: Path, tmp_path: Path) -> None:
    with pytest.raises(WorktreeError, match="must not be inside the repository"):
        WorktreeIsolationAdapter(repository, repository / ".workers")
    adapter = WorktreeIsolationAdapter(repository, tmp_path / "worker-trees")

    with pytest.raises(WorktreeError, match="identifier"):
        adapter.create("..", "escape", base_revision=git(repository, "rev-parse", "HEAD"))


def test_cleanup_removes_clean_worktree_and_branch(repository: Path, tmp_path: Path) -> None:
    base = git(repository, "rev-parse", "HEAD")
    adapter = WorktreeIsolationAdapter(repository, tmp_path / "worker-trees")
    created = adapter.create("slice", "clean", base_revision=base)

    result = adapter.cleanup(created, successful=True)

    assert result.disposition is WorktreeDisposition.REMOVED
    assert not created.path.exists()
    assert created.branch not in git(repository, "branch", "--format=%(refname:short)").splitlines()


def test_cleanup_preserves_dirty_or_failed_evidence(repository: Path, tmp_path: Path) -> None:
    base = git(repository, "rev-parse", "HEAD")
    adapter = WorktreeIsolationAdapter(repository, tmp_path / "worker-trees")
    dirty = adapter.create("slice", "dirty", base_revision=base)
    (dirty.path / "evidence.txt").write_text("retain\n", encoding="utf-8")

    dirty_result = adapter.cleanup(dirty, successful=True)
    failed_result = adapter.cleanup(dirty, successful=False)

    assert dirty_result.disposition is WorktreeDisposition.PRESERVED_DIRTY
    assert failed_result.disposition is WorktreeDisposition.PRESERVED_FAILED
    assert dirty.path.exists()
    assert git(dirty.path, "status", "--short") == "?? evidence.txt"


def test_stale_detection_and_non_destructive_recovery(repository: Path, tmp_path: Path) -> None:
    base = git(repository, "rev-parse", "HEAD")
    adapter = WorktreeIsolationAdapter(repository, tmp_path / "worker-trees")
    active = adapter.create("slice", "active", base_revision=base)
    stale = adapter.inspect(active, active_branches=())
    live = adapter.inspect(active, active_branches=(active.branch,))

    assert stale.stale is True
    assert live.stale is False
    assert adapter.recover(active, active_branches=()).disposition is WorktreeDisposition.REMOVED


def test_recovery_preserves_stale_dirty_worktree(repository: Path, tmp_path: Path) -> None:
    base = git(repository, "rev-parse", "HEAD")
    adapter = WorktreeIsolationAdapter(repository, tmp_path / "worker-trees")
    created = adapter.create("slice", "stale", base_revision=base)
    (created.path / "tracked.txt").write_text("worker change\n", encoding="utf-8")

    result = adapter.recover(created, active_branches=())

    assert result.disposition is WorktreeDisposition.PRESERVED_DIRTY
    assert created.path.exists()


def test_cleanup_refuses_foreign_path(repository: Path, tmp_path: Path) -> None:
    adapter = WorktreeIsolationAdapter(repository, tmp_path / "worker-trees")
    shared = adapter.shared_read_only()

    with pytest.raises(WorktreeError, match="outside the configured worktree root"):
        adapter.cleanup(shared, successful=True)


def test_changed_path_verification_rejects_forbidden_and_unowned_edits(
    repository: Path, tmp_path: Path
) -> None:
    base = git(repository, "rev-parse", "HEAD")
    adapter = WorktreeIsolationAdapter(repository, tmp_path / "worker-trees")
    created = adapter.create("slice", "ownership", base_revision=base)
    (created.path / "src").mkdir()
    (created.path / "src" / "owned.py").write_text("owned\n", encoding="utf-8")
    (created.path / "secrets.txt").write_text("forbidden\n", encoding="utf-8")

    with pytest.raises(WorktreeError, match="forbidden path: secrets.txt"):
        adapter.verify_changed_paths(
            created,
            owned_paths=("src",),
            forbidden_paths=("secrets.txt",),
        )

    (created.path / "secrets.txt").unlink()
    (created.path / "docs.txt").write_text("unowned\n", encoding="utf-8")
    with pytest.raises(WorktreeError, match="outside owned paths: docs.txt"):
        adapter.verify_changed_paths(created, owned_paths=("src",), forbidden_paths=())
