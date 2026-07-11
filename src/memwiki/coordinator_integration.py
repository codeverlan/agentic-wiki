from __future__ import annotations

import fcntl
import hashlib
import json
import os
import subprocess
from contextlib import contextmanager
from dataclasses import dataclass
from enum import Enum
from pathlib import Path, PurePosixPath
from typing import Dict, Iterator, Mapping, Optional, Sequence, Tuple

from memwiki.coordinator_reports import WorkerReportAdmission
from memwiki.coordinator_validation import (
    ValidationCommand,
    ValidationResult,
    ValidationRunner,
)


class IntegrationStatus(str, Enum):
    INTEGRATED = "integrated"
    REJECTED = "rejected"
    STALE_BASE = "stale_base"
    INVALID_WORKTREE = "invalid_worktree"
    UNEXPECTED_DIFF = "unexpected_diff"
    PRE_VALIDATION_FAILED = "pre_validation_failed"
    CONFLICT = "conflict"
    POST_VALIDATION_FAILED = "post_validation_failed"
    ROLLED_BACK = "rolled_back"
    ROLLBACK_FAILED = "rollback_failed"
    OUTCOME_UNKNOWN = "outcome_unknown"


@dataclass(frozen=True)
class IntegrationRequest:
    integration_id: str
    slice_id: str
    admission: WorkerReportAdmission
    repository: Path
    worktree: Path
    base_revision: str
    head_revision: str
    expected_paths: Tuple[str, ...]
    pre_validation: Tuple[ValidationCommand, ...] = ()
    post_validation: Tuple[ValidationCommand, ...] = ()


@dataclass(frozen=True)
class IntegrationResult:
    integration_id: str
    status: IntegrationStatus
    before_revision: Optional[str]
    integrated_revision: Optional[str]
    final_revision: Optional[str]
    changed_paths: Tuple[str, ...]
    pre_validation: Tuple[ValidationResult, ...]
    post_validation: Tuple[ValidationResult, ...]
    blocker: Optional[Dict[str, object]]
    evidence: Dict[str, object]
    receipt_path: Path


