from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List

import pytest

from memwiki.coordinator_lease import CoordinatorLeaseAuthority, LeaseIdentity
from memwiki.coordinator_models import SliceRecord
from memwiki.coordinator_receipts import CommandReceiptStore
from memwiki.coordinator_runner import (
    ContinuousCoordinatorRunner,
    RunnerConfig,
    RunnerStopReason,
)
from memwiki.coordinator_scheduler import SchedulerDecision, SchedulerInput

START = datetime(2026, 7, 11, 12, 0, tzinfo=timezone.utc)
OWNER = LeaseIdentity("host", 42, "runner")


class FakeClock:
    def __init__(self) -> None:
        self.current = START

    def now(self) -> datetime:
        return self.current

    def advance(self, duration: timedelta) -> None:
        self.current += duration


class FakeWakeSource:
    def __init__(self, clock: FakeClock, reasons: List[str] | None = None) -> None:
        self.clock = clock
        self.reasons = list(reasons or [])
        self.waits: List[timedelta] = []

    def wait(self, timeout: timedelta) -> str:
        self.waits.append(timeout)
        self.clock.advance(timeout)
        return self.reasons.pop(0) if self.reasons else "timeout"


def node(slice_id: str, status: str = "waiting") -> SliceRecord:
    return SliceRecord(slice_id, slice_id, status, [], [f"src/{slice_id}"], True)


def make_state(slices: tuple[SliceRecord, ...], lease: object) -> SchedulerInput:
    assert hasattr(lease, "fencing_token")
    return SchedulerInput(
        slices=slices,
        slice_leases=(),
        worker_observations=(),
        discovery_proposals=(),
        coordinator_lease=lease,  # type: ignore[arg-type]
        owner=OWNER,
        fencing_token=lease.fencing_token,  # type: ignore[attr-defined]
        max_workers=6,
    )


def runner(tmp_path: Path, clock: FakeClock, wake: FakeWakeSource) -> ContinuousCoordinatorRunner:
    return ContinuousCoordinatorRunner(
        authority=CoordinatorLeaseAuthority(),
        owner=OWNER,
        receipts=CommandReceiptStore(tmp_path / "receipts.json"),
        clock=clock.now,
        wake_source=wake,
        config=RunnerConfig(
            lease_ttl=timedelta(seconds=10),
            heartbeat_interval=timedelta(seconds=4),
            idle_sleep=timedelta(seconds=3),
        ),
    )


def test_continues_across_completed_slices_and_temporary_empty_ready_intervals(
    tmp_path: Path,
) -> None:
    clock = FakeClock()
    wake = FakeWakeSource(clock)
    statuses = ["waiting", "active", "completed"]
    ticks = 0

    def state(lease: object, now: datetime) -> SchedulerInput:
        nonlocal ticks
        status = statuses[min(ticks, len(statuses) - 1)]
        ticks += 1
        return make_state((node("AC-A", status),), lease)

    result = runner(tmp_path, clock, wake).run(
        state,
        apply_intent=lambda intent, token: None,
        is_complete=lambda decision: all(item.status == "completed" for item in decision.slices),
    )

    assert result.reason is RunnerStopReason.COMPLETED
    assert result.ticks == 3
    assert len(wake.waits) == 2


def test_blocked_slice_does_not_prevent_independent_dispatch(tmp_path: Path) -> None:
    clock = FakeClock()
    wake = FakeWakeSource(clock)
    applied: List[str] = []
    calls = 0

    def state(lease: object, now: datetime) -> SchedulerInput:
        nonlocal calls
        calls += 1
        independent = "waiting" if calls == 1 else "completed"
        return make_state((node("AC-BLOCKED", "blocked"), node("AC-INDEPENDENT", independent)), lease)

    result = runner(tmp_path, clock, wake).run(
        state,
        apply_intent=lambda intent, token: applied.append(f"{intent.kind}:{intent.slice_id}"),
        is_complete=lambda decision: any(item.status == "completed" for item in decision.slices),
    )

    assert "dispatch:AC-INDEPENDENT" in applied
    assert result.reason is RunnerStopReason.COMPLETED


def test_user_return_wakes_runner_without_cancelling_safe_work(tmp_path: Path) -> None:
    clock = FakeClock()
    wake = FakeWakeSource(clock, ["user_return"])
    calls = 0

    def state(lease: object, now: datetime) -> SchedulerInput:
        nonlocal calls
        calls += 1
        return make_state((node("AC-A", "completed" if calls == 2 else "active"),), lease)

    result = runner(tmp_path, clock, wake).run(
        state,
        apply_intent=lambda intent, token: None,
        is_complete=lambda decision: decision.slices[0].status == "completed",
    )

    assert result.reason is RunnerStopReason.COMPLETED
    assert result.wake_reasons == ("user_return",)
    assert result.ticks == 2


