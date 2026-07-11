from __future__ import annotations

from datetime import datetime, timezone

import pytest

from memwiki.coordinator_design import (
    DesignDecision,
    DesignEvidence,
    DesignEvidenceGate,
    DesignMemory,
    DesignReference,
    EvidenceDisposition,
    ScreenInventoryItem,
    ScreenshotEvidence,
)
from memwiki.coordinator_privacy import DataProfile

NOW = datetime(2026, 7, 11, 12, 0, tzinfo=timezone.utc)
SHA = "a" * 64
BASELINE = "b" * 64


def memory(*, profile: DataProfile = DataProfile.NO) -> DesignMemory:
    return DesignMemory(
        project_type="web application",
        graphics_choice="Use generated product imagery and project design references",
        data_profile=profile,
        principles=("Dense, accessible operational UI", "Consistent navigation"),
        references=(
            DesignReference(
                reference_id="ref-1",
                kind="plugin",
                label="Design Studio",
                locator="plugin://design-studio",
                version="0.1.0",
            ),
        ),
        assets=("design/logo.png",),
        screens=(
            ScreenInventoryItem("dashboard", "/dashboard", ("default", "empty", "error")),
        ),
        decisions=(
            DesignDecision("decision-1", 1, "Use persistent navigation", "accepted", NOW),
        ),
        updated_at=NOW,
    )


def screenshot(*, viewport: str, synthetic: bool = False, commit: str = "commit-1") -> ScreenshotEvidence:
    return ScreenshotEvidence(
        evidence_id=f"shot-{viewport}",
        route="/dashboard",
        screen_id="dashboard",
        state="default",
        viewport=viewport,
        commit_revision=commit,
        content_sha256=SHA,
        baseline_sha256=BASELINE,
        variance_percent=0.2,
        variance_limit_percent=1.0,
        captured_at=NOW,
        reviewed=True,
        synthetic=synthetic,
    )


def evidence(*, profile: DataProfile = DataProfile.NO) -> DesignEvidence:
    synthetic = profile is not DataProfile.NO
    return DesignEvidence(
        slice_id="AC-X",
        material_frontend=True,
        commit_revision="commit-1",
        screenshots=(
            screenshot(viewport="desktop", synthetic=synthetic),
            screenshot(viewport="mobile", synthetic=synthetic),
        ),
        accessibility_status="passed",
        accessibility_evidence_sha256=SHA,
        disposition=EvidenceDisposition.PROVIDED,
        disposition_reason=None,
        reviewed_at=NOW,
    )


def test_design_memory_requires_interview_choices_and_versioned_decisions() -> None:
    with pytest.raises(ValueError, match="project type"):
        DesignMemory(
            project_type="",
            graphics_choice="choice",
            data_profile=DataProfile.NO,
            updated_at=NOW,
        )
    with pytest.raises(ValueError, match="graphics choice"):
        DesignMemory(
            project_type="app",
            graphics_choice="",
            data_profile=DataProfile.NO,
            updated_at=NOW,
        )
    with pytest.raises(ValueError, match="decision versions"):
        DesignMemory(
            project_type="app",
            graphics_choice="choice",
            data_profile=DataProfile.NO,
            decisions=(
                DesignDecision("same", 2, "new", "accepted", NOW),
                DesignDecision("same", 1, "old", "superseded", NOW),
            ),
            updated_at=NOW,
        )


def test_design_memory_round_trips_and_excludes_credentials() -> None:
    original = memory()
    assert DesignMemory.from_dict(original.to_dict()) == original
    with pytest.raises(ValueError, match="credential"):
        DesignReference("bad", "api", "Vendor", "https://x.test?api_key=secret", None)
    with pytest.raises(ValueError, match="credential"):
        DesignReference("bad", "plugin", "password=hunter2", "plugin://safe", None)


def test_valid_desktop_mobile_accessible_evidence_passes() -> None:
    result = DesignEvidenceGate().evaluate(memory(), evidence(), expected_commit="commit-1")
    assert result.accepted
    assert result.findings == ()
    assert result.can_complete


