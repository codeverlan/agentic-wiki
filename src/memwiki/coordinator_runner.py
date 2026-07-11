from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import Callable, Optional, Protocol, Tuple

from memwiki.coordinator_lease import (
    CoordinatorLease,
    CoordinatorLeaseAuthority,
    LeaseIdentity,
)
from memwiki.coordinator_receipts import CommandReceiptStore
from memwiki.coordinator_scheduler import (
    SchedulerDecision,
    SchedulerInput,
    SchedulerIntent,
    reconciliation_tick,
)


class WakeSource(Protocol):
    def wait(self, timeout: timedelta) -> str:
        """Wait for an event or timeout and return a diagnostic wake reason."""


class RunnerStopReason(str, Enum):
    COMPLETED = "completed"
    REQUESTED = "requested"
    HARD_DEADLINE = "hard_deadline"


@dataclass(frozen=True)
class RunnerConfig:
    lease_ttl: timedelta = timedelta(seconds=30)
    heartbeat_interval: timedelta = timedelta(seconds=10)
    idle_sleep: timedelta = timedelta(seconds=1)

    def __post_init__(self) -> None:
        if self.lease_ttl <= timedelta(0):
            raise ValueError("lease_ttl must be positive")
        if not timedelta(0) < self.heartbeat_interval < self.lease_ttl:
            raise ValueError("heartbeat_interval must be positive and less than lease_ttl")
        if self.idle_sleep <= timedelta(0):
            raise ValueError("idle_sleep must be positive")


@dataclass(frozen=True)
class RunnerResult:
    reason: RunnerStopReason
    ticks: int
    intents_applied: int
    heartbeats: int
    fencing_token: int
    started_at: datetime
    stopped_at: datetime
    wake_reasons: Tuple[str, ...]


StateBuilder = Callable[[CoordinatorLease, datetime], SchedulerInput]
IntentApplier = Callable[[SchedulerIntent, int], None]
DecisionPredicate = Callable[[SchedulerDecision], bool]
Clock = Callable[[], datetime]


class ContinuousCoordinatorRunner:
    """Drive pure reconciliation ticks until an explicit terminal predicate passes."""

    def __init__(
        self,
        authority: CoordinatorLeaseAuthority,
        owner: LeaseIdentity,
        receipts: CommandReceiptStore,
        clock: Clock,
        wake_source: WakeSource,
        config: RunnerConfig,
    ) -> None:
        self._authority = authority
        self._owner = owner
        self._receipts = receipts
        self._clock = clock
        self._wake_source = wake_source
        self._config = config

    def run(
        self,
        build_state: StateBuilder,
        *,
        apply_intent: IntentApplier,
        is_complete: DecisionPredicate,
        should_stop: Optional[DecisionPredicate] = None,
        hard_deadline: Optional[datetime] = None,
    ) -> RunnerResult:
        started_at = self._now()
        if hard_deadline is not None:
            _aware(hard_deadline, "hard_deadline")

        # A prior process may have crossed the side-effect boundary. Such work
        # requires reconciliation and must never be blindly replayed.
        self._receipts.reconcile_interrupted()
        lease = self._authority.acquire(
            self._owner,
            now=started_at,
            ttl=self._config.lease_ttl,
        )
        next_heartbeat = started_at + self._config.heartbeat_interval
        ticks = 0
        applied = 0
        heartbeats = 0
        wakes = []
        reason: Optional[RunnerStopReason] = None

        try:
            while reason is None:
                now = self._now()
                if hard_deadline is not None and now >= hard_deadline:
                    reason = RunnerStopReason.HARD_DEADLINE
                    break
                if now >= next_heartbeat:
                    lease = self._authority.heartbeat(
                        self._owner,
                        lease.fencing_token,
                        now=now,
                        ttl=self._config.lease_ttl,
                    )
                    heartbeats += 1
                    next_heartbeat = now + self._config.heartbeat_interval

                decision = reconciliation_tick(build_state(lease, now), now=now)
                ticks += 1
                for intent in decision.intents:
                    if self._apply_once(
                        _intent_key(intent),
                        intent,
                        lease.fencing_token,
                        apply_intent,
                    ):
                        applied += 1

                if is_complete(decision):
                    reason = RunnerStopReason.COMPLETED
                    break
                if should_stop is not None and should_stop(decision):
                    reason = RunnerStopReason.REQUESTED
                    break

                timeout = self._bounded_wait(now, next_heartbeat, hard_deadline)
                wakes.append(self._wake_source.wait(timeout))
        finally:
            stopped_at = self._now()
            current = self._authority.lease
            if current is not None and current.is_live(stopped_at) and current.owner == self._owner:
                self._authority.release(
                    self._owner,
                    current.fencing_token,
                    now=stopped_at,
                )

        assert reason is not None
        return RunnerResult(
            reason,
            ticks,
            applied,
            heartbeats,
            lease.fencing_token,
            started_at,
            stopped_at,
            tuple(wakes),
        )

    def _apply_once(
        self,
        key: str,
        intent: SchedulerIntent,
        fencing_token: int,
        apply_intent: IntentApplier,
    ) -> bool:
        arguments = {
            "intent": asdict(intent),
        }
        receipt = self._receipts.plan(key, "apply_scheduler_intent", arguments)
        if receipt.status != "planned":
            return False
        self._receipts.transition(key, "started")
        try:
            apply_intent(intent, fencing_token)
        except Exception as exc:
            self._receipts.transition(key, "failed", error=type(exc).__name__)
            raise
        except BaseException:
            self._receipts.transition(key, "outcome_unknown")
            raise
        self._receipts.transition(key, "succeeded")
        return True

    def _bounded_wait(
        self,
        now: datetime,
        next_heartbeat: datetime,
        hard_deadline: Optional[datetime],
    ) -> timedelta:
        limits = [self._config.idle_sleep, next_heartbeat - now]
        if hard_deadline is not None:
            limits.append(hard_deadline - now)
        return max(timedelta(0), min(limits))

    def _now(self) -> datetime:
        value = self._clock()
        _aware(value, "clock")
        return value


def _aware(value: datetime, label: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware")


def _intent_key(intent: SchedulerIntent) -> str:
    encoded = json.dumps(
        asdict(intent),
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
        allow_nan=False,
    ).encode("utf-8")
    return f"scheduler-intent:{hashlib.sha256(encoded).hexdigest()}"
