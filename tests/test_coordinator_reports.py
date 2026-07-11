from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

import pytest

from memwiki.coordinator_assignments import (
    AssignmentEnvelope,
    BoundedContextManifest,
    ReportContract,
)
from memwiki.coordinator_reports import ReportAdmissionError, WorkerReportAdmitter
from memwiki.coordinator_slice_leases import SliceLease
from memwiki.coordinator_worktrees import WorkerWorktree, WorktreeInspection

NOW = datetime(2026, 7, 11, 12, tzinfo=timezone.utc)


def _sha(value: bytes) -> str:
    return f"sha256:{hashlib.sha256(value).hexdigest()}"


def _assignment() -> AssignmentEnvelope:
    return AssignmentEnvelope.create(
        assignment_id="assignment-1",
        slice_id="AC-016",
        worker_id="worker-1",
        context=BoundedContextManifest.create(
            artifacts=["plan.json"], context_hashes=[_sha(b"plan")], token_budget=1000
        ),
        owned_paths=["src/memwiki/coordinator_reports.py", "tests/test_coordinator_reports.py"],
        forbidden_paths=["wiki"],
        base_revision="a" * 40,
        required_tests=["pytest"],
        permissions=["write"],
        lease_policy="exclusive",
        report_contract=ReportContract(
            destination="reports/AC-016.json",
            required_fields=["summary", "changed_files", "tests"],
            schema_version=1,
        ),
        issued_at=NOW,
        acceptance_deadline=NOW + timedelta(minutes=5),
    )


def _lease(assignment: AssignmentEnvelope) -> SliceLease:
    return SliceLease(
        assignment_id=assignment.assignment_id,
        assignment_hash=assignment.assignment_hash,
        slice_id=assignment.slice_id,
        worker_id=assignment.worker_id,
        owned_paths=assignment.owned_paths,
        base_revision=assignment.base_revision,
        attempt=2,
        coordinator_fencing_token=7,
        generation=3,
        lease_token=_sha(b"lease"),
        status="active",
        acquired_at=NOW,
        acknowledged_at=NOW,
        heartbeat_at=NOW,
        expires_at=NOW + timedelta(hours=1),
    )


def _inspection(root: Path, assignment: AssignmentEnvelope) -> WorktreeInspection:
    worktree = WorkerWorktree(
        path=root,
        branch="codex/worker/ac-016-attempt-2",
        base_revision=assignment.base_revision,
        head_revision="b" * 40,
    )
    return WorktreeInspection(
        worktree=worktree,
        registered=True,
        exists=True,
        dirty_paths=assignment.owned_paths,
        stale=False,
    )


def _report(root: Path, assignment: AssignmentEnvelope, lease: SliceLease) -> dict[str, object]:
    changed = []
    for relative in assignment.owned_paths:
        content = root.joinpath(relative).read_bytes()
        changed.append({"path": relative, "sha256": _sha(content)})
    context_hash = _sha("\n".join(assignment.context.context_hashes).encode())
    report: dict[str, object] = {
        "schema_version": 1,
        "report_id": "report-1",
        "assignment_id": assignment.assignment_id,
        "assignment_hash": assignment.assignment_hash,
        "slice_id": assignment.slice_id,
        "worker_id": assignment.worker_id,
        "attempt": lease.attempt,
        "lease_token": lease.lease_token,
        "coordinator_fencing_token": lease.coordinator_fencing_token,
        "base_revision": assignment.base_revision,
        "context_hash": context_hash,
        "submitted_at": (NOW + timedelta(minutes=1)).isoformat(),
        "outcome": "success",
        "summary": "Implemented report admission.",
        "changed_files": changed,
        "tests": [{"name": "pytest", "status": "passed", "evidence_sha256": _sha(b"ok")}],
        "blockers": [],
        "memory_delta": [],
        "capability_evidence": [],
        "worktree": {
            "branch": "codex/worker/ac-016-attempt-2",
            "head_revision": "b" * 40,
            "dirty_paths": list(assignment.owned_paths),
            "conflicted": False,
        },
    }
    report["report_hash"] = WorkerReportAdmitter.report_hash(report)
    return report


@pytest.fixture
def case(tmp_path: Path) -> tuple[AssignmentEnvelope, SliceLease, WorktreeInspection, dict[str, object]]:
    assignment = _assignment()
    for relative, content in zip(assignment.owned_paths, (b"module", b"tests")):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    lease = _lease(assignment)
    return assignment, lease, _inspection(tmp_path, assignment), _report(tmp_path, assignment, lease)


def test_accepts_complete_current_report_and_renders_semantic_html(
    case: tuple[AssignmentEnvelope, SliceLease, WorktreeInspection, dict[str, object]],
) -> None:
    assignment, lease, inspection, report = case
    result = WorkerReportAdmitter().admit(
        report,
        assignment=assignment,
        lease=lease,
        inspection=inspection,
        current_coordinator_fencing_token=7,
        received_at=NOW + timedelta(minutes=2),
    )

    assert result.integrable is True
    assert result.reasons == ()
    assert result.preserved.report_hash == report["report_hash"]
    assert result.preserved.payload == report
    html = result.to_html()
    assert '<article class="worker-report"' in html
    assert "Implemented report admission." in html
    assert "application/ld+json" in html


Mutation = Callable[
    [AssignmentEnvelope, SliceLease, WorktreeInspection, dict[str, object]],
    tuple[AssignmentEnvelope, SliceLease, WorktreeInspection, dict[str, object], int],
]


