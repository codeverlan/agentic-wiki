from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from memwiki.coordinator_limits import (
    BudgetLimits,
    BudgetUsage,
    LimitAction,
    LimitController,
    LimitReason,
    QueueState,
    StopKind,
    StopRequest,
)

NOW = datetime(2026, 7, 11, 12, 0, tzinfo=timezone.utc)


def controller(**limits: object) -> LimitController:
    return LimitController(
        run_id="run-1",
        started_at=NOW,
        started_monotonic=100.0,
        limits=BudgetLimits(**limits),
    )


def test_no_artificial_phase_or_empty_ready_stop() -> None:
    decision = controller().evaluate(
        now=NOW,
        monotonic_now=120.0,
        queue=QueueState(ready=0, active=0, stale=0, undisposed=1, required_remaining=1),
    )
    assert decision.action is LimitAction.CONTINUE
    assert decision.reason is LimitReason.WORK_REMAINS
    assert decision.allow_new_dispatch is True


def test_completion_requires_proven_queue_drain() -> None:
    unproven = controller().evaluate(now=NOW, monotonic_now=101.0, queue=QueueState(), completion_proven=False)
    proven = controller().evaluate(now=NOW, monotonic_now=101.0, queue=QueueState(), completion_proven=True)
    assert unproven.action is LimitAction.CONTINUE
    assert unproven.reason is LimitReason.DRAIN_UNPROVEN
    assert proven.action is LimitAction.STOP
    assert proven.reason is LimitReason.COMPLETED


def test_graceful_stop_freezes_dispatch_and_drains_active_safe_work() -> None:
    item = StopRequest.create("stop-1", StopKind.GRACEFUL, NOW, "user")
    limits = controller().with_stop_request(item)
    draining = limits.evaluate(now=NOW, monotonic_now=102.0, queue=QueueState(active=2, required_remaining=2))
    stopped = limits.evaluate(now=NOW, monotonic_now=103.0, queue=QueueState())
    assert draining.action is LimitAction.DRAIN
    assert draining.allow_new_dispatch is False
    assert draining.cancel_active is False
    assert stopped.action is LimitAction.STOP
    assert stopped.reason is LimitReason.USER_STOP


def test_immediate_safety_stop_cancels_affected_operations() -> None:
    item = StopRequest.create("safety-1", StopKind.SAFETY, NOW, "privacy_boundary")
    decision = (
        controller()
        .with_stop_request(item)
        .evaluate(now=NOW, monotonic_now=102.0, queue=QueueState(active=3, required_remaining=3))
    )
    assert decision.action is LimitAction.CANCEL
    assert decision.cancel_active is True
    assert decision.reason is LimitReason.SAFETY_STOP


@pytest.mark.parametrize(
    ("kwargs", "usage", "elapsed", "reason"),
    [
        ({"time_limit_seconds": 30}, BudgetUsage(), 30.0, LimitReason.TIME_EXHAUSTED),
        ({"token_limit": 100}, BudgetUsage(tokens=100), 1.0, LimitReason.TOKEN_EXHAUSTED),
        ({"usage_limit": 5}, BudgetUsage(usage_units=5), 1.0, LimitReason.USAGE_EXHAUSTED),
        ({"cost_limit_micros": 50}, BudgetUsage(cost_micros=50), 1.0, LimitReason.COST_EXHAUSTED),
    ],
)
def test_hard_limits_create_resumable_evidence(
    kwargs: dict[str, int], usage: BudgetUsage, elapsed: float, reason: LimitReason
) -> None:
    limits = controller(**kwargs).record_usage(usage)
    decision = limits.evaluate(
        now=NOW + timedelta(days=4),
        monotonic_now=100.0 + elapsed,
        queue=QueueState(active=1, required_remaining=1),
    )
    assert decision.action is LimitAction.STOP
    assert decision.reason is reason
    assert decision.resumable is True
    assert decision.evidence["reason"] == reason.value
    assert decision.evidence["run_id"] == "run-1"


def test_hard_deadline_uses_wall_time_but_elapsed_limit_uses_monotonic_clock() -> None:
    deadline = NOW + timedelta(hours=1)
    limits = controller(time_limit_seconds=60, hard_deadline=deadline)
    rollback = limits.evaluate(now=NOW - timedelta(days=1), monotonic_now=160.0, queue=QueueState(active=1))
    deadline_hit = controller(hard_deadline=deadline).evaluate(
        now=deadline, monotonic_now=101.0, queue=QueueState(active=1)
    )
    assert rollback.reason is LimitReason.TIME_EXHAUSTED
    assert deadline_hit.reason is LimitReason.DEADLINE_REACHED


def test_reservations_prevent_oversubscription_and_restart_round_trip() -> None:
    limits = controller(token_limit=100, cost_limit_micros=500)
    limits = limits.reserve("attempt-1", tokens=70, cost_micros=300)
    assert limits.available.tokens == 30
    with pytest.raises(ValueError, match="exceeds available budget"):
        limits.reserve("attempt-2", tokens=31)
    restored = LimitController.from_dict(limits.to_dict())
    assert restored.to_dict() == limits.to_dict()
    assert restored.reserve("attempt-1", tokens=70, cost_micros=300) == restored


def test_settlement_charges_actual_once_and_releases_reservation() -> None:
    limits = controller(token_limit=100).reserve("attempt-1", tokens=60)
    settled = limits.settle("attempt-1", BudgetUsage(tokens=45))
    assert settled.usage.tokens == 45
    assert settled.available.tokens == 55
    assert settled.settle("attempt-1", BudgetUsage(tokens=45)) == settled


def test_precedence_is_safety_then_explicit_then_hard_limit_then_completion() -> None:
    base = controller(token_limit=1).record_usage(BudgetUsage(tokens=1))
    explicit = base.with_stop_request(StopRequest.create("user", StopKind.IMMEDIATE, NOW, "user"))
    safety = explicit.with_stop_request(
        StopRequest.create("safe", StopKind.SAFETY, NOW + timedelta(seconds=1), "safety")
    )
    queue = QueueState()
    assert (
        base.evaluate(now=NOW, monotonic_now=101.0, queue=queue, completion_proven=True).reason
        is LimitReason.TOKEN_EXHAUSTED
    )
    assert explicit.evaluate(now=NOW, monotonic_now=101.0, queue=queue).reason is LimitReason.USER_STOP
    assert safety.evaluate(now=NOW, monotonic_now=101.0, queue=queue).reason is LimitReason.SAFETY_STOP


def test_rejects_clock_regression_invalid_values_and_duplicate_stop_conflict() -> None:
    limits = controller()
    with pytest.raises(ValueError, match="monotonic clock regressed"):
        limits.evaluate(now=NOW, monotonic_now=99.0, queue=QueueState())
    with pytest.raises(ValueError, match="non-negative"):
        BudgetUsage(tokens=-1)
    item = StopRequest.create("same", StopKind.GRACEFUL, NOW, "user")
    limits = limits.with_stop_request(item)
    assert limits.with_stop_request(item) == limits
    with pytest.raises(ValueError, match="conflicting stop request"):
        limits.with_stop_request(StopRequest.create("same", StopKind.SAFETY, NOW, "safety"))