@pytest.mark.parametrize(
    ("mutator", "finding"),
    [
        (lambda item: item.__dict__.update(commit_revision="old"), "stale commit"),
        (lambda item: item.__dict__.update(content_sha256="bad"), "invalid screenshot hash"),
        (lambda item: item.__dict__.update(variance_percent=2.0), "variance exceeds limit"),
        (lambda item: item.__dict__.update(reviewed=False), "not reviewed"),
    ],
)
def test_invalid_screenshot_metadata_blocks_completion(mutator: object, finding: str) -> None:
    shot = screenshot(viewport="desktop")
    mutator(shot)  # type: ignore[operator]
    item = evidence()
    object.__setattr__(item, "screenshots", (shot, screenshot(viewport="mobile")))
    result = DesignEvidenceGate().evaluate(memory(), item, expected_commit="commit-1")
    assert not result.can_complete
    assert any(finding in value for value in result.findings)


def test_missing_viewport_and_accessibility_failure_block_completion() -> None:
    item = evidence()
    object.__setattr__(item, "screenshots", (screenshot(viewport="desktop"),))
    object.__setattr__(item, "accessibility_status", "failed")
    result = DesignEvidenceGate().evaluate(memory(), item, expected_commit="commit-1")
    assert "required viewport missing: mobile" in result.findings
    assert "accessibility validation did not pass" in result.findings
    assert not result.can_complete


def test_new_baseline_requires_a_supervised_proposal_reference() -> None:
    shot = screenshot(viewport="desktop")
    object.__setattr__(shot, "baseline_sha256", None)
    item = evidence()
    object.__setattr__(item, "screenshots", (shot, screenshot(viewport="mobile")))
    result = DesignEvidenceGate().evaluate(memory(), item, expected_commit="commit-1")
    assert any("baseline missing without supervised proposal" in value for value in result.findings)

    object.__setattr__(shot, "baseline_proposal_supervision_id", "supervision-baseline-1")
    result = DesignEvidenceGate().evaluate(memory(), item, expected_commit="commit-1")
    assert result.can_complete


@pytest.mark.parametrize("profile", [DataProfile.YES, DataProfile.UNKNOWN])
def test_phi_yes_or_unknown_requires_synthetic_evidence(profile: DataProfile) -> None:
    item = evidence(profile=profile)
    shots = list(item.screenshots)
    object.__setattr__(shots[0], "synthetic", False)
    object.__setattr__(item, "screenshots", tuple(shots))
    result = DesignEvidenceGate().evaluate(memory(profile=profile), item, expected_commit="commit-1")
    assert "PHI posture requires synthetic screenshot evidence" in result.findings
    assert not result.can_complete


def test_non_frontend_work_may_use_justified_not_applicable_disposition() -> None:
    item = DesignEvidence(
        slice_id="AC-BACKEND",
        material_frontend=False,
        commit_revision="commit-1",
        screenshots=(),
        accessibility_status="not-applicable",
        accessibility_evidence_sha256=None,
        disposition=EvidenceDisposition.NOT_APPLICABLE,
        disposition_reason="Backend-only schema validation with no rendered surface",
        reviewed_at=NOW,
    )
    result = DesignEvidenceGate().evaluate(memory(), item, expected_commit="commit-1")
    assert result.can_complete


def test_material_frontend_cannot_bypass_evidence_with_disposition() -> None:
    item = DesignEvidence(
        slice_id="AC-UI",
        material_frontend=True,
        commit_revision="commit-1",
        screenshots=(),
        accessibility_status="not-applicable",
        accessibility_evidence_sha256=None,
        disposition=EvidenceDisposition.NOT_APPLICABLE,
        disposition_reason="No browser available",
        reviewed_at=NOW,
    )
    result = DesignEvidenceGate().evaluate(memory(), item, expected_commit="commit-1")
    assert not result.can_complete
    assert "material frontend work requires provided design evidence" in result.findings


def test_semantic_html_contains_inventory_decisions_and_json_ld_without_secrets() -> None:
    rendered = memory().to_html()
    assert '<script type="application/ld+json">' in rendered
    assert "dashboard" in rendered
    assert "persistent navigation" in rendered
    assert "plugin://design-studio" in rendered
    assert "api_key" not in rendered