def _mutate_report(field: str, value: object) -> Mutation:
    def mutate(
        assignment: AssignmentEnvelope,
        lease: SliceLease,
        inspection: WorktreeInspection,
        report: dict[str, object],
    ) -> tuple[AssignmentEnvelope, SliceLease, WorktreeInspection, dict[str, object], int]:
        report[field] = value
        report["report_hash"] = WorkerReportAdmitter.report_hash(report)
        return assignment, lease, inspection, report, 7

    return mutate


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        (_mutate_report("assignment_id", "wrong"), "assignment identity mismatch"),
        (_mutate_report("attempt", 1), "attempt mismatch"),
        (_mutate_report("lease_token", "sha256:" + "0" * 64), "lease token mismatch"),
        (_mutate_report("base_revision", "c" * 40), "base revision mismatch"),
        (_mutate_report("context_hash", _sha(b"wrong")), "context hash mismatch"),
    ],
)
def test_rejects_identity_and_integrity_mismatches_but_preserves_evidence(
    case: tuple[AssignmentEnvelope, SliceLease, WorktreeInspection, dict[str, object]],
    mutation: Mutation,
    reason: str,
) -> None:
    assignment, lease, inspection, report = case
    assignment, lease, inspection, report, fence = mutation(assignment, lease, inspection, report)
    result = WorkerReportAdmitter().admit(
        report,
        assignment=assignment,
        lease=lease,
        inspection=inspection,
        current_coordinator_fencing_token=fence,
        received_at=NOW + timedelta(minutes=2),
    )
    assert result.integrable is False
    assert reason in result.reasons
    assert result.preserved.payload == report


def test_rejects_stale_late_or_inactive_lease(
    case: tuple[AssignmentEnvelope, SliceLease, WorktreeInspection, dict[str, object]],
) -> None:
    assignment, lease, inspection, report = case
    result = WorkerReportAdmitter().admit(
        report,
        assignment=assignment,
        lease=replace(lease, status="superseded"),
        inspection=inspection,
        current_coordinator_fencing_token=8,
        received_at=lease.expires_at + timedelta(seconds=1),
    )
    assert result.integrable is False
    assert "stale coordinator fencing token" in result.reasons
    assert "lease is not active" in result.reasons
    assert "report arrived after lease expiry" in result.reasons


def test_rejects_hash_mismatch_out_of_scope_and_worktree_mismatch(
    case: tuple[AssignmentEnvelope, SliceLease, WorktreeInspection, dict[str, object]],
) -> None:
    assignment, lease, inspection, report = case
    changed = report["changed_files"]
    assert isinstance(changed, list)
    assert isinstance(changed[0], dict)
    changed[0]["sha256"] = _sha(b"tampered")
    changed.append({"path": "README.md", "sha256": _sha(b"outside")})
    worktree = report["worktree"]
    assert isinstance(worktree, dict)
    worktree["head_revision"] = "d" * 40
    report["report_hash"] = WorkerReportAdmitter.report_hash(report)

    result = WorkerReportAdmitter().admit(
        report,
        assignment=assignment,
        lease=lease,
        inspection=inspection,
        current_coordinator_fencing_token=7,
        received_at=NOW + timedelta(minutes=2),
    )
    assert "changed file hash mismatch" in result.reasons
    assert "changed path is outside assignment ownership" in result.reasons
    assert "worktree head mismatch" in result.reasons


def test_rejects_conflicts_missing_fields_and_secret_values(
    case: tuple[AssignmentEnvelope, SliceLease, WorktreeInspection, dict[str, object]],
) -> None:
    assignment, lease, inspection, report = case
    del report["summary"]
    report["blockers"] = [{"reason": "token is top-secret-value"}]
    worktree = report["worktree"]
    assert isinstance(worktree, dict)
    worktree["conflicted"] = True
    report["report_hash"] = WorkerReportAdmitter.report_hash(report)

    result = WorkerReportAdmitter().admit(
        report,
        assignment=assignment,
        lease=lease,
        inspection=inspection,
        current_coordinator_fencing_token=7,
        received_at=NOW + timedelta(minutes=2),
        secret_values=["top-secret-value"],
    )
    assert "required report field is missing: summary" in result.reasons
    assert "worktree reports conflicts" in result.reasons
    assert "report contains a credential value" in result.reasons
    assert "top-secret-value" not in result.to_html()


def test_malformed_report_is_rejected_and_preserved_without_raising(
    case: tuple[AssignmentEnvelope, SliceLease, WorktreeInspection, dict[str, object]],
) -> None:
    assignment, lease, inspection, _ = case
    result = WorkerReportAdmitter().admit(
        {"not": {"json": {1, 2}}},
        assignment=assignment,
        lease=lease,
        inspection=inspection,
        current_coordinator_fencing_token=7,
        received_at=NOW + timedelta(minutes=2),
    )
    assert result.integrable is False
    assert result.reasons == ("malformed report: report must be JSON serializable",)
    assert result.preserved.raw_sha256.startswith("sha256:")


def test_report_hash_rejects_non_mapping_and_invalid_existing_hash() -> None:
    with pytest.raises(ReportAdmissionError, match="report must be an object"):
        WorkerReportAdmitter.report_hash([])  # type: ignore[arg-type]
    with pytest.raises(ReportAdmissionError, match="report_hash must be a string"):
        WorkerReportAdmitter.report_hash({"report_hash": 1})
