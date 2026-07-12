from __future__ import annotations

import copy
import hashlib
import json

import pytest

from memwiki.coordinator_completion import (
    REQUIRED_PROJECTIONS,
    REQUIRED_VALIDATION_GATES,
    CompletionEvidenceError,
    create_completion_manifest,
    evaluate_completion,
    verify_completion_manifest,
)


def _hash(label: str) -> str:
    return "sha256:" + hashlib.sha256(label.encode()).hexdigest()


def _snapshot() -> dict[str, object]:
    revision = "commit-final"
    evidence = {f"e-{index}": _hash(f"evidence-{index}") for index in range(80)}
    evidence_ids = iter(evidence)

    def proof() -> str:
        return next(evidence_ids)

    slices = {
        "AC-001": {
            "required": True,
            "disposition": "integrated",
            "integrated_revision": revision,
            "report_evidence_id": proof(),
            "integration_evidence_id": proof(),
            "evaluation_passed": True,
            "evaluation_revision": revision,
            "evaluation_evidence_id": proof(),
        },
        "DISC-001": {
            "required": True,
            "accepted_discovery": True,
            "disposition": "integrated",
            "integrated_revision": revision,
            "report_evidence_id": proof(),
            "integration_evidence_id": proof(),
            "evaluation_passed": True,
            "evaluation_revision": revision,
            "evaluation_evidence_id": proof(),
        },
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
        "run_id": "run-038",
        "objective": "Build and prove the autonomous coordinator",
        "authority_evidence_id": proof(),
        "initial_git": {"revision": "commit-initial", "evidence_id": proof()},
        "final_git": {
            "revision": revision,
            "local_matches": True,
            "remote_matches": True,
            "evidence_id": proof(),
        },
        "current_revision": revision,
        "queue_digest": _hash("queue"),
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
        "resume_instruction": "No resume required; independently verify this manifest.",
        "evidence_hashes": evidence,
    }


def test_complete_snapshot_passes_every_condition_and_is_reconstructable() -> None:
    snapshot = _snapshot()
    result = evaluate_completion(snapshot)
    assert result.complete is True
    assert result.failed_conditions == ()
    assert all(condition.passed for condition in result.conditions)

    manifest = create_completion_manifest(snapshot)
    assert manifest["complete"] is True
    assert manifest["manifest_hash"].startswith("sha256:")
    assert verify_completion_manifest(manifest) == result


@pytest.mark.parametrize(
    ("path", "value", "reason"),
    [
        (("queue", "ready"), ["AC-099"], "queue_drained"),
        (("queue", "reachable_discoveries"), ["proposal-1"], "queue_drained"),
        (("budgets", "reconciled"), False, "budgets_reconciled"),
        (("final_git", "remote_matches"), False, "git_state_matches"),
        (("journal", "rebuild_matches"), False, "journal_reconstructs"),
        (("plugin", "source_cache_parity"), False, "plugin_verified"),
        (("residuals", "leases"), 1, "no_residual_runtime_or_sensitive_data"),
    ],
)
def test_false_drain_and_missing_requirements_fail(
    path: tuple[str, str], value: object, reason: str
) -> None:
    snapshot = _snapshot()
    container = snapshot[path[0]]
    assert isinstance(container, dict)
    container[path[1]] = value
    result = evaluate_completion(snapshot)
    assert result.complete is False
    assert reason in result.failed_conditions


def test_required_slice_must_be_integrated_at_final_revision() -> None:
    snapshot = _snapshot()
    slices = snapshot["slices"]
    assert isinstance(slices, dict)
    item = slices["AC-001"]
    assert isinstance(item, dict)
    item["disposition"] = "completed"
    assert "required_work_integrated" in evaluate_completion(snapshot).failed_conditions


def test_required_slice_evaluation_must_pass_at_final_revision() -> None:
    snapshot = _snapshot()
    slices = snapshot["slices"]
    assert isinstance(slices, dict)
    item = slices["AC-001"]
    assert isinstance(item, dict)
    item["evaluation_passed"] = False
    assert "required_work_integrated" in evaluate_completion(snapshot).failed_conditions
    item["evaluation_passed"] = True
    item["evaluation_revision"] = "stale-commit"
    assert "required_work_integrated" in evaluate_completion(snapshot).failed_conditions
    item["disposition"] = "integrated"
    item["integrated_revision"] = "stale-commit"
    assert "required_work_integrated" in evaluate_completion(snapshot).failed_conditions


def test_final_revision_alias_binds_validation_and_git_proof() -> None:
    snapshot = _snapshot()
    snapshot["final_revision"] = "commit-final"
    snapshot.pop("current_revision")
    assert evaluate_completion(snapshot).complete is True
    final_git = snapshot["final_git"]
    assert isinstance(final_git, dict)
    final_git["revision"] = "other-commit"
    assert "git_state_matches" in evaluate_completion(snapshot).failed_conditions