def test_heartbeats_lease_and_uses_current_fencing_token(tmp_path: Path) -> None:
    clock = FakeClock()
    wake = FakeWakeSource(clock)
    tokens: List[int] = []
    ticks = 0

    def state(lease: object, now: datetime) -> SchedulerInput:
        nonlocal ticks
        ticks += 1
        return make_state((node("AC-A", "completed" if ticks == 3 else "waiting"),), lease)

    result = runner(tmp_path, clock, wake).run(
        state,
        apply_intent=lambda intent, token: tokens.append(token),
        is_complete=lambda decision: decision.slices[0].status == "completed",
    )

    assert result.heartbeats >= 1
    assert set(tokens) == {result.fencing_token}


def test_hard_deadline_is_a_real_boundary_and_sleep_is_bounded(tmp_path: Path) -> None:
    clock = FakeClock()
    wake = FakeWakeSource(clock)
    result = runner(tmp_path, clock, wake).run(
        lambda lease, now: make_state((node("AC-A", "active"),), lease),
        apply_intent=lambda intent, token: None,
        is_complete=lambda decision: False,
        hard_deadline=START + timedelta(seconds=5),
    )

    assert result.reason is RunnerStopReason.HARD_DEADLINE
    assert sum(wake.waits, timedelta()) == timedelta(seconds=5)
    assert all(duration <= timedelta(seconds=3) for duration in wake.waits)
    assert wake.waits[-1] == timedelta(seconds=1)


def test_explicit_stop_is_checked_only_at_tick_boundary(tmp_path: Path) -> None:
    clock = FakeClock()
    wake = FakeWakeSource(clock)
    checks = 0

    def stop(decision: SchedulerDecision) -> bool:
        nonlocal checks
        checks += 1
        return checks == 2

    result = runner(tmp_path, clock, wake).run(
        lambda lease, now: make_state((node("AC-A", "active"),), lease),
        apply_intent=lambda intent, token: None,
        is_complete=lambda decision: False,
        should_stop=stop,
    )

    assert result.reason is RunnerStopReason.REQUESTED
    assert result.ticks == 2


def test_successful_intent_is_not_reapplied_after_restart(tmp_path: Path) -> None:
    clock = FakeClock()
    wake = FakeWakeSource(clock)
    receipts = CommandReceiptStore(tmp_path / "receipts.json")
    authority = CoordinatorLeaseAuthority()
    applied: List[str] = []

    def build() -> ContinuousCoordinatorRunner:
        return ContinuousCoordinatorRunner(
            authority=authority,
            owner=OWNER,
            receipts=receipts,
            clock=clock.now,
            wake_source=wake,
            config=RunnerConfig(idle_sleep=timedelta(seconds=1)),
        )

    def state(lease: object, now: datetime) -> SchedulerInput:
        return make_state((node("AC-A"),), lease)

    def complete(decision: SchedulerDecision) -> bool:
        return True

    build().run(state, apply_intent=lambda intent, token: applied.append(intent.kind), is_complete=complete)
    build().run(state, apply_intent=lambda intent, token: applied.append(intent.kind), is_complete=complete)

    assert applied == ["dispatch"]


def test_crashed_started_intent_becomes_outcome_unknown_and_is_not_replayed(
    tmp_path: Path,
) -> None:
    clock = FakeClock()
    wake = FakeWakeSource(clock)
    receipts = CommandReceiptStore(tmp_path / "receipts.json")
    authority = CoordinatorLeaseAuthority()
    calls = 0

    def apply(intent: object, token: int) -> None:
        nonlocal calls
        calls += 1
        raise KeyboardInterrupt

    first = ContinuousCoordinatorRunner(
        authority, OWNER, receipts, clock.now, wake, RunnerConfig()
    )
    with pytest.raises(KeyboardInterrupt):
        first.run(
            lambda lease, now: make_state((node("AC-A"),), lease),
            apply_intent=apply,  # type: ignore[arg-type]
            is_complete=lambda decision: True,
        )

    second = ContinuousCoordinatorRunner(
        authority, OWNER, receipts, clock.now, wake, RunnerConfig()
    )
    second.run(
        lambda lease, now: make_state((node("AC-A"),), lease),
        apply_intent=lambda intent, token: pytest.fail("must not replay unknown outcome"),
        is_complete=lambda decision: True,
    )

    assert calls == 1
    receipt = next(iter(receipts._read().values()))
    assert receipt.status == "outcome_unknown"


def test_rejects_naive_deadline_and_invalid_configuration(tmp_path: Path) -> None:
    clock = FakeClock()
    wake = FakeWakeSource(clock)
    with pytest.raises(ValueError, match="heartbeat_interval"):
        RunnerConfig(lease_ttl=timedelta(seconds=2), heartbeat_interval=timedelta(seconds=2))
    with pytest.raises(ValueError, match="timezone-aware"):
        runner(tmp_path, clock, wake).run(
            lambda lease, now: make_state((node("AC-A"),), lease),
            apply_intent=lambda intent, token: None,
            is_complete=lambda decision: False,
            hard_deadline=datetime(2026, 7, 11),
        )
