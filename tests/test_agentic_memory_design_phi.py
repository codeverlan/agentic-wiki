from __future__ import annotations

import importlib
from hashlib import sha256
from pathlib import Path
from typing import Any

import pytest


def agent_memory() -> Any:
    return importlib.import_module("memwiki.agent_memory")


@pytest.mark.parametrize(
    ("answer", "development_data_mode", "synthetic_only", "requires_supervision"),
    [
        ("yes", "synthetic-or-verified-deidentified-only", True, False),
        ("no", "general-project-policy", False, False),
        ("unknown", "precautionary-synthetic-only-until-answered", True, True),
    ],
)
def test_phi_answer_maps_to_development_policy(
    answer: str,
    development_data_mode: str,
    synthetic_only: bool,
    requires_supervision: bool,
) -> None:
    policy = agent_memory().phi_policy_for(answer)

    assert policy.answer == answer
    assert policy.development_data_mode == development_data_mode
    assert policy.synthetic_only is synthetic_only
    assert policy.requires_supervision is requires_supervision


@pytest.mark.parametrize("answer", ["yes", "unknown"])
def test_phi_yes_and_unknown_allow_only_synthetic_or_deidentified_evidence(answer: str) -> None:
    memory = agent_memory()
    policy = memory.phi_policy_for(answer)

    assert policy.allows_evidence("synthetic") is True
    assert policy.allows_evidence("verified-deidentified") is True
    assert policy.allows_evidence("real") is False


@pytest.mark.parametrize(
    "prohibited_field",
    ["phi_value", "raw_phi", "secret_value", "raw_secret", "password_value", "token_value"],
)
def test_agent_memory_rejects_phi_and_secret_value_fields(prohibited_field: str) -> None:
    memory = agent_memory()
    record = {
        "record_type": "screen_reference",
        "screen_id": "claims-list",
        "metadata": {prohibited_field: "synthetic-sensitive-placeholder"},
    }

    with pytest.raises(ValueError, match=prohibited_field):
        memory.validate_memory_record(record)


def test_screen_reference_preserves_required_metadata() -> None:
    memory = agent_memory()
    metadata = {
        "screen_id": "claims-list",
        "route_or_surface": "/claims",
        "feature": "claims review",
        "user_role": "billing-specialist",
        "viewport": "1440x900",
        "application_state": "synthetic denied claim",
        "source_path": "design/screens/claims-list.png",
        "sha256": "a" * 64,
        "captured_at": "2026-07-11T09:00:00-04:00",
        "capture_method": "playwright",
        "reference_status": "approved",
        "related_components": ["ClaimsTable", "StatusFilter"],
        "applicable_design_principles": ["dense-operational-layout"],
        "commit_or_revision": "abc1234",
        "supersedes": [],
        "contains_phi": False,
        "synthetic_or_deidentified_status": "synthetic",
        "approved_by": "design-lead-001",
        "approved_at": "2026-07-11T09:05:00-04:00",
        "approval_authority": "supervised-user-approval",
    }

    reference = memory.create_screen_reference(metadata)

    assert reference.to_dict() == metadata


def test_material_visual_mismatch_creates_design_variance_without_replacing_baseline() -> None:
    memory = agent_memory()
    baseline = memory.create_screen_reference(
        {
            "screen_id": "claims-list-v1",
            "route_or_surface": "/claims",
            "feature": "claims review",
            "user_role": "billing-specialist",
            "viewport": "1440x900",
            "application_state": "synthetic denied claim",
            "source_path": "design/screens/claims-list-v1.png",
            "sha256": "a" * 64,
            "captured_at": "2026-07-11T09:00:00-04:00",
            "capture_method": "playwright",
            "reference_status": "approved",
            "related_components": ["ClaimsTable"],
            "applicable_design_principles": ["dense-operational-layout"],
            "commit_or_revision": "abc1234",
            "supersedes": [],
            "contains_phi": False,
            "synthetic_or_deidentified_status": "synthetic",
            "approved_by": "design-lead-001",
            "approved_at": "2026-07-11T09:05:00-04:00",
            "approval_authority": "supervised-user-approval",
        }
    )

    result = memory.validate_visual_reference(
        baseline=baseline,
        observed_sha256="b" * 64,
        material_mismatch=True,
        rationale="Primary filters moved below the result table.",
        affected_components=["ClaimsTable", "StatusFilter"],
    )

    assert result.status == "variance"
    assert result.baseline_reference_id == baseline.screen_id
    assert result.variance.rationale == "Primary filters moved below the result table."
    assert result.variance.affected_components == ["ClaimsTable", "StatusFilter"]
    assert result.replaces_baseline is False


