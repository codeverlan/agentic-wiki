from __future__ import annotations

from datetime import datetime, timezone

import pytest

from memwiki.coordinator_retry import RetryLedger, RetryPolicy

NOW = datetime(2026, 7, 11, 12, 0, tzinfo=timezone.utc)


def test_validation_repairs_are_bounded_to_two_and_then_escalate() -> None:
    ledger = RetryLedger.empty()
    policy = RetryPolicy(max_validation_repairs=2)

    first = ledger.record_outcome("slice-a", "validation_repair", NOW, policy)
    second = ledger.record_outcome("slice-a", "validation_repair", NOW, policy)
    exhausted = ledger.record_outcome("slice-a", "validation_repair", NOW, policy)

    assert first.action == second.action == "repair"
    assert first.ordinal == 1
    assert second.ordinal == 2
    assert exhausted.action == "terminal_escalation"
    assert exhausted.reason == "validation_repair_exhausted"
    assert exhausted.affects_slice_only is True
    assert ledger.slice_state("slice-a").validation_repairs == 2


def test_infrastructure_retries_use_injected_clock_and_deterministic_backoff() -> None:
    policy = RetryPolicy(
        max_infrastructure_retries=3,
        initial_backoff_seconds=10,
        backoff_multiplier=2,
        max_backoff_seconds=60,
        jitter_fraction=0.2,
    )
    left = RetryLedger.empty()
    right = RetryLedger.empty()

    left_decision = left.record_outcome("slice-a", "infrastructure_retry", NOW, policy)
    right_decision = right.record_outcome("slice-a", "infrastructure_retry", NOW, policy)

    assert left_decision == right_decision
    assert left_decision.action == "retry_after"
    assert left_decision.eligible_at is not None
    delay = (left_decision.eligible_at - NOW).total_seconds()
    assert 8 <= delay <= 12


def test_restart_does_not_reset_attempt_or_repair_counters() -> None:
    policy = RetryPolicy(max_infrastructure_retries=2, max_validation_repairs=2)
    ledger = RetryLedger.empty()
    ledger.record_outcome("slice-a", "infrastructure_retry", NOW, policy)
    repair = ledger.record_outcome("slice-a", "validation_repair", NOW, policy)
    ledger.record_repair_action(
        slice_id="slice-a",
        decision_id=repair.decision_id,
        action="update implementation",
        evidence_ids=("receipt-1",),
        recorded_at=NOW,
    )

    restored = RetryLedger.from_dict(ledger.to_dict())
    next_retry = restored.record_outcome("slice-a", "infrastructure_retry", NOW, policy)
    next_repair = restored.record_outcome("slice-a", "validation_repair", NOW, policy)

    assert next_retry.ordinal == 2
    assert next_repair.ordinal == 2
    assert restored.to_dict()["repair_actions"][0]["evidence_ids"] == ["receipt-1"]


@pytest.mark.parametrize(
    ("classification", "action"),
    [
        ("blocker", "blocked"),
        ("cancellation", "cancelled"),
        ("terminal_failure", "terminal_escalation"),
    ],
)
def test_non_retry_classifications_are_persisted(
    classification: str, action: str
) -> None:
    ledger = RetryLedger.empty()

    decision = ledger.record_outcome("slice-a", classification, NOW, RetryPolicy())

    assert decision.action == action
    assert ledger.slice_state("slice-a").attempts == 1
    assert RetryLedger.from_dict(ledger.to_dict()).to_dict() == ledger.to_dict()


def test_exhausting_one_slice_does_not_globally_stop_independent_work() -> None:
    policy = RetryPolicy(max_infrastructure_retries=1)
    ledger = RetryLedger.empty()
    ledger.record_outcome("slice-a", "infrastructure_retry", NOW, policy)

    exhausted = ledger.record_outcome("slice-a", "infrastructure_retry", NOW, policy)
    independent = ledger.record_outcome("slice-b", "infrastructure_retry", NOW, policy)

    assert exhausted.action == "terminal_escalation"
    assert exhausted.affects_slice_only is True
    assert independent.action == "retry_after"


def test_invalid_persisted_ledger_is_rejected() -> None:
    with pytest.raises(ValueError, match="unknown classification"):
        RetryLedger.from_dict(
            {
                "schema_version": 1,
                "slices": {},
                "decisions": [
                    {
                        "decision_id": "d",
                        "slice_id": "s",
                        "classification": "mystery",
                        "action": "retry_after",
                        "ordinal": 1,
                        "recorded_at": "2026-07-11T12:00:00+00:00",
                        "eligible_at": None,
                        "reason": "x",
                        "affects_slice_only": True,
                    }
                ],
                "repair_actions": [],
            }
        )


def test_persisted_counters_must_match_the_decision_history() -> None:
    ledger = RetryLedger.empty()
    ledger.record_outcome("slice-a", "infrastructure_retry", NOW, RetryPolicy())
    payload = ledger.to_dict()
    slices = payload["slices"]
    assert isinstance(slices, dict)
    slice_state = slices["slice-a"]
    assert isinstance(slice_state, dict)
    slice_state["attempts"] = 0

    with pytest.raises(ValueError, match="counters do not match"):
        RetryLedger.from_dict(payload)
