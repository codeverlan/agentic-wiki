from __future__ import annotations

import hashlib
import json
import os
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

from memwiki.coordinator_api import CoordinatorAPI
from memwiki.coordinator_capabilities import (
    AuthRequirement,
    Availability,
    CapabilityDescriptor,
    CapabilityKind,
    CapabilityRegistry,
    CostClass,
    MutationScope,
    NetworkRequirement,
    PrivacyClass,
    ReplaySafety,
    SupervisionRequirement,
)
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
)
from memwiki.coordinator_harness import (
    DeterministicExternalAdapter,
    FakeClock,
    HarnessScenario,
    OrchestrationHarness,
)
from memwiki.coordinator_privacy import DataProfile
from memwiki.coordinator_supervision import SupervisionCategory, SupervisionQueue

START = datetime(2026, 7, 11, 16, 0, tzinfo=timezone.utc)
FIXTURE = Path(__file__).parent / "fixtures" / "coordinator_lightweight" / "two_parallel_slices.json"


def _hash(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return "sha256:" + hashlib.sha256(encoded.encode()).hexdigest()


def _plugin_helper() -> Path:
    override = os.environ.get("AGENT_DEVELOPMENT_COORDINATOR_HELPER")
    if override:
        helper = Path(override).expanduser().resolve()
        assert helper.is_file()
        return helper
    cache = Path.home() / ".codex" / "plugins" / "cache" / "personal" / "agent-development-coordinator"
    candidates = sorted(cache.glob("*/scripts/coordinator.py"))
    assert candidates, "installed agent-development-coordinator helper is required"
    return candidates[-1]


def _helper(project: Path, *arguments: str) -> Mapping[str, Any]:
    result = subprocess.run(
        [str(_plugin_helper()), "--project-root", str(project), *arguments],
        cwd=project,
        text=True,
        capture_output=True,
        check=False,
        timeout=20,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert isinstance(payload, dict)
    return payload


def _completion_snapshot(
    *, run_id: str, result_hash: str, status_path: Path
) -> dict[str, object]:
    revision = "lightweight-integrated-revision"
    queue_digest = result_hash if result_hash.startswith("sha256:") else f"sha256:{result_hash}"
    evidence = {f"e-{index}": _hash([run_id, index]) for index in range(64)}
    identifiers = iter(evidence)

    def proof() -> str:
        return next(identifiers)

    slices = {
        slice_id: {
            "required": True,
            "disposition": "integrated",
            "integrated_revision": revision,
            "report_evidence_id": proof(),
            "integration_evidence_id": proof(),
        }
        for slice_id in ("LW-A", "LW-B")
    }
    gates = {
        name: {"passed": True, "revision": revision, "evidence_id": proof()}
        for name in REQUIRED_VALIDATION_GATES
    }
    projections = {
        name: {"revision": revision, "evidence_id": proof()} for name in REQUIRED_PROJECTIONS
    }
    return {
        "schema_version": 1,
        "run_id": run_id,
        "objective": "Complete a bounded autonomous lightweight build",
        "authority_evidence_id": proof(),
        "initial_git": {"revision": "lightweight-base", "evidence_id": proof()},
        "final_git": {
            "revision": revision,
            "local_matches": True,
            "remote_matches": True,
            "evidence_id": proof(),
        },
        "current_revision": revision,
        "queue_digest": queue_digest,
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
        "validation_gates": gates,
        "journal": {
            "verified": True,
            "projection_rebuilt": True,
            "rebuild_matches": True,
            "evidence_id": proof(),
        },
        "budgets": {"reconciled": True, "evidence_id": proof()},
        "residuals": {
            "credentials": 0,
            "phi_canaries": 0,
            "worker_processes": 0,
            "leases": 0,
            "locks": 0,
            "temporary_worktrees": 0,
            "unbounded_artifacts": 0,
            "evidence_id": proof(),
        },
        "plugin": {
            "source_cache_parity": True,
            "fresh_context_smoke": True,
            "evidence_id": proof(),
        },
        "projections": projections,
        "resources": {"evidence_id": proof()},
        "commits": [{"revision": revision, "evidence_id": proof()}],
        "incidents": [],
        "repairs": [],
        "leases": [],
        "resume_instruction": f"Verify {status_path.name} and this manifest.",
        "evidence_hashes": evidence,
    }


def test_lightweight_autonomous_build_end_to_end(tmp_path: Path) -> None:
    project = tmp_path / "plain-project"
    project.mkdir()

    preflight = _helper(project, "preflight")
    assert preflight["adapter"] == "lightweight"
    assert preflight["plugin_immutable"] is True
    assert ".agent-development/implementation-artifacts" in str(preflight["artifact_root"])

    initialized = _helper(project, "init", "--run-id", "lw-e2e", "--max-workers", "2")
    assert initialized["state"]["status"] == "ready"
    assert not (project / "_bmad").exists()

    design_memory = DesignMemory(
        project_type="local command-line tool",
        graphics_choice="No material frontend; retain a semantic HTML status surface",
        data_profile=DataProfile.NO,
        principles=("Accessible status evidence",),
        decisions=(DesignDecision("html-status", 1, "Use semantic HTML", "accepted", START),),
        updated_at=START,
    )
    design_evidence = DesignEvidence(
        slice_id="LW-A",
        material_frontend=False,
        commit_revision="lightweight-integrated-revision",
        screenshots=(),
        accessibility_status="not-applicable",
        accessibility_evidence_sha256=None,
        disposition=EvidenceDisposition.NOT_APPLICABLE,
        disposition_reason="The build has no material frontend; coordinator status remains HTML.",
        reviewed_at=START,
    )
    assert DesignEvidenceGate().evaluate(
        design_memory, design_evidence, expected_commit="lightweight-integrated-revision"
    ).can_complete

    capability = CapabilityDescriptor(
        identifier="skill.local-reference",
        display_name="Local reference lookup",
        kind=CapabilityKind.SKILL,
        source="installed-plugin",
        operations=("lookup",),
        availability=Availability.AVAILABLE,
        mutation_scope=MutationScope.READ_ONLY,
        auth=AuthRequirement.NONE,
        network=NetworkRequirement.NONE,
        cost=CostClass.FREE,
        privacy=PrivacyClass.LOCAL_ONLY,
        replay=ReplaySafety.SAFE,
        supervision=SupervisionRequirement.NONE,
        preference=1,
        metadata={"installed": True},
    )
    assert CapabilityRegistry((capability,)).discover()[0].to_dict()["auth"] == "none"

    scenario = HarnessScenario.from_dict(json.loads(FIXTURE.read_text(encoding="utf-8")))
    external = DeterministicExternalAdapter(
        {"skill.local-reference": ({"source": "fixture", "offline": True},)}
    )
    run = OrchestrationHarness(clock=FakeClock(START), external=external).run(scenario)
    assert run.statuses == {"LW-A": "integrated", "LW-B": "integrated"}
    assert run.queue_drained
    first_dispatches = [entry.slice_id for entry in run.trace if entry.boundary == "dispatch"]
    assert set(first_dispatches[:2]) == {"LW-A", "LW-B"}
    assert run.external_calls[0].capability == "skill.local-reference"

    supervision = SupervisionQueue.empty()
    supervision.open(
        item_id="confirm-handoff",
        category=SupervisionCategory.AUTHORIZATION,
        summary="Confirm the final handoff presentation",
        choices=("approve", "defer"),
        evidence_ids=(run.evidence_sha256,),
        affected_slice_ids=("LW-A", "LW-B"),
        resumable=True,
        created_at=START,
    )
    returned = supervision.record_user_return(
        recorded_at=START + timedelta(seconds=1), active_slice_ids=("LW-A", "LW-B")
    )
    assert returned.cancel_active_work is False
    assert supervision.presentation_due
    supervision.resolve(
        "confirm-handoff",
        disposition="approved",
        evidence_ids=(run.evidence_sha256,),
        recorded_at=START + timedelta(seconds=2),
    )

    restarted = CoordinatorAPI(project)
    assert restarted.status()["run_id"] == "lw-e2e"
    assert restarted.verify()["valid"] is True

    status_path = project / ".agent-development" / "implementation-artifacts" / "status.html"
    inspected = _helper(project, "inspect", "--output", str(status_path))
    assert inspected["verification"]["valid"] is True
    status_html = status_path.read_text(encoding="utf-8")
    assert "<!doctype html>" in status_html.lower()
    assert 'type="application/ld+json"' in status_html
    assert "lw-e2e" in status_html

    snapshot = _completion_snapshot(
        run_id="lw-e2e", result_hash=run.evidence_sha256, status_path=status_path
    )
    manifest = create_completion_manifest(snapshot)
    assert verify_completion_manifest(manifest).complete
    manifest_path = status_path.with_name("completion-manifest.json")
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    assert manifest_path.is_file()
    assert not any("credential" in key.lower() for key in manifest)