def test_proposed_reference_cannot_be_used_as_visual_baseline() -> None:
    memory = agent_memory()
    metadata = {
        "screen_id": "claims-list-proposed",
        "route_or_surface": "/claims",
        "feature": "claims review",
        "user_role": "billing-specialist",
        "viewport": "1440x900",
        "application_state": "synthetic denied claim",
        "source_path": "design/screens/claims-list.png",
        "sha256": "a" * 64,
        "captured_at": "2026-07-11T09:00:00-04:00",
        "capture_method": "playwright",
        "reference_status": "proposed",
        "related_components": ["ClaimsTable"],
        "applicable_design_principles": ["dense-operational-layout"],
        "commit_or_revision": "abc1234",
        "supersedes": [],
        "contains_phi": False,
        "synthetic_or_deidentified_status": "synthetic",
    }
    proposed = memory.create_screen_reference(metadata)

    with pytest.raises(ValueError, match="approved screen reference"):
        memory.validate_visual_reference(
            baseline=proposed,
            observed_sha256="b" * 64,
            material_mismatch=False,
        )


def test_approved_reference_requires_supervised_authority_metadata() -> None:
    memory = agent_memory()
    metadata = {
        "screen_id": "claims-list",
        "route_or_surface": "/claims",
        "feature": "claims review",
        "user_role": "billing-specialist",
        "viewport": "1440x900",
        "application_state": "synthetic denied claim",
        "source_path": "design/screens/claims-list.png",
        "sha256": "a" * 64,
        "captured_at": "2026-07-11T09:00:00-04:00",
        "capture_method": "playwright",
        "reference_status": "approved",
        "related_components": ["ClaimsTable"],
        "applicable_design_principles": [],
        "commit_or_revision": "abc1234",
        "supersedes": [],
        "contains_phi": False,
        "synthetic_or_deidentified_status": "synthetic",
    }

    with pytest.raises(ValueError, match="approval metadata"):
        memory.create_screen_reference(metadata)


def test_approved_successor_derives_superseded_context_without_mutating_predecessor() -> None:
    memory = agent_memory()
    references = [
        {"screen_id": "claims-v1", "reference_status": "approved", "supersedes": []},
        {"screen_id": "claims-v2", "reference_status": "approved", "supersedes": ["claims-v1"]},
    ]

    context = memory.compile_design_context(references=references)

    assert [reference["screen_id"] for reference in context.screen_references] == ["claims-v2"]
    assert context.excluded_superseded_ids == ["claims-v1"]


def test_screen_reference_verifies_workspace_controlled_screenshot_bytes(tmp_path: Path) -> None:
    memory = agent_memory()
    screenshot_root = tmp_path / "design" / "screens"
    screenshot_root.mkdir(parents=True)
    screenshot = screenshot_root / "claims.png"
    screenshot.write_bytes(b"synthetic screenshot bytes")
    metadata = {
        "screen_id": "claims-list",
        "route_or_surface": "/claims",
        "feature": "claims review",
        "user_role": "billing-specialist",
        "viewport": "1440x900",
        "application_state": "synthetic denied claim",
        "source_path": "claims.png",
        "sha256": sha256(screenshot.read_bytes()).hexdigest(),
        "captured_at": "2026-07-11T09:00:00-04:00",
        "capture_method": "playwright",
        "reference_status": "proposed",
        "related_components": ["ClaimsTable"],
        "applicable_design_principles": [],
        "commit_or_revision": "abc1234",
        "supersedes": [],
        "contains_phi": False,
        "synthetic_or_deidentified_status": "synthetic",
    }

    reference = memory.create_screen_reference(metadata, screenshot_root=screenshot_root)
    assert reference.sha256 == metadata["sha256"]

    screenshot.write_bytes(b"modified")
    with pytest.raises(ValueError, match="does not match screenshot bytes"):
        memory.create_screen_reference(metadata, screenshot_root=screenshot_root)


def test_superseded_screen_references_are_excluded_from_active_design_context() -> None:
    memory = agent_memory()
    references = [
        {
            "screen_id": "claims-list-v1",
            "reference_status": "superseded",
            "supersedes": [],
        },
        {
            "screen_id": "claims-list-v2",
            "reference_status": "approved",
            "supersedes": ["claims-list-v1"],
        },
        {
            "screen_id": "portal-home",
            "reference_status": "approved",
            "supersedes": [],
        },
    ]

    context = memory.compile_design_context(references=references)

    assert [item["screen_id"] for item in context.screen_references] == [
        "claims-list-v2",
        "portal-home",
    ]
    assert context.excluded_superseded_ids == ["claims-list-v1"]
