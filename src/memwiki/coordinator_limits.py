from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import datetime
from enum import Enum
from typing import Any, Dict, Optional, Tuple


class StopKind(str, Enum):
    GRACEFUL = "graceful"
    IMMEDIATE = "immediate"
    SAFETY = "safety"


class LimitAction(str, Enum):
    CONTINUE = "continue"
    DRAIN = "drain"
    STOP = "stop"
    CANCEL = "cancel"


class LimitReason(str, Enum):
    WORK_REMAINS = "work_remains"
    DRAIN_UNPROVEN = "drain_unproven"
    COMPLETED = "completed"
    USER_STOP = "user_stop"
    SAFETY_STOP = "safety_stop"
    DEADLINE_REACHED = "deadline_reached"
    TIME_EXHAUSTED = "time_exhausted"
    TOKEN_EXHAUSTED = "token_exhausted"
    USAGE_EXHAUSTED = "usage_exhausted"
    COST_EXHAUSTED = "cost_exhausted"


@dataclass(frozen=True)
class BudgetUsage:
    tokens: int = 0
    usage_units: int = 0
    cost_micros: int = 0

    def __post_init__(self) -> None:
        for name, value in asdict(self).items():
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")

    def __add__(self, other: "BudgetUsage") -> "BudgetUsage":
        return BudgetUsage(
            self.tokens + other.tokens,
            self.usage_units + other.usage_units,
            self.cost_micros + other.cost_micros,
        )


@dataclass(frozen=True)
class BudgetLimits:
    token_limit: Optional[int] = None
    usage_limit: Optional[int] = None
    cost_limit_micros: Optional[int] = None
    time_limit_seconds: Optional[int] = None
    hard_deadline: Optional[datetime] = None

    def __post_init__(self) -> None:
        for name in ("token_limit", "usage_limit", "cost_limit_micros", "time_limit_seconds"):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, int) or isinstance(value, bool) or value < 1):
                raise ValueError(f"{name} must be null or a positive integer")
        if self.hard_deadline is not None:
            _aware(self.hard_deadline, "hard_deadline")


@dataclass(frozen=True)
class StopRequest:
    request_id: str
    kind: StopKind
    requested_at: datetime
    source: str

    @classmethod
    def create(cls, request_id: str, kind: StopKind, requested_at: datetime, source: str) -> "StopRequest":
        if not request_id or not source:
            raise ValueError("stop request identity and source must be non-empty")
        _aware(requested_at, "requested_at")
        return cls(request_id, kind, requested_at, source)


@dataclass(frozen=True)
class QueueState:
    ready: int = 0
    active: int = 0
    stale: int = 0
    undisposed: int = 0
    required_remaining: int = 0

    def __post_init__(self) -> None:
        for name, value in asdict(self).items():
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ValueError(f"queue {name} must be a non-negative integer")

    @property
    def drained(self) -> bool:
        return not any(asdict(self).values())


@dataclass(frozen=True)
class Reservation:
    reservation_id: str
    usage: BudgetUsage


@dataclass(frozen=True)
class LimitDecision:
    action: LimitAction
    reason: LimitReason
    allow_new_dispatch: bool
    cancel_active: bool
    resumable: bool
    evidence: Dict[str, Any]


