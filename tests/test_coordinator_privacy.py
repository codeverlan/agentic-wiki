from __future__ import annotations

import json
from dataclasses import replace

import pytest

from memwiki.coordinator_privacy import (
    DataProfile,
    PrivacyAction,
    PrivacyPolicy,
    PrivacyViolationError,
    RuntimeSurface,
    ScanFinding,
)
from memwiki.policy import OperationContext


def context() -> OperationContext:
    return OperationContext(
        actor_id="synthetic-operator",
        actor_role="developer",
        purpose_of_use="synthetic development",
        session_id="synthetic-session",
    )


@pytest.mark.parametrize("profile", [DataProfile.YES, DataProfile.UNKNOWN])
def test_yes_and_unknown_profiles_require_synthetic_development(profile: DataProfile) -> None:
    policy = PrivacyPolicy(profile)

    assert policy.synthetic_only
    policy.authorize(PrivacyAction.WRITE_ARTIFACT, synthetic=True)
    with pytest.raises(PrivacyViolationError) as caught:
        policy.authorize(PrivacyAction.WRITE_ARTIFACT, synthetic=False)

    assert caught.value.violations[0].code == "synthetic-only"


def test_no_profile_does_not_add_phi_specific_synthetic_requirement() -> None:
    policy = PrivacyPolicy(DataProfile.NO)
    policy.authorize(PrivacyAction.WRITE_ARTIFACT, synthetic=False)
    assert not policy.synthetic_only


@pytest.mark.parametrize("action", [PrivacyAction.READ_PHI, PrivacyAction.MUTATE_PHI])
def test_phi_reads_and_mutations_require_operation_context(action: PrivacyAction) -> None:
    policy = PrivacyPolicy(DataProfile.YES)

    with pytest.raises(PrivacyViolationError, match="operation context"):
        policy.authorize(action, synthetic=False)
    policy.authorize(action, context=context(), synthetic=False)


def test_unknown_profile_denies_actual_phi_access_even_with_context() -> None:
    with pytest.raises(PrivacyViolationError) as caught:
        PrivacyPolicy(DataProfile.UNKNOWN).authorize(
            PrivacyAction.READ_PHI, context=context(), synthetic=False
        )

    assert caught.value.violations[0].code == "synthetic-only"


@pytest.mark.parametrize(
    "action",
    [
        PrivacyAction.REMOTE_ADAPTER,
        PrivacyAction.CLOUD_STORAGE,
        PrivacyAction.TELEMETRY,
        PrivacyAction.STATIC_EXPORT,
        PrivacyAction.EXTERNAL_INVOCATION,
    ],
)
@pytest.mark.parametrize("profile", [DataProfile.YES, DataProfile.UNKNOWN])
def test_phi_and_unknown_profiles_deny_nonlocal_runtime_actions(
    profile: DataProfile, action: PrivacyAction
) -> None:
    with pytest.raises(PrivacyViolationError) as caught:
        PrivacyPolicy(profile).authorize(action, context=context(), synthetic=True)

    assert caught.value.violations[0].code == "local-only"


def test_local_worker_and_local_validation_remain_available_in_phi_mode() -> None:
    policy = PrivacyPolicy(DataProfile.YES)
    policy.authorize(PrivacyAction.LOCAL_WORKER, synthetic=True)
    policy.authorize(PrivacyAction.LOCAL_VALIDATION, synthetic=True)


@pytest.mark.parametrize(
    "surface",
    [
        RuntimeSurface.PROMPT,
        RuntimeSurface.FILE,
        RuntimeSurface.LOG,
        RuntimeSurface.SCREENSHOT,
        RuntimeSurface.REPORT,
        RuntimeSurface.GIT,
        RuntimeSurface.HTML,
    ],
)
def test_canary_scanning_covers_every_runtime_surface_without_echoing_values(
    surface: RuntimeSurface,
) -> None:
    phi_canary = "SYNTHETIC-PHI-CANARY-4815"
    secret_canary = "SYNTHETIC-SECRET-CANARY-9264"
    policy = PrivacyPolicy(
        DataProfile.YES,
        phi_canaries=(phi_canary,),
        secret_canaries=(secret_canary,),
    )

    with pytest.raises(PrivacyViolationError) as caught:
        policy.inspect(
            surface,
            {"safe": [f"prefix {phi_canary} suffix", {"token": secret_canary}]},
            location="artifact/example",
        )

    violations = caught.value.violations
    assert {item.category for item in violations} == {"phi", "secret"}
    assert all(item.surface is surface for item in violations)
    dumped = json.dumps([item.to_dict() for item in violations]) + str(caught.value)
    assert phi_canary not in dumped
    assert secret_canary not in dumped
    assert all(len(item.evidence_sha256) == 64 for item in violations)


def test_sensitive_keys_are_detected_without_serializing_their_values() -> None:
    policy = PrivacyPolicy(DataProfile.NO)
    blocked = "SYNTHETIC-CREDENTIAL-VALUE"

    with pytest.raises(PrivacyViolationError) as caught:
        policy.inspect(RuntimeSurface.REPORT, {"api_key": blocked})

    assert caught.value.violations[0].code == "sensitive-key"
    assert blocked not in json.dumps(caught.value.to_dict())


def test_binary_screenshot_canaries_and_sensitive_locations_are_redacted() -> None:
    canary = "SYNTHETIC-SCREENSHOT-CANARY-2231"
    policy = PrivacyPolicy(DataProfile.YES, phi_canaries=(canary,))

    with pytest.raises(PrivacyViolationError) as caught:
        policy.inspect(
            RuntimeSurface.SCREENSHOT,
            b"image-prefix-SYNTHETIC-SCREENSHOT-CANARY-2231-image-suffix",
            location=f"screens/{canary}.png",
        )

    dumped = json.dumps(caught.value.to_dict())
    assert canary not in dumped
    assert caught.value.violations[0].location == "[REDACTED]"


def test_scan_hooks_add_structured_findings_and_cannot_leak_evidence() -> None:
    def hook(surface: RuntimeSurface, value: object) -> tuple[ScanFinding, ...]:
        assert surface is RuntimeSurface.FILE
        assert value == "synthetic fixture"
        return (ScanFinding(category="phi", code="custom-detector", evidence="blocked detail"),)

    policy = PrivacyPolicy(DataProfile.YES, scan_hooks=(hook,))
    with pytest.raises(PrivacyViolationError) as caught:
        policy.inspect(RuntimeSurface.FILE, "synthetic fixture", location="draft/input.json")

    violation = caught.value.violations[0]
    assert violation.code == "custom-detector"
    assert violation.location == "draft/input.json"
    assert "blocked detail" not in json.dumps(violation.to_dict())


def test_invalid_profiles_and_unstructured_hook_results_are_rejected() -> None:
    with pytest.raises(ValueError, match="profile"):
        PrivacyPolicy("maybe")  # type: ignore[arg-type]

    policy = PrivacyPolicy(DataProfile.YES, scan_hooks=(lambda _surface, _value: ("bad",),))
    with pytest.raises(TypeError, match="ScanFinding"):
        policy.scan(RuntimeSurface.FILE, "value")


def test_structured_violation_round_trip_contains_no_payload() -> None:
    finding = ScanFinding(category="secret", code="canary", evidence="sensitive value")
    violation = PrivacyPolicy(DataProfile.YES)._finding_to_violation(
        RuntimeSurface.LOG, finding, "logs/run.jsonl"
    )
    restored = type(violation).from_dict(violation.to_dict())

    assert restored == violation
    assert "sensitive value" not in json.dumps(restored.to_dict())
    assert replace(restored, location="other").location == "other"
