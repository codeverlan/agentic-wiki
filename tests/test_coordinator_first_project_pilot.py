from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from memwiki.coordinator_api import CoordinatorAPI
from memwiki.coordinator_completion import (
    REQUIRED_PROJECTIONS,
    REQUIRED_VALIDATION_GATES,
    create_completion_manifest,
    verify_completion_manifest,
)
from memwiki.coordinator_design import (
    DesignDecision,
    DesignEvidence,
    DesignEvidenceGate,
    DesignMemory,
    EvidenceDisposition,
    ScreenInventoryItem,
    ScreenshotEvidence,
)
from memwiki.coordinator_engineering import EvaluationResult, SliceEvaluation
from memwiki.coordinator_harness import FakeClock, HarnessScenario, OrchestrationHarness
from memwiki.coordinator_privacy import DataProfile
from memwiki.coordinator_project_intake import EntryPath, ProjectIntakeManager
from memwiki.coordinator_supervision import SupervisionCategory, SupervisionQueue

START = datetime(2026, 7, 12, 16, 0, tzinfo=timezone.utc)
FIXTURE = Path(__file__).parent / "fixtures" / "coordinator_pilot" / "shiftboard.json"
REVISION = "shiftboard-integrated-revision"


def _digest(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _scenario(name: str) -> HarnessScenario:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))[name]
    assert isinstance(payload, dict)
    return HarnessScenario.from_dict(payload)


def _completion_snapshot(slice_ids: tuple[str, ...], evaluation_evidence_id: str) -> dict[str, object]:
    evidence: dict[str, str] = {}

    def proof(label: str) -> str:
        evidence_id = f"e-{len(evidence):03d}-{label}"
        evidence[evidence_id] = _digest(label)
        return evidence_id

    slices = {
        slice_id: {
            "required": True,
            "disposition": "integrated",
            "integrated_revision": REVISION,
            "report_evidence_id": proof(f"{slice_id}-report"),
            "integration_evidence_id": proof(f"{slice_id}-integration"),
            "evaluation_passed": True,
            "evaluation_revision": REVISION,
            "evaluation_evidence_id": evaluation_evidence_id,
        }
        for slice_id in slice_ids
    }
    evidence[evaluation_evidence_id] = _digest("storage-repair-evaluation")
    return {
        "schema_version": 1,
        "run_id": "shiftboard-first-project-pilot",
        "objective": "Deliver the synthetic Shiftboard scheduling pilot",
        "authority_evidence_id": proof("authority"),
        "initial_git": {"revision": "shiftboard-baseline", "evidence_id": proof("initial-git")},
        "final_git": {
            "revision": REVISION,
            "local_matches": True,
            "remote_matches": True,
            "evidence_id": proof("final-git"),
        },
        "current_revision": REVISION,
        "queue_digest": _digest("shiftboard-drained-queue"),
        "slices": slices,
        "queue": {
            name: []
            for name in (
                "ready",
                "active",
                "stale",
                "waiting_resolvable",
                "undisposed_reports",
                "uncertain_commands",
                "reachable_discoveries",
            )
        },
        "blockers": [],
        "supervision": [],
        "validation_gates": {
            name: {"passed": True, "revision": REVISION, "evidence_id": proof(f"gate-{name}")}
            for name in REQUIRED_VALIDATION_GATES
        },
        "journal": {
            "verified": True,
            "projection_rebuilt": True,
            "rebuild_matches": True,
            "evidence_id": proof("journal"),
        },
        "budgets": {"reconciled": True, "evidence_id": proof("budgets")},
        "residuals": {
            "credentials": 0,
            "phi_canaries": 0,
            "worker_processes": 0,
            "leases": 0,
            "locks": 0,
            "temporary_worktrees": 0,
            "unbounded_artifacts": 0,
            "evidence_id": proof("residuals"),
        },
        "plugin": {
            "source_cache_parity": True,
            "fresh_context_smoke": True,
            "evidence_id": proof("plugin"),
        },
        "projections": {
            name: {"revision": REVISION, "evidence_id": proof(f"view-{name}")}
            for name in REQUIRED_PROJECTIONS
        },
        "resources": {"evidence_id": proof("resources")},
        "commits": [{"revision": REVISION, "evidence_id": proof("commit")}],
        "incidents": [],
        "repairs": [{"id": "storage-contract-repair", "evidence_id": proof("repair")}],
        "leases": [],
        "resume_instruction": "Verify the Shiftboard manifest and retained synthetic evidence.",
        "evidence_hashes": evidence,
    }