@dataclass(frozen=True)
class LimitController:
    run_id: str
    started_at: datetime
    started_monotonic: float
    limits: BudgetLimits
    usage: BudgetUsage = BudgetUsage()
    reservations: Tuple[Reservation, ...] = ()
    settled_ids: Tuple[str, ...] = ()
    stop_requests: Tuple[StopRequest, ...] = ()

    def __post_init__(self) -> None:
        if not self.run_id:
            raise ValueError("run_id must be non-empty")
        _aware(self.started_at, "started_at")
        if self.started_monotonic < 0:
            raise ValueError("started_monotonic must be non-negative")
        ids = [item.reservation_id for item in self.reservations]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate reservation ID")
        request_ids = [item.request_id for item in self.stop_requests]
        if len(request_ids) != len(set(request_ids)):
            raise ValueError("duplicate stop request ID")

    @property
    def reserved(self) -> BudgetUsage:
        total = BudgetUsage()
        for item in self.reservations:
            total += item.usage
        return total

    @property
    def available(self) -> BudgetUsage:
        committed = self.usage + self.reserved
        return BudgetUsage(
            _remaining(self.limits.token_limit, committed.tokens),
            _remaining(self.limits.usage_limit, committed.usage_units),
            _remaining(self.limits.cost_limit_micros, committed.cost_micros),
        )

    def record_usage(self, usage: BudgetUsage) -> "LimitController":
        return replace(self, usage=self.usage + usage)

    def reserve(
        self,
        reservation_id: str,
        *,
        tokens: int = 0,
        usage_units: int = 0,
        cost_micros: int = 0,
    ) -> "LimitController":
        requested = BudgetUsage(tokens, usage_units, cost_micros)
        existing = next((item for item in self.reservations if item.reservation_id == reservation_id), None)
        if existing is not None:
            if existing.usage != requested:
                raise ValueError("conflicting reservation")
            return self
        if reservation_id in self.settled_ids:
            raise ValueError("reservation has already settled")
        available = self.available
        if any(
            requested_value > available_value
            for requested_value, available_value in zip(asdict(requested).values(), asdict(available).values())
        ):
            raise ValueError("reservation exceeds available budget")
        return replace(
            self,
            reservations=tuple(
                sorted(self.reservations + (Reservation(reservation_id, requested),), key=lambda x: x.reservation_id)
            ),
        )

    def settle(self, reservation_id: str, actual: BudgetUsage) -> "LimitController":
        if reservation_id in self.settled_ids:
            return self
        if not any(item.reservation_id == reservation_id for item in self.reservations):
            raise ValueError("unknown reservation")
        return replace(
            self,
            usage=self.usage + actual,
            reservations=tuple(item for item in self.reservations if item.reservation_id != reservation_id),
            settled_ids=tuple(sorted(self.settled_ids + (reservation_id,))),
        )

    def with_stop_request(self, request: StopRequest) -> "LimitController":
        existing = next((item for item in self.stop_requests if item.request_id == request.request_id), None)
        if existing is not None:
            if existing != request:
                raise ValueError("conflicting stop request")
            return self
        return replace(
            self,
            stop_requests=tuple(
                sorted(
                    self.stop_requests + (request,),
                    key=lambda item: (item.requested_at, item.request_id),
                )
            ),
        )

    def evaluate(
        self,
        *,
        now: datetime,
        monotonic_now: float,
        queue: QueueState,
        completion_proven: bool = False,
    ) -> LimitDecision:
        _aware(now, "now")
        if monotonic_now < self.started_monotonic:
            raise ValueError("monotonic clock regressed")
        safety = next((item for item in self.stop_requests if item.kind is StopKind.SAFETY), None)
        if safety is not None:
            return self._decision(LimitAction.CANCEL, LimitReason.SAFETY_STOP, now, True, True)
        immediate = next((item for item in self.stop_requests if item.kind is StopKind.IMMEDIATE), None)
        if immediate is not None:
            return self._decision(LimitAction.CANCEL, LimitReason.USER_STOP, now, True, True)
        graceful = next((item for item in self.stop_requests if item.kind is StopKind.GRACEFUL), None)
        if graceful is not None:
            action = LimitAction.DRAIN if queue.active else LimitAction.STOP
            return self._decision(action, LimitReason.USER_STOP, now, False, True)

        hard_reason = self._hard_reason(now, monotonic_now)
        if hard_reason is not None:
            return self._decision(LimitAction.STOP, hard_reason, now, False, True)
        if queue.drained:
            if completion_proven:
                return self._decision(LimitAction.STOP, LimitReason.COMPLETED, now, False, False)
            return self._decision(LimitAction.CONTINUE, LimitReason.DRAIN_UNPROVEN, now, False, False)
        return self._decision(LimitAction.CONTINUE, LimitReason.WORK_REMAINS, now, False, False)

    def _hard_reason(self, now: datetime, monotonic_now: float) -> Optional[LimitReason]:
        if self.limits.hard_deadline is not None and now >= self.limits.hard_deadline:
            return LimitReason.DEADLINE_REACHED
        elapsed = monotonic_now - self.started_monotonic
        if self.limits.time_limit_seconds is not None and elapsed >= self.limits.time_limit_seconds:
            return LimitReason.TIME_EXHAUSTED
        committed = self.usage + self.reserved
        checks = (
            (self.limits.token_limit, committed.tokens, LimitReason.TOKEN_EXHAUSTED),
            (self.limits.usage_limit, committed.usage_units, LimitReason.USAGE_EXHAUSTED),
            (self.limits.cost_limit_micros, committed.cost_micros, LimitReason.COST_EXHAUSTED),
        )
        return next((reason for limit, value, reason in checks if limit is not None and value >= limit), None)

    def _decision(
        self,
        action: LimitAction,
        reason: LimitReason,
        now: datetime,
        cancel: bool,
        resumable: bool,
    ) -> LimitDecision:
        return LimitDecision(
            action=action,
            reason=reason,
            allow_new_dispatch=action is LimitAction.CONTINUE,
            cancel_active=cancel,
            resumable=resumable,
            evidence={
                "run_id": self.run_id,
                "reason": reason.value,
                "action": action.value,
                "observed_at": now.isoformat(),
                "usage": asdict(self.usage),
                "reserved": asdict(self.reserved),
                "stop_request_ids": [item.request_id for item in self.stop_requests],
            },
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "run_id": self.run_id,
            "started_at": self.started_at.isoformat(),
            "started_monotonic": self.started_monotonic,
            "limits": {
                **asdict(self.limits),
                "hard_deadline": (
                    self.limits.hard_deadline.isoformat() if self.limits.hard_deadline is not None else None
                ),
            },
            "usage": asdict(self.usage),
            "reservations": [
                {"reservation_id": item.reservation_id, "usage": asdict(item.usage)} for item in self.reservations
            ],
            "settled_ids": list(self.settled_ids),
            "stop_requests": [
                {
                    "request_id": item.request_id,
                    "kind": item.kind.value,
                    "requested_at": item.requested_at.isoformat(),
                    "source": item.source,
                }
                for item in self.stop_requests
            ],
        }

    @classmethod
    def from_dict(cls, value: Dict[str, Any]) -> "LimitController":
        raw_limits = dict(value["limits"])
        deadline = raw_limits.pop("hard_deadline")
        return cls(
            run_id=value["run_id"],
            started_at=_datetime(value["started_at"]),
            started_monotonic=value["started_monotonic"],
            limits=BudgetLimits(
                **raw_limits,
                hard_deadline=_datetime(deadline) if deadline is not None else None,
            ),
            usage=BudgetUsage(**value["usage"]),
            reservations=tuple(
                Reservation(item["reservation_id"], BudgetUsage(**item["usage"])) for item in value["reservations"]
            ),
            settled_ids=tuple(value["settled_ids"]),
            stop_requests=tuple(
                StopRequest.create(
                    item["request_id"],
                    StopKind(item["kind"]),
                    _datetime(item["requested_at"]),
                    item["source"],
                )
                for item in value["stop_requests"]
            ),
        )


def _remaining(limit: Optional[int], committed: int) -> int:
    return max(0, limit - committed) if limit is not None else 2**63 - 1


def _aware(value: datetime, label: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware")


def _datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    _aware(parsed, "timestamp")
    return parsed
