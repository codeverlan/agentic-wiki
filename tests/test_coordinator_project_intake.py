from __future__ import annotations

import json
from pathlib import Path

import pytest

from memwiki.coordinator_model_routing import (
    CapabilityTier,
    HostCapabilityProfile,
    ModelCapability,
    ModelRoutingPolicy,
    ReasoningEffort,
)
from memwiki.coordinator_project_intake import (
    EntryPath,
    IntakeCheckpointError,
    ProjectIntakeManager,
)


def _complete_update() -> dict[str, object]:
    return {
        "product": {
            "purpose": "Coordinate a synthetic referral workflow",
            "users": ["synthetic coordinator", "synthetic clinician"],
            "outcomes": ["referrals have a visible disposition"],
            "scope": ["intake", "routing", "status"],
        },
        "acceptance_criteria": [
            {"id": "AC-1", "statement": "A synthetic referral can be routed and verified"}
        ],
        "design": {"material": True, "authority": "user-approved baseline"},
        "sources": [{"id": "outline", "authority": "user", "kind": "partial-outline"}],
        "external_dependencies": [
            {"id": "synthetic-api", "availability": "available", "auth_reference": "env:SYNTHETIC_API"}
        ],
        "credential_sources": [{"id": "synthetic-api", "source": "environment"}],
        "open_questions": [],
        "contradictions": [],
    }


def test_initialize_creates_authoritative_state_and_all_required_projections(tmp_path: Path) -> None:
    manager = ProjectIntakeManager(tmp_path)

    result = manager.initialize(
        project_id="synthetic-referrals",
        entry_path=EntryPath.PARTIAL_RESOURCES,
        project_type="web-application",
        phi_answer="no",
        recorded_at="2026-07-12T15:00:00-04:00",
    )

    assert result.revision == 1
    assert result.adapter == "lightweight"
    assert result.state_path.is_file()
    assert result.checkpoint_path.is_file()
    assert set(result.projection_paths) >= {
        "intent-packet",
        "specification",
        "accepted-decision-matrix",
        "phi-profile",
        "design-memory",
        "external-capabilities",
        "project-overlay",
        "host-routing-policy",
        "readiness",
        "slice-queue",
    }
    assert all(paths.json_path.is_file() and paths.html_path.is_file() for paths in result.projection_paths.values())


def test_complete_update_passes_readiness_and_creates_initial_slice(tmp_path: Path) -> None:
    manager = ProjectIntakeManager(tmp_path)
    manager.initialize(
        project_id="synthetic-referrals",
        entry_path=EntryPath.PARTIAL_RESOURCES,
        project_type="web-application",
        phi_answer="no",
        recorded_at="2026-07-12T15:00:00-04:00",
    )

    updated = manager.apply(
        _complete_update(), expected_revision=1, recorded_at="2026-07-12T15:05:00-04:00"
    )
    readiness = manager.readiness()

    assert updated.revision == 2
    assert readiness.ready is True
    assert readiness.blocking_issues == ()
    queue = manager.storage.read_json(manager.artifact("slice-queue"))
    assert queue["slices"][0]["id"] == "INTAKE-IMPLEMENTATION-001"


def test_readiness_fails_closed_for_unknown_phi_and_material_gaps(tmp_path: Path) -> None:
    manager = ProjectIntakeManager(tmp_path)
    manager.initialize(
        project_id="clinical-unknown",
        entry_path=EntryPath.FROM_SCRATCH,
        project_type="clinical-web-application",
        phi_answer="unknown",
        recorded_at="2026-07-12T15:00:00-04:00",
    )

    result = manager.readiness()

    assert result.ready is False
    assert "phi-status-unresolved" in result.blocking_issues
    assert "product-purpose-missing" in result.blocking_issues
    assert "acceptance-criteria-missing" in result.blocking_issues
    assert result.synthetic_only is True


def test_resume_repairs_stale_projections_and_matches_uninterrupted_state(tmp_path: Path) -> None:
    manager = ProjectIntakeManager(tmp_path)
    manager.initialize(
        project_id="recoverable",
        entry_path=EntryPath.FROM_SCRATCH,
        project_type="cli",
        phi_answer="no",
        recorded_at="2026-07-12T15:00:00-04:00",
    )
    manager.apply(
        {"product": {"purpose": "Build a deterministic CLI"}},
        expected_revision=1,
        recorded_at="2026-07-12T15:01:00-04:00",
    )
    specification = manager.storage.paths_for(manager.artifact("specification"))
    specification.html_path.write_text("stale", encoding="utf-8")

    resumed = ProjectIntakeManager(tmp_path).resume()

    assert resumed.revision == 2
    assert "Build a deterministic CLI" in specification.html_path.read_text(encoding="utf-8")
    assert ProjectIntakeManager(tmp_path).inspect().state == resumed.state