def test_synthetic_shiftboard_first_project_pilot(tmp_path: Path) -> None:
    project = tmp_path / "shiftboard"
    project.mkdir()

    intake = ProjectIntakeManager(project)
    intake.initialize(
        project_id="synthetic-shiftboard",
        entry_path=EntryPath.FROM_SCRATCH,
        project_type="scheduling web application",
        phi_answer="no",
        recorded_at=START.isoformat(),
    )
    intake.apply(
        {
            "product": {
                "purpose": "Coordinate synthetic shift coverage without clinical records",
                "users": ["scheduler", "shift lead"],
                "outcomes": ["open shifts have a visible coverage disposition"],
                "scope": ["shift board", "coverage request", "assignment status"],
            },
            "acceptance_criteria": [
                {"id": "SB-1", "statement": "A synthetic open shift can be assigned and verified"}
            ],
            "design": {"material": True, "authority": "synthetic pilot design baseline"},
            "decisions": [{"id": "synthetic-only", "status": "accepted"}],
            "open_questions": [],
            "contradictions": [],
        },
        expected_revision=1,
        recorded_at=(START + timedelta(minutes=1)).isoformat(),
    )
    readiness = intake.readiness()
    assert readiness.ready is True
    assert readiness.synthetic_only is False

    blocked = OrchestrationHarness(clock=FakeClock(START)).run(_scenario("blocked"))
    assert len(blocked.statuses) == 6
    assert blocked.statuses["storage-schema"] == "blocked"
    assert blocked.statuses["shift-api"] == "blocked"
    assert {"board-ui", "notifications", "audit-export", "help-content"} <= set(blocked.integrated)

    control = CoordinatorAPI(project)
    initialized = control.initialize(run_id="shiftboard-first-project-pilot", max_workers=6)
    assert initialized["max_workers"] == 6
    stopped = control.stop(reason="synthetic storage repair boundary")
    assert stopped["desired_state"] == "stopped"
    restarted = CoordinatorAPI(project)
    assert restarted.status() == stopped
    resumed = restarted.resume()
    assert resumed["desired_state"] == "running"
    assert CoordinatorAPI(project).verify()["valid"] is True

    supervision = SupervisionQueue.empty()
    supervision.open(
        item_id="shiftboard-design-baseline-v2",
        category=SupervisionCategory.AUTHORIZATION,
        summary="Approve the synthetic Shiftboard board baseline change",
        choices=("approve", "defer"),
        evidence_ids=("design-baseline-v2",),
        affected_slice_ids=("board-ui",),
        resumable=True,
        created_at=START,
    )
    supervision.resolve(
        "shiftboard-design-baseline-v2",
        disposition="approved",
        evidence_ids=("design-baseline-v2",),
        recorded_at=START + timedelta(minutes=2),
    )
    assert supervision.get("shiftboard-design-baseline-v2").status.value == "resolved"

    design = DesignMemory(
        project_type="scheduling web application",
        graphics_choice="Dense operational shift board with synthetic names only",
        data_profile=DataProfile.NO,
        principles=("Visible coverage state", "Keyboard-accessible scheduling actions"),
        screens=(ScreenInventoryItem("shift-board", "/shifts", ("default", "empty")),),
        decisions=(
            DesignDecision("board-baseline", 1, "Use a compact table baseline", "superseded", START),
            DesignDecision("board-baseline", 2, "Use assignment-state columns", "accepted", START),
        ),
        updated_at=START,
    )
    evidence = DesignEvidence(
        slice_id="board-ui",
        material_frontend=True,
        commit_revision=REVISION,
        screenshots=(
            ScreenshotEvidence(
                "shiftboard-desktop", "/shifts", "shift-board", "default", "desktop", REVISION,
                "a" * 64, None, 0.0, 1.0, START, True, True,
                baseline_proposal_supervision_id="shiftboard-design-baseline-v2",
            ),
            ScreenshotEvidence(
                "shiftboard-mobile", "/shifts", "shift-board", "default", "mobile", REVISION,
                "b" * 64, None, 0.0, 1.0, START, True, True,
                baseline_proposal_supervision_id="shiftboard-design-baseline-v2",
            ),
        ),
        accessibility_status="passed",
        accessibility_evidence_sha256="c" * 64,
        disposition=EvidenceDisposition.PROVIDED,
        disposition_reason=None,
        reviewed_at=START,
    )
    assert DesignEvidenceGate().evaluate(design, evidence, expected_commit=REVISION).can_complete

    storage_evaluation = SliceEvaluation.create(
        slice_id="storage-schema",
        capability_checks=("pytest:storage-schema",),
        regression_checks=("pytest:shift-api-contract",),
        baseline_revision="shiftboard-baseline",
        baseline={"pytest:storage-schema": False, "pytest:shift-api-contract": True},
        metadata={"synthetic": True},
    )
    rejected = storage_evaluation.compare(
        integrated_revision="shiftboard-regressed-revision",
        observed={"pytest:storage-schema": True, "pytest:shift-api-contract": False},
    )
    assert rejected.status is EvaluationResult.REGRESSED
    assert rejected.completion_gate_passed is False
    repaired = storage_evaluation.compare(
        integrated_revision=REVISION,
        observed={"pytest:storage-schema": True, "pytest:shift-api-contract": True},
    )
    assert repaired.completion_gate_passed is True

    recovered = OrchestrationHarness(clock=FakeClock(START)).run(_scenario("recovered"))
    assert recovered.queue_drained is True
    assert set(recovered.integrated) == set(recovered.statuses)
    snapshot = _completion_snapshot(recovered.integrated, repaired.comparison_id)
    manifest = create_completion_manifest(snapshot)
    assert verify_completion_manifest(manifest).complete is True
    assert all(
        {"evaluation_passed", "evaluation_revision", "evaluation_evidence_id"} <= set(slice_state)
        for slice_state in snapshot["slices"].values()  # type: ignore[union-attr]
    )