def test_tests_must_pass_at_integrated_revision_and_all_gate_names_are_required() -> None:
    snapshot = _snapshot()
    gates = snapshot["validation_gates"]
    assert isinstance(gates, dict)
    gates.pop("soak")
    assert "validation_gates_pass" in evaluate_completion(snapshot).failed_conditions

    snapshot = _snapshot()
    gates = snapshot["validation_gates"]
    assert isinstance(gates, dict)
    gate = gates["unit"]
    assert isinstance(gate, dict)
    gate["revision"] = "pre-integration"
    assert "validation_gates_pass" in evaluate_completion(snapshot).failed_conditions


def test_unresolved_mandatory_blocker_or_supervision_fails_but_advisory_does_not() -> None:
    snapshot = _snapshot()
    snapshot["blockers"] = [{"id": "b1", "status": "open", "mandatory": True}]
    assert "mandatory_items_resolved" in evaluate_completion(snapshot).failed_conditions
    snapshot["blockers"] = [{"id": "b2", "status": "open", "mandatory": False}]
    assert "mandatory_items_resolved" not in evaluate_completion(snapshot).failed_conditions
    snapshot["supervision"] = [{"id": "s1", "status": "deferred", "mandatory": True}]
    assert "mandatory_items_resolved" in evaluate_completion(snapshot).failed_conditions


def test_every_referenced_evidence_id_requires_a_valid_hash() -> None:
    snapshot = _snapshot()
    evidence = snapshot["evidence_hashes"]
    assert isinstance(evidence, dict)
    evidence.pop(next(iter(evidence)))
    assert "evidence_hashes_complete" in evaluate_completion(snapshot).failed_conditions

    snapshot = _snapshot()
    evidence = snapshot["evidence_hashes"]
    assert isinstance(evidence, dict)
    evidence[next(iter(evidence))] = "sha256:not-a-hash"
    assert "evidence_hashes_complete" in evaluate_completion(snapshot).failed_conditions


def test_all_status_projections_must_be_current() -> None:
    snapshot = _snapshot()
    projections = snapshot["projections"]
    assert isinstance(projections, dict)
    projection = projections["handoff"]
    assert isinstance(projection, dict)
    projection["revision"] = "stale"
    assert "status_projections_current" in evaluate_completion(snapshot).failed_conditions


def test_manifest_is_canonical_immutable_and_detects_tampering() -> None:
    manifest = create_completion_manifest(_snapshot())
    canonical = json.dumps(manifest, sort_keys=True, separators=(",", ":"), allow_nan=False)
    assert json.loads(canonical) == manifest

    tampered = copy.deepcopy(manifest)
    tampered["objective"] = "declare a phase complete"
    with pytest.raises(CompletionEvidenceError, match="manifest hash mismatch"):
        verify_completion_manifest(tampered)


def test_incomplete_snapshot_cannot_create_a_completion_manifest() -> None:
    snapshot = _snapshot()
    queue = snapshot["queue"]
    assert isinstance(queue, dict)
    queue["active"] = ["AC-001"]
    with pytest.raises(CompletionEvidenceError, match="completion predicate failed"):
        create_completion_manifest(snapshot)


def test_phase_or_milestone_flags_cannot_substitute_for_predicate() -> None:
    snapshot = _snapshot()
    snapshot["phase_complete"] = True
    queue = snapshot["queue"]
    assert isinstance(queue, dict)
    queue["waiting_resolvable"] = ["AC-002"]
    assert evaluate_completion(snapshot).complete is False


def test_coherence_checks_reports_leases_runtime_memory_and_sha_parity() -> None:
    snapshot = _snapshot()
    snapshot["worker_reports"] = [{"report_id": "r-1", "disposition": "integrated", "evidence_id": "e-1"}]
    snapshot["active_leases"] = []
    snapshot["runtime"] = {
        "reconciled": True,
        "workers_reconciled": True,
        "effects_reconciled": True,
        "evidence_id": "e-2",
    }
    snapshot["memory_dispositions"] = [{"id": "m-1", "disposition": "no_change", "evidence_id": "e-3"}]
    snapshot["sha_parity"] = {
        "revision": "commit-final",
        "local_matches": True,
        "remote_matches": True,
        "artifacts_match": True,
        "evidence_id": "e-4",
    }
    assert evaluate_completion(snapshot).complete is True

    snapshot["active_leases"] = [{"lease_id": "lease-1"}]
    assert "active_leases_cleared" in evaluate_completion(snapshot).failed_conditions
    snapshot["active_leases"] = []
    reports = snapshot["worker_reports"]
    assert isinstance(reports, list) and isinstance(reports[0], dict)
    reports[0]["disposition"] = "received"
    assert "worker_reports_disposed" in evaluate_completion(snapshot).failed_conditions


def test_complete_with_advisories_is_successful_and_is_manifest_bound() -> None:
    snapshot = _snapshot()
    snapshot["blockers"] = [{"id": "future-1", "status": "open", "mandatory": False}]
    result = evaluate_completion(snapshot)
    assert result.complete is True
    assert result.disposition == "complete-with-advisories"
    manifest = create_completion_manifest(snapshot)
    assert manifest["disposition"] == "complete-with-advisories"
    assert verify_completion_manifest(manifest).advisory_ids == ("blockers:future-1",)