class IntegrationController:
    """Integrate admitted worker commits into one shared branch at a time."""

    def __init__(self, repository: Path, *, crash_after_commit: bool = False) -> None:
        self.repository = _repository_root(repository)
        self._git_dir = Path(
            _git(self.repository, "rev-parse", "--absolute-git-dir").stdout.strip()
        )
        self._state_dir = self._git_dir / "agentic-wiki" / "integrations"
        self._lock_path = self._git_dir / "agentic-wiki" / "integration.lock"
        self._crash_after_commit = crash_after_commit

    def integrate(self, request: IntegrationRequest) -> IntegrationResult:
        self._validate_request(request)
        with self._serial_lock():
            return self._integrate_locked(request)

    def recover(self, integration_id: str) -> IntegrationResult:
        receipt_path = self._receipt_path(integration_id)
        if not receipt_path.is_file():
            raise ValueError("integration receipt does not exist")
        payload = json.loads(receipt_path.read_text())
        integrated = payload.get("integrated_revision")
        raw_status = str(payload["status"])
        if raw_status in {
            "integration_started",
            "commit_applied",
            "post_validation_started",
        }:
            recovered_status = IntegrationStatus.OUTCOME_UNKNOWN
        else:
            recovered_status = IntegrationStatus(raw_status)
        return IntegrationResult(
            integration_id=integration_id,
            status=recovered_status,
            before_revision=_optional_string(payload.get("before_revision")),
            integrated_revision=_optional_string(integrated),
            final_revision=_head(self.repository),
            changed_paths=tuple(payload.get("changed_paths", ())),
            pre_validation=(),
            post_validation=(),
            blocker=payload.get("blocker") if isinstance(payload.get("blocker"), dict) else None,
            evidence=dict(payload),
            receipt_path=receipt_path,
        )

    def _integrate_locked(self, request: IntegrationRequest) -> IntegrationResult:
        before = _head(self.repository)
        receipt_path = self._receipt_path(request.integration_id)
        existing = self._read_receipt(receipt_path)
        if existing is not None:
            return self.recover(request.integration_id)
        if not request.admission.integrable:
            return self._finish(
                request, IntegrationStatus.REJECTED, before, (), (), (),
                blocker={"category": "report_rejected", "reasons": list(request.admission.reasons)},
            )

        worktree_error = self._verify_worktree(request)
        if worktree_error is not None:
            status, reason = worktree_error
            return self._finish(
                request, status, before, (), (), (),
                blocker={"category": status.value, "reason": reason},
            )
        changed_paths = _changed_paths(request.worktree, request.base_revision, request.head_revision)
        if changed_paths != tuple(sorted(request.expected_paths)):
            return self._finish(
                request, IntegrationStatus.UNEXPECTED_DIFF, before, changed_paths, (), (),
                blocker={
                    "category": "unexpected_diff",
                    "expected_paths": list(sorted(request.expected_paths)),
                    "actual_paths": list(changed_paths),
                },
            )

        main_changes = _changed_paths(self.repository, request.base_revision, before)
        if before != request.base_revision and not _has_overlap(main_changes, changed_paths):
            return self._finish(
                request, IntegrationStatus.STALE_BASE, before, changed_paths, (), (),
                blocker={"category": "stale_base", "current_revision": before},
            )
        dirty = _dirty_paths(self.repository)
        if _has_overlap(dirty, changed_paths):
            return self._finish(
                request, IntegrationStatus.CONFLICT, before, changed_paths, (), (),
                blocker={"category": "integration_conflict", "paths": list(dirty)},
            )

        pre = self._validate(request.worktree, request.pre_validation)
        if not _all_passed(pre):
            return self._finish(
                request, IntegrationStatus.PRE_VALIDATION_FAILED, before, changed_paths, pre, (),
                blocker={"category": "pre_integration_validation"},
            )

        commits = _commits(request.worktree, request.base_revision, request.head_revision)
        self._write_receipt(
            receipt_path,
            self._evidence(request, "integration_started", before, None, changed_paths, None),
        )
        applied: list[str] = []
        for commit in commits:
            result = _git(self.repository, "cherry-pick", commit, check=False)
            if result.returncode != 0:
                conflicts = _conflicted_paths(self.repository)
                _git(self.repository, "cherry-pick", "--abort", check=False)
                return self._finish(
                    request, IntegrationStatus.CONFLICT, before, changed_paths, pre, (),
                    blocker={
                        "category": "integration_conflict",
                        "paths": list(conflicts),
                        "stderr_sha256": hashlib.sha256(result.stderr.encode()).hexdigest(),
                    },
                )
            applied.append(_head(self.repository))

        integrated = _head(self.repository)
        self._write_receipt(
            receipt_path,
            self._evidence(
                request, "commit_applied", before, integrated, changed_paths, None
            ),
        )
        if self._crash_after_commit:
            raise RuntimeError("injected crash after integration commit")

        self._write_receipt(
            receipt_path,
            self._evidence(
                request, "post_validation_started", before, integrated, changed_paths, None
            ),
        )
        post = self._validate(self.repository, request.post_validation)
        if _all_passed(post):
            return self._finish(
                request,
                IntegrationStatus.INTEGRATED,
                before,
                changed_paths,
                pre,
                post,
                integrated=integrated,
            )

        rollback = self._rollback(applied, integrated)
        status = IntegrationStatus.ROLLED_BACK if rollback else IntegrationStatus.ROLLBACK_FAILED
        return self._finish(
            request,
            status,
            before,
            changed_paths,
            pre,
            post,
            integrated=integrated,
            blocker={"category": "post_integration_validation", "rollback_succeeded": rollback},
        )

    def _verify_worktree(
        self, request: IntegrationRequest
    ) -> Optional[Tuple[IntegrationStatus, str]]:
        root = _repository_root(request.worktree)
        if root != request.worktree.resolve() or root == self.repository:
            return IntegrationStatus.INVALID_WORKTREE, "worker path is not a separate Git worktree"
        if _dirty_paths(request.worktree):
            return IntegrationStatus.INVALID_WORKTREE, "worker worktree is dirty"
        if _head(request.worktree) != request.head_revision:
            return IntegrationStatus.INVALID_WORKTREE, "worker HEAD does not match the report"
        if not _is_ancestor(request.worktree, request.base_revision, request.head_revision):
            return IntegrationStatus.INVALID_WORKTREE, "worker head does not descend from its base"
        if not _commits(request.worktree, request.base_revision, request.head_revision):
            return IntegrationStatus.INVALID_WORKTREE, "worker produced no commits"
        return None

    def _rollback(self, commits: Sequence[str], integrated: str) -> bool:
        if _head(self.repository) != integrated or _conflicted_paths(self.repository):
            return False
        for commit in reversed(commits):
            result = _git(self.repository, "revert", "--no-edit", commit, check=False)
            if result.returncode != 0:
                _git(self.repository, "revert", "--abort", check=False)
                return False
        return True

    def _validate(
        self, root: Path, commands: Sequence[ValidationCommand]
    ) -> Tuple[ValidationResult, ...]:
        runner = ValidationRunner(root)
        return tuple(runner.run(command) for command in commands)

    def _finish(
        self,
        request: IntegrationRequest,
        status: IntegrationStatus,
        before: str,
        changed_paths: Tuple[str, ...],
        pre: Tuple[ValidationResult, ...],
        post: Tuple[ValidationResult, ...],
        *,
        integrated: Optional[str] = None,
        blocker: Optional[Dict[str, object]] = None,
    ) -> IntegrationResult:
        receipt_path = self._receipt_path(request.integration_id)
        evidence = self._evidence(
            request, status.value, before, integrated, changed_paths, blocker
        )
        evidence["pre_validation"] = [result.to_dict() for result in pre]
        evidence["post_validation"] = [result.to_dict() for result in post]
        evidence["final_revision"] = _head(self.repository)
        self._write_receipt(receipt_path, evidence)
        return IntegrationResult(
            request.integration_id,
            status,
            before,
            integrated,
            _head(self.repository),
            changed_paths,
            pre,
            post,
            blocker,
            evidence,
            receipt_path,
        )

    @staticmethod
    def _evidence(
        request: IntegrationRequest,
        status: str,
        before: str,
        integrated: Optional[str],
        changed_paths: Tuple[str, ...],
        blocker: Optional[Mapping[str, object]],
    ) -> Dict[str, object]:
        return {
            "schema_version": 1,
            "integration_id": request.integration_id,
            "slice_id": request.slice_id,
            "report_sha256": request.admission.preserved.raw_sha256,
            "status": status,
            "before_revision": before,
            "worker_head_revision": request.head_revision,
            "integrated_revision": integrated,
            "changed_paths": list(changed_paths),
            "blocker": dict(blocker) if blocker is not None else None,
        }

    @contextmanager
    def _serial_lock(self) -> Iterator[None]:
        self._lock_path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(self._lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

    def _receipt_path(self, integration_id: str) -> Path:
        allowed = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_."
        if not integration_id or any(character not in allowed for character in integration_id):
            raise ValueError("integration id contains unsafe characters")
        return self._state_dir / f"{integration_id}.json"

    @staticmethod
    def _read_receipt(path: Path) -> Optional[Dict[str, object]]:
        if not path.exists():
            return None
        payload = json.loads(path.read_text())
        return dict(payload) if isinstance(payload, dict) else None

    @staticmethod
    def _write_receipt(path: Path, payload: Mapping[str, object]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n"
        temporary.write_text(encoded)
        os.replace(temporary, path)

    def _validate_request(self, request: IntegrationRequest) -> None:
        if request.repository.resolve() != self.repository:
            raise ValueError("request repository does not match the controller")
        if not request.expected_paths:
            raise ValueError("expected paths must not be empty")
        normalized = tuple(sorted({_relative_path(path) for path in request.expected_paths}))
        if normalized != tuple(sorted(request.expected_paths)):
            raise ValueError("expected paths must be normalized and unique")


def _git(path: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
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
    result = _git(path.resolve(), "rev-parse", "--show-toplevel", check=False)
    if result.returncode != 0:
        raise ValueError("path is not inside a Git repository")
    return Path(result.stdout.strip()).resolve()


def _head(path: Path) -> str:
    return _git(path, "rev-parse", "HEAD").stdout.strip()


def _is_ancestor(path: Path, ancestor: str, descendant: str) -> bool:
    return _git(path, "merge-base", "--is-ancestor", ancestor, descendant, check=False).returncode == 0


def _commits(path: Path, base: str, head: str) -> Tuple[str, ...]:
    output = _git(path, "rev-list", "--reverse", f"{base}..{head}").stdout
    return tuple(filter(None, output.splitlines()))


def _changed_paths(path: Path, base: str, head: str) -> Tuple[str, ...]:
    output = _git(path, "diff", "--name-only", "-z", base, head).stdout
    return tuple(sorted(filter(None, output.split("\0"))))


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


def _conflicted_paths(path: Path) -> Tuple[str, ...]:
    output = _git(path, "diff", "--name-only", "--diff-filter=U", "-z").stdout
    return tuple(sorted(filter(None, output.split("\0"))))


def _relative_path(value: str) -> str:
    path = PurePosixPath(value)
    if path.is_absolute() or not path.parts or ".." in path.parts or value in {"", "."}:
        raise ValueError("path must be confined and repository-relative")
    return path.as_posix().rstrip("/")


def _overlap(left: str, right: str) -> bool:
    left_parts = PurePosixPath(left).parts
    right_parts = PurePosixPath(right).parts
    common = min(len(left_parts), len(right_parts))
    return left_parts[:common] == right_parts[:common]


def _has_overlap(left: Sequence[str], right: Sequence[str]) -> bool:
    return any(_overlap(first, second) for first in left for second in right)


def _all_passed(results: Sequence[ValidationResult]) -> bool:
    return all(result.outcome == "passed" for result in results)


def _optional_string(value: object) -> Optional[str]:
    return value if isinstance(value, str) else None
