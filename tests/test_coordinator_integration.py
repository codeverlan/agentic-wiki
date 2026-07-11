from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from memwiki.coordinator_integration import (
    IntegrationController,
    IntegrationRequest,
    IntegrationStatus,
)
from memwiki.coordinator_reports import PreservedReportEvidence, WorkerReportAdmission
from memwiki.coordinator_validation import ValidationCommand


def git(path: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=path, check=True, capture_output=True, text=True
    )
    return result.stdout.strip()


def write(path: Path, relative: str, content: str) -> None:
    target = path / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content)


@pytest.fixture
def repository(tmp_path: Path) -> tuple[Path, Path, str]:
    main = tmp_path / "main"
    worker = tmp_path / "worker"
    main.mkdir()
    git(main, "init", "-b", "main")
    git(main, "config", "user.name", "Test User")
    git(main, "config", "user.email", "test@example.invalid")
    write(main, "base.txt", "base\n")
    write(main, "notes.txt", "original\n")
    git(main, "add", ".")
    git(main, "commit", "-m", "base")
    base = git(main, "rev-parse", "HEAD")
    git(main, "worktree", "add", "-b", "codex/worker/ac-018", str(worker), base)
    return main, worker, base


def admitted(report_id: str = "report-1") -> WorkerReportAdmission:
    return WorkerReportAdmission(
        integrable=True,
        reasons=(),
        preserved=PreservedReportEvidence(
            payload={"report_id": report_id},
            raw_sha256="sha256:" + "a" * 64,
            report_hash="sha256:" + "b" * 64,
            received_at=__import__("datetime").datetime.now(
                __import__("datetime").timezone.utc
            ),
        ),
    )


def command(code: str) -> ValidationCommand:
    return ValidationCommand(
        argv=(sys.executable, "-c", code), timeout_seconds=5, max_output_bytes=4096
    )


def request(
    main: Path,
    worker: Path,
    base: str,
    *,
    paths: tuple[str, ...] = ("feature.txt",),
    pre: tuple[ValidationCommand, ...] = (),
    post: tuple[ValidationCommand, ...] = (),
) -> IntegrationRequest:
    return IntegrationRequest(
        integration_id="integration-1",
        slice_id="AC-018",
        admission=admitted(),
        repository=main,
        worktree=worker,
        base_revision=base,
        head_revision=git(worker, "rev-parse", "HEAD"),
        expected_paths=paths,
        pre_validation=pre,
        post_validation=post,
    )


def commit_worker(worker: Path, relative: str = "feature.txt", content: str = "ok\n") -> str:
    write(worker, relative, content)
    git(worker, "add", relative)
    git(worker, "commit", "-m", "worker change")
    return git(worker, "rev-parse", "HEAD")


def test_integrates_validated_report_serially(repository: tuple[Path, Path, str]) -> None:
    main, worker, base = repository
    commit_worker(worker)
    result = IntegrationController(main).integrate(
        request(
            main,
            worker,
            base,
            pre=(command("assert open('feature.txt').read() == 'ok\\n'"),),
            post=(command("assert open('feature.txt').read() == 'ok\\n'"),),
        )
    )

    assert result.status is IntegrationStatus.INTEGRATED
    assert (main / "feature.txt").read_text() == "ok\n"
    assert git(main, "rev-parse", "HEAD") == result.integrated_revision
    receipt = json.loads(result.receipt_path.read_text())
    assert receipt["status"] == "integrated"
    assert receipt["before_revision"] == base
    assert len(result.pre_validation) == len(result.post_validation) == 1


def test_rejects_stale_base_without_mutation(repository: tuple[Path, Path, str]) -> None:
    main, worker, base = repository
    commit_worker(worker)
    write(main, "main.txt", "advance\n")
    git(main, "add", "main.txt")
    git(main, "commit", "-m", "advance main")
    before = git(main, "rev-parse", "HEAD")

    result = IntegrationController(main).integrate(request(main, worker, base))

    assert result.status is IntegrationStatus.STALE_BASE
    assert git(main, "rev-parse", "HEAD") == before
    assert not (main / "feature.txt").exists()


