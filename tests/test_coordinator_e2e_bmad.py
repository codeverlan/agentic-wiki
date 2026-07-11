from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict

import pytest

from memwiki.coordinator_api import CoordinatorAPI
from memwiki.coordinator_completion import (
    REQUIRED_PROJECTIONS,
    REQUIRED_VALIDATION_GATES,
    create_completion_manifest,
    verify_completion_manifest,
)
from memwiki.coordinator_harness import FakeClock, HarnessScenario, OrchestrationHarness
from memwiki.coordinator_privacy import DataProfile, PrivacyAction, PrivacyPolicy

FIXTURES = Path(__file__).parent / "fixtures" / "coordinator_bmad"
START = datetime(2026, 7, 11, 12, 0, tzinfo=timezone.utc)


def _plugin_helper() -> Path:
    cache = Path.home() / ".codex/plugins/cache/personal/agent-development-coordinator"
    helpers = sorted(cache.glob("*/scripts/coordinator.py"))
    assert helpers, "installed agent-development-coordinator helper is required"
    return helpers[-1]


def _helper(project: Path, operation: str, *arguments: str) -> Dict[str, object]:
    result = subprocess.run(
        [
            sys.executable,
            str(_plugin_helper()),
            "--project-root",
            str(project),
            operation,
            *arguments,
        ],
        cwd=project,
        text=True,
        capture_output=True,
        check=False,
        timeout=15,
    )
    assert result.returncode == 0, result.stdout or result.stderr
    payload = json.loads(result.stdout)
    assert isinstance(payload, dict)
    return payload


def _scenario(name: str) -> HarnessScenario:
    payload = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return HarnessScenario.from_dict(payload)


def _digest(label: str) -> str:
    return "sha256:" + hashlib.sha256(label.encode("utf-8")).hexdigest()


def _completion_snapshot(slice_ids: tuple[str, ...]) -> Dict[str, object]:
    revision = "bmad-integrated-revision"
    evidence: Dict[str, str] = {}

    def proof(label: str) -> str:
        evidence_id = f"e-{len(evidence):03d}-{label}"
        evidence[evidence_id] = _digest(label)
        return evidence_id

    slices = {
        slice_id: {
            "required": True,
            "disposition": "integrated",
            "integrated_revision": revision,
            "report_evidence_id": proof(f"{slice_id}-report"),
            "integration_evidence_id": proof(f"{slice_id}-integration"),
            "evaluation_passed": True,
            "evaluation_revision": revision,
            "evaluation_evidence_id": proof(f"{slice_id}-evaluation"),
        }
        for slice_id in slice_ids
    }
    gates = {
        name: {"passed": True, "revision": revision, "evidence_id": proof(f"gate-{name}")}
        for name in REQUIRED_VALIDATION_GATES
    }
    projections = {
        name: {"revision": revision, "evidence_id": proof(f"view-{name}")}
        for name in REQUIRED_PROJECTIONS
    }
    return {
        "schema_version": 1,
        "run_id": "bmad-e2e-044",
        "objective": "Complete a bounded autonomous BMAD build",
        "authority_evidence_id": proof("authority"),
        "initial_git": {"revision": "bmad-initial", "evidence_id": proof("initial-git")},
        "final_git": {
            "revision": revision,
            "local_matches": True,
            "remote_matches": True,
            "evidence_id": proof("final-git"),
        },
        "current_revision": revision,
        "queue_digest": _digest("drained-queue"),
        "slices": slices,
        "queue": {
            "ready": [],
            "active": [],
            "stale": [],
            "waiting_resolvable": [],
            "undisposed_reports": [],
            "uncertain_commands": [],
            "reachable_discoveries": [],
        },
        "blockers": [],
        "supervision": [],
        "validation_gates": gates,
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
        "projections": projections,
        "resources": {"evidence_id": proof("resources")},
        "commits": [{"revision": revision, "evidence_id": proof("commit")}],
        "incidents": [],
        "repairs": [],
        "leases": [],
        "resume_instruction": "No resume required; verify this manifest independently.",
        "evidence_hashes": evidence,
    }


def test_bmad_autonomous_build_survives_blocking_and_restart(tmp_path: Path) -> None:
    project = tmp_path / "clinical-platform"
    (project / "_bmad").mkdir(parents=True)
    (project / "_bmad/config.toml").write_text('project_name = "Synthetic EMR"\n')

    preflight = _helper(project, "preflight")
    assert preflight["adapter"] == "bmad"
    assert preflight["plugin_immutable"] is True
    assert preflight["ready"] is True

    initialized = _helper(project, "init", "--run-id", "bmad-e2e-044", "--max-workers", "2")
    assert initialized["state"]["max_workers"] == 2  # type: ignore[index]

    privacy = PrivacyPolicy(DataProfile.YES)
    assert privacy.synthetic_only is True
    privacy.authorize(PrivacyAction.WRITE_ARTIFACT, synthetic=True)
    with pytest.raises(PermissionError, match="synthetic-only"):
        privacy.authorize(PrivacyAction.WRITE_ARTIFACT, synthetic=False)

    blocked = OrchestrationHarness(clock=FakeClock(START)).run(
        _scenario("blocked-independent.json")
    )
    assert set(blocked.dispatch_order[:2]) == {"claims", "scheduling"}
    assert blocked.statuses["claims"] == "blocked"
    assert blocked.statuses["claims-child"] == "blocked"
    assert blocked.statuses["scheduling"] == "integrated"
    assert blocked.statuses["portal"] == "integrated"
    assert blocked.queue_drained is False
    assert any(entry.boundary == "tick" for entry in blocked.trace)
    assert {
        entry.boundary for entry in blocked.trace if entry.slice_id == "scheduling"
    } == {"dispatch", "report", "validation", "integration"}

    CoordinatorAPI(project).stop(reason="synthetic restart boundary")
    recovery = _helper(project, "recover", "--resume")
    assert recovery["verification"]["valid"] is True  # type: ignore[index]
    assert recovery["resume"]["desired_state"] == "running"  # type: ignore[index]
    assert CoordinatorAPI(project).status()["status"] == "ready"

    recovered = OrchestrationHarness(clock=FakeClock(START)).run(_scenario("recovered.json"))
    assert recovered.queue_drained is True
    assert set(recovered.integrated) == {"claims", "claims-child", "scheduling", "portal"}

    manifest = create_completion_manifest(_completion_snapshot(recovered.integrated))
    assert verify_completion_manifest(manifest).complete is True
    manifest_path = project / "_bmad-output/implementation-artifacts/completion-manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")

    inspected = _helper(project, "inspect")
    assert inspected["adapter"] == "bmad"
    assert inspected["verification"]["valid"] is True  # type: ignore[index]
    html_path = Path(str(inspected["rendered"]["path"]))  # type: ignore[index]
    html = html_path.read_text(encoding="utf-8")
    assert html_path.is_relative_to(project / "_bmad-output/implementation-artifacts")
    assert "<!doctype html>" in html.lower()
    assert 'type="application/ld+json"' in html
    assert "bmad-e2e-044" in html
