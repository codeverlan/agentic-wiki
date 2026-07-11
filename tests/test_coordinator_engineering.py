from __future__ import annotations

import json

import pytest

from memwiki.coordinator_engineering import (
    EvaluationResult,
    ExecutionTier,
    SliceDefinition,
    SliceEvaluation,
    SliceRisk,
    advise_decomposition,
    route_execution_tier,
)


def _evaluation() -> SliceEvaluation:
    return SliceEvaluation.create(
        slice_id="slice-eval",
        capability_checks=("pytest:feature",),
        regression_checks=("pytest:regression",),
        baseline_revision="commit-before",
        baseline={"pytest:feature": False, "pytest:regression": True},
    )


def test_evaluation_compares_baseline_and_integrated_results() -> None:
    evaluation = _evaluation()
    assert type(evaluation).from_dict(evaluation.to_dict()) == evaluation
    result = evaluation.compare(
        integrated_revision="commit-after",
        observed={"pytest:feature": True, "pytest:regression": True},
    )

    assert result.status is EvaluationResult.IMPROVED
    assert result.completion_gate_passed is True
    assert result.newly_passing == ("pytest:feature",)
    assert result.regressions == ()
    assert type(result).from_dict(result.to_dict()) == result


def test_feature_success_does_not_hide_a_regression() -> None:
    result = _evaluation().compare(
        integrated_revision="commit-after",
        observed={"pytest:feature": True, "pytest:regression": False},
    )

    assert result.status is EvaluationResult.REGRESSED
    assert result.completion_gate_passed is False
    assert result.regressions == ("pytest:regression",)


def test_evaluation_requires_complete_before_and_after_evidence() -> None:
    with pytest.raises(ValueError, match="baseline evidence"):
        SliceEvaluation.create(
            slice_id="slice-eval",
            capability_checks=("feature",),
            regression_checks=("regression",),
            baseline_revision="commit-before",
            baseline={"feature": False},
        )

    with pytest.raises(ValueError, match="observed evidence"):
        _evaluation().compare(
            integrated_revision="commit-after",
            observed={"pytest:feature": True},
        )


def test_sensitive_values_are_rejected_from_evaluation_metadata() -> None:
    with pytest.raises(ValueError, match="sensitive"):
        SliceEvaluation.create(
            slice_id="slice-eval",
            capability_checks=("feature",),
            regression_checks=(),
            baseline_revision="commit-before",
            baseline={"feature": False},
            metadata={"api_key": "do-not-store"},
        )


def test_decomposition_advice_is_non_blocking_and_duration_is_not_a_gate() -> None:
    advice = advise_decomposition(
        SliceDefinition(
            slice_id="slice-wide",
            objective="Change storage, authentication, and the dashboard",
            dominant_risks=("storage", "authentication", "frontend"),
            verification=("unit tests", "browser proof"),
            done_condition="All checks pass at the integrated revision",
            estimated_minutes=240,
        )
    )

    assert advice.may_proceed is True
    assert advice.split_recommended is True
    assert "multiple dominant risks" in advice.reasons
    assert all("15" not in reason for reason in advice.reasons)


@pytest.mark.parametrize(
    ("risk", "complexity", "uncertainty", "expected"),
    [
        (SliceRisk.LOW, 1, 1, ExecutionTier.NARROW),
        (SliceRisk.MODERATE, 3, 3, ExecutionTier.IMPLEMENTATION),
        (SliceRisk.SECURITY_CRITICAL, 1, 1, ExecutionTier.FRONTIER),
    ],
)
def test_provider_neutral_routing(
    risk: SliceRisk, complexity: int, uncertainty: int, expected: ExecutionTier
) -> None:
    decision = route_execution_tier(
        risk=risk,
        complexity=complexity,
        uncertainty=uncertainty,
        context_size=2,
        qualified_failures=0,
    )

    assert decision.tier is expected
    serialized = json.dumps(decision.to_dict()).lower()
    assert all(name not in serialized for name in ("claude", "haiku", "sonnet", "opus"))


def test_evidence_backed_failures_escalate_capability_tier() -> None:
    decision = route_execution_tier(
        risk=SliceRisk.LOW,
        complexity=1,
        uncertainty=1,
        context_size=1,
        qualified_failures=2,
    )

    assert decision.tier is ExecutionTier.IMPLEMENTATION
    assert "qualified failures" in " ".join(decision.reasons)