def test_conflict_becomes_blocker_and_preserves_unrelated_dirt(
    repository: tuple[Path, Path, str],
) -> None:
    main, worker, base = repository
    commit_worker(worker, "base.txt", "worker\n")
    write(main, "base.txt", "main\n")
    git(main, "add", "base.txt")
    git(main, "commit", "-m", "conflicting main")
    write(main, "notes.txt", "keep me\n")
    before = git(main, "rev-parse", "HEAD")

    result = IntegrationController(main).integrate(
        request(main, worker, base, paths=("base.txt",))
    )

    assert result.status is IntegrationStatus.CONFLICT
    assert result.blocker is not None
    assert result.blocker["category"] == "integration_conflict"
    assert git(main, "rev-parse", "HEAD") == before
    assert (main / "notes.txt").read_text() == "keep me\n"
    assert "CHERRY_PICK_HEAD" not in git(main, "status", "--porcelain=v2", "--branch")


def test_failed_pre_validation_never_mutates_main(
    repository: tuple[Path, Path, str],
) -> None:
    main, worker, base = repository
    commit_worker(worker)
    result = IntegrationController(main).integrate(
        request(main, worker, base, pre=(command("raise SystemExit(3)"),))
    )
    assert result.status is IntegrationStatus.PRE_VALIDATION_FAILED
    assert git(main, "rev-parse", "HEAD") == base


def test_failed_post_validation_reverts_only_integrated_change(
    repository: tuple[Path, Path, str],
) -> None:
    main, worker, base = repository
    commit_worker(worker)
    write(main, "notes.txt", "keep me\n")

    result = IntegrationController(main).integrate(
        request(main, worker, base, post=(command("raise SystemExit(4)"),))
    )

    assert result.status is IntegrationStatus.ROLLED_BACK
    assert not (main / "feature.txt").exists()
    assert (main / "notes.txt").read_text() == "keep me\n"
    assert git(main, "rev-parse", "HEAD") != base
    assert "Revert" in git(main, "log", "-1", "--pretty=%s")


def test_crash_after_commit_is_recoverable_from_receipt(
    repository: tuple[Path, Path, str],
) -> None:
    main, worker, base = repository
    commit_worker(worker)
    controller = IntegrationController(main, crash_after_commit=True)

    with pytest.raises(RuntimeError, match="injected crash after integration commit"):
        controller.integrate(request(main, worker, base))

    receipt_path = main / ".git" / "agentic-wiki" / "integrations" / "integration-1.json"
    receipt = json.loads(receipt_path.read_text())
    assert receipt["status"] == "commit_applied"
    assert git(main, "rev-parse", "HEAD") == receipt["integrated_revision"]

    recovered = IntegrationController(main).recover("integration-1")
    assert recovered.status is IntegrationStatus.OUTCOME_UNKNOWN
    assert recovered.integrated_revision == git(main, "rev-parse", "HEAD")


def test_rejects_unadmitted_report_and_unexpected_diff(
    repository: tuple[Path, Path, str],
) -> None:
    main, worker, base = repository
    commit_worker(worker)
    denied = WorkerReportAdmission(False, ("bad report",), admitted().preserved)
    invalid = request(main, worker, base)
    invalid = IntegrationRequest(**{**invalid.__dict__, "admission": denied})
    assert IntegrationController(main).integrate(invalid).status is IntegrationStatus.REJECTED

    wrong_paths = replace(
        request(main, worker, base, paths=("other.txt",)),
        integration_id="integration-2",
    )
    result = IntegrationController(main).integrate(wrong_paths)
    assert result.status is IntegrationStatus.UNEXPECTED_DIFF
    assert git(main, "rev-parse", "HEAD") == base