def test_checkpoint_tampering_at_current_revision_is_rejected(tmp_path: Path) -> None:
    manager = ProjectIntakeManager(tmp_path)
    initialized = manager.initialize(
        project_id="tamper",
        entry_path=EntryPath.FROM_SCRATCH,
        project_type="library",
        phi_answer="no",
        recorded_at="2026-07-12T15:00:00-04:00",
    )
    checkpoint = json.loads(initialized.checkpoint_path.read_text())
    checkpoint["state_digest"] = "sha256:" + "0" * 64
    initialized.checkpoint_path.write_text(json.dumps(checkpoint), encoding="utf-8")

    with pytest.raises(IntakeCheckpointError, match="digest"):
        ProjectIntakeManager(tmp_path).resume()


def test_apply_cannot_launder_tampered_authoritative_state(tmp_path: Path) -> None:
    manager = ProjectIntakeManager(tmp_path)
    initialized = manager.initialize(
        project_id="tampered-state",
        entry_path=EntryPath.FROM_SCRATCH,
        project_type="library",
        phi_answer="no",
        recorded_at="2026-07-12T15:00:00-04:00",
    )
    state = json.loads(initialized.state_path.read_text())
    state["product"]["purpose"] = "uncheckpointed mutation"
    initialized.state_path.write_text(json.dumps(state), encoding="utf-8")

    with pytest.raises(IntakeCheckpointError, match="digest"):
        manager.apply(
            {"product": {"purpose": "attempted laundering"}},
            expected_revision=1,
            recorded_at="2026-07-12T15:01:00-04:00",
        )


def test_overlay_customizes_project_behavior_without_plugin_paths_or_secrets(tmp_path: Path) -> None:
    manager = ProjectIntakeManager(tmp_path)
    manager.initialize(
        project_id="overlay",
        entry_path=EntryPath.FROM_SCRATCH,
        project_type="web-application",
        phi_answer="no",
        recorded_at="2026-07-12T15:00:00-04:00",
    )
    result = manager.apply(
        {
            "overlay": {
                "terminology": {"slice": "work item"},
                "interview_depth": "deep",
                "delivery": {"check_in": "major-milestones"},
            }
        },
        expected_revision=1,
        recorded_at="2026-07-12T15:02:00-04:00",
    )

    assert result.state["overlay"]["terminology"] == {"slice": "work item"}
    with pytest.raises(ValueError, match="overlay"):
        manager.apply(
            {"overlay": {"plugin_path": "/tmp/mutate-plugin"}},
            expected_revision=2,
            recorded_at="2026-07-12T15:03:00-04:00",
        )
    with pytest.raises(ValueError, match="sensitive"):
        manager.apply(
            {"credential_sources": [{"id": "x", "password": "do-not-store"}]},
            expected_revision=2,
            recorded_at="2026-07-12T15:03:00-04:00",
        )


def test_bmad_marker_selects_bmad_artifact_root(tmp_path: Path) -> None:
    (tmp_path / "_bmad").mkdir()
    manager = ProjectIntakeManager(tmp_path)
    result = manager.initialize(
        project_id="bmad-project",
        entry_path=EntryPath.EXISTING_SPEC,
        project_type="api",
        phi_answer="no",
        recorded_at="2026-07-12T15:00:00-04:00",
    )

    assert result.adapter == "bmad"
    assert "_bmad-output/implementation-artifacts" in str(result.state_path)


def test_existing_lightweight_intake_remains_authoritative_after_bmad_initialization(
    tmp_path: Path,
) -> None:
    initialized = ProjectIntakeManager(tmp_path).initialize(
        project_id="bmad-later",
        entry_path=EntryPath.FROM_SCRATCH,
        project_type="web-application",
        phi_answer="no",
        recorded_at="2026-07-13T12:00:00-04:00",
    )
    (tmp_path / "_bmad").mkdir()

    resumed = ProjectIntakeManager(tmp_path).resume()

    assert initialized.adapter == "lightweight"
    assert resumed.adapter == "lightweight"
    assert resumed.state["project_id"] == "bmad-later"
    assert resumed.state_path == initialized.state_path


def test_host_profile_and_routing_policy_are_validated_project_records(tmp_path: Path) -> None:
    manager = ProjectIntakeManager(tmp_path)
    manager.initialize(
        project_id="routing",
        entry_path=EntryPath.FROM_SCRATCH,
        project_type="library",
        phi_answer="no",
        recorded_at="2026-07-12T15:00:00-04:00",
    )
    profile = HostCapabilityProfile(
        "local",
        (ModelCapability("small", CapabilityTier.NARROW, (ReasoningEffort.LOW,)),),
    )
    policy = ModelRoutingPolicy(
        preferred_by_tier={CapabilityTier.NARROW: ("small", ReasoningEffort.LOW)}
    )

    result = manager.apply(
        {
            "host_capability_profile": profile.to_dict(),
            "host_routing_policy": policy.to_dict(),
        },
        expected_revision=1,
        recorded_at="2026-07-12T15:01:00-04:00",
    )

    assert result.state["host_capability_profile"]["profile_id"] == profile.profile_id
    with pytest.raises(ValueError, match="fields do not match"):
        manager.apply(
            {"host_routing_policy": {"narrow": "small"}},
            expected_revision=2,
            recorded_at="2026-07-12T15:02:00-04:00",
        )
