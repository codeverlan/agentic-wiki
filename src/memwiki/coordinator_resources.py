from __future__ import annotations

import html
import json
import math
import re
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple, Union

from memwiki.coordinator_limits import BudgetLimits, BudgetUsage, LimitController

Number = Union[int, float]
_SECRET_PATTERN = re.compile(r"(?i)(?:api[_-]?key|password|passwd|secret|access[_-]?token|bearer)\s*[:=]\s*\S+")
_RESOURCE_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,127}$")


@dataclass(frozen=True)
class UsageAmount:
    """Measured usage. None means the provider did not report that dimension."""

    tokens: Optional[int] = 0
    elapsed_ms: Optional[int] = 0
    cost_micros: Optional[int] = 0
    resources: Mapping[str, Optional[Number]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in ("tokens", "elapsed_ms", "cost_micros"):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, int) or isinstance(value, bool) or value < 0):
                raise ValueError(f"{name} must be null or a non-negative integer")
        normalized: Dict[str, Optional[Number]] = {}
        for name, value in sorted(self.resources.items()):
            if not isinstance(name, str) or not _RESOURCE_NAME.fullmatch(name):
                raise ValueError("resource names must be stable identifiers")
            if value is not None:
                if not isinstance(value, (int, float)) or isinstance(value, bool) or value < 0:
                    raise ValueError("resource values must be null or non-negative numbers")
                if not math.isfinite(value):
                    raise ValueError("resource values must be finite")
            normalized[name] = value
        object.__setattr__(self, "resources", normalized)

    @classmethod
    def zero(cls) -> "UsageAmount":
        return cls()

    def plus(self, other: "UsageAmount") -> "UsageAmount":
        names = set(self.resources) | set(other.resources)
        return UsageAmount(
            tokens=_sum_int(self.tokens, other.tokens),
            elapsed_ms=_sum_int(self.elapsed_ms, other.elapsed_ms),
            cost_micros=_sum_int(self.cost_micros, other.cost_micros),
            resources={
                name: _sum_known(self.resources.get(name, 0), other.resources.get(name, 0)) for name in sorted(names)
            },
        )

    @property
    def unknown_dimensions(self) -> Tuple[str, ...]:
        names = []
        for name in ("tokens", "elapsed_ms", "cost_micros"):
            if getattr(self, name) is None:
                names.append(name)
        names.extend(f"resource:{name}" for name, value in self.resources.items() if value is None)
        return tuple(sorted(names))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tokens": self.tokens,
            "elapsed_ms": self.elapsed_ms,
            "cost_micros": self.cost_micros,
            "resources": dict(self.resources),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "UsageAmount":
        return cls(
            tokens=value["tokens"],
            elapsed_ms=value["elapsed_ms"],
            cost_micros=value["cost_micros"],
            resources=value["resources"],
        )


@dataclass(frozen=True)
class ResourceScope:
    run_id: str
    slice_id: Optional[str] = None
    attempt_id: Optional[str] = None
    capability_id: Optional[str] = None
    worker_id: Optional[str] = None

    def __post_init__(self) -> None:
        if not self.run_id.strip():
            raise ValueError("run_id must be non-empty")
        for name in ("slice_id", "attempt_id", "capability_id", "worker_id"):
            value = getattr(self, name)
            if value is not None and not value.strip():
                raise ValueError(f"{name} must be null or non-empty")


@dataclass(frozen=True)
class UsageEntry:
    entry_id: str
    scope: ResourceScope
    usage: UsageAmount
    observed_at: datetime
    source: str
    estimation_source: Optional[str] = None


@dataclass(frozen=True)
class ResourceReservation:
    reservation_id: str
    scope: ResourceScope
    amount: UsageAmount
    reserved_at: datetime
    source: str


@dataclass(frozen=True)
class ResourceSummary:
    actual: UsageAmount
    reserved: UsageAmount
    remaining: UsageAmount
    thresholds: Mapping[str, Optional[float]]
    unknown_dimensions: Tuple[str, ...]
    estimation_sources: Tuple[str, ...]
    entry_ids: Tuple[str, ...]
    reservation_ids: Tuple[str, ...]


@dataclass(frozen=True)
class BudgetReconciliation:
    charged: BudgetUsage
    unknown_dimensions: Tuple[str, ...]
    source_entry_ids: Tuple[str, ...]


@dataclass(frozen=True)
class ResourceLedger:
    entries: Tuple[UsageEntry, ...] = ()
    reservations: Tuple[ResourceReservation, ...] = ()
    settled_reservation_ids: Tuple[str, ...] = ()

    @classmethod
    def empty(cls) -> "ResourceLedger":
        return cls()

    @property
    def run_id(self) -> Optional[str]:
        scopes = [item.scope for item in self.entries] + [item.scope for item in self.reservations]
        return scopes[0].run_id if scopes else None

    def record(
        self,
        *,
        entry_id: str,
        scope: ResourceScope,
        usage: UsageAmount,
        observed_at: datetime,
        source: str,
        estimation_source: Optional[str] = None,
    ) -> "ResourceLedger":
        entry = UsageEntry(entry_id, scope, usage, observed_at, source, estimation_source)
        _validate_usage_entry(entry)
        self._validate_run(scope)
        existing = next((item for item in self.entries if item.entry_id == entry_id), None)
        if existing is not None:
            if existing != entry:
                raise ValueError("conflicting usage entry")
            return self
        return replace(
            self,
            entries=tuple(sorted(self.entries + (entry,), key=lambda item: (item.observed_at, item.entry_id))),
        )

    def reserve(
        self,
        *,
        reservation_id: str,
        scope: ResourceScope,
        amount: UsageAmount,
        reserved_at: datetime,
        source: str,
    ) -> "ResourceLedger":
        reservation = ResourceReservation(reservation_id, scope, amount, reserved_at, source)
        _validate_reservation(reservation)
        self._validate_run(scope)
        existing = next((item for item in self.reservations if item.reservation_id == reservation_id), None)
        if existing is not None:
            if existing != reservation:
                raise ValueError("conflicting reservation")
            return self
        if reservation_id in self.settled_reservation_ids:
            raise ValueError("reservation has already settled")
        return replace(
            self,
            reservations=tuple(
                sorted(
                    self.reservations + (reservation,),
                    key=lambda item: (item.reserved_at, item.reservation_id),
                )
            ),
        )

    def settle(
        self,
        *,
        reservation_id: str,
        entry_id: str,
        actual: UsageAmount,
        observed_at: datetime,
        source: str,
        estimation_source: Optional[str] = None,
    ) -> "ResourceLedger":
        reservation = next((item for item in self.reservations if item.reservation_id == reservation_id), None)
        if reservation is None:
            if reservation_id not in self.settled_reservation_ids:
                raise ValueError("unknown reservation")
            existing = next((item for item in self.entries if item.entry_id == entry_id), None)
            candidate = UsageEntry(
                entry_id, self._settled_scope(entry_id), actual, observed_at, source, estimation_source
            )
            if existing != candidate:
                raise ValueError("conflicting settlement")
            return self
        updated = self.record(
            entry_id=entry_id,
            scope=reservation.scope,
            usage=actual,
            observed_at=observed_at,
            source=source,
            estimation_source=estimation_source,
        )
        return replace(
            updated,
            reservations=tuple(item for item in updated.reservations if item.reservation_id != reservation_id),
            settled_reservation_ids=tuple(sorted(updated.settled_reservation_ids + (reservation_id,))),
        )

    def _settled_scope(self, entry_id: str) -> ResourceScope:
        existing = next((item for item in self.entries if item.entry_id == entry_id), None)
        if existing is None:
            raise ValueError("conflicting settlement")
        return existing.scope

    def _validate_run(self, scope: ResourceScope) -> None:
        if self.run_id is not None and scope.run_id != self.run_id:
            raise ValueError("resource ledger is confined to a single run")

    def summary(
        self,
        *,
        run_id: Optional[str] = None,
        slice_id: Optional[str] = None,
        attempt_id: Optional[str] = None,
        capability_id: Optional[str] = None,
        worker_id: Optional[str] = None,
        limits: Optional[BudgetLimits] = None,
    ) -> ResourceSummary:
        filters = {
            "run_id": run_id,
            "slice_id": slice_id,
            "attempt_id": attempt_id,
            "capability_id": capability_id,
            "worker_id": worker_id,
        }
        entries = tuple(item for item in self.entries if _matches(item.scope, filters))
        reservations = tuple(item for item in self.reservations if _matches(item.scope, filters))
        actual = _aggregate(tuple(item.usage for item in entries))
        reserved = _aggregate(tuple(item.amount for item in reservations))
        remaining, thresholds = _budget_view(actual, reserved, limits)
        return ResourceSummary(
            actual=actual,
            reserved=reserved,
            remaining=remaining,
            thresholds=thresholds,
            unknown_dimensions=actual.unknown_dimensions,
            estimation_sources=tuple(
                sorted({item.estimation_source for item in entries if item.estimation_source is not None})
            ),
            entry_ids=tuple(item.entry_id for item in entries),
            reservation_ids=tuple(item.reservation_id for item in reservations),
        )

    def reconcile_budget(self, controller: LimitController) -> Tuple[LimitController, BudgetReconciliation]:
        if self.run_id is not None and self.run_id != controller.run_id:
            raise ValueError("ledger and budget controller run IDs differ")
        pending = tuple(
            item for item in self.entries if _reconciliation_id(item.entry_id) not in controller.settled_ids
        )
        charged = BudgetUsage(
            tokens=sum(item.usage.tokens or 0 for item in pending),
            cost_micros=sum(item.usage.cost_micros or 0 for item in pending),
        )
        markers = tuple(_reconciliation_id(item.entry_id) for item in pending)
        reconciled = replace(
            controller,
            usage=controller.usage + charged,
            settled_ids=tuple(sorted(controller.settled_ids + markers)),
        )
        unknown = {
            name for item in pending for name in item.usage.unknown_dimensions if name in {"tokens", "cost_micros"}
        }
        return reconciled, BudgetReconciliation(
            charged=charged,
            unknown_dimensions=tuple(sorted(unknown)),
            source_entry_ids=tuple(item.entry_id for item in pending),
        )

    def html_metadata(self, *, limits: Optional[BudgetLimits] = None) -> str:
        summary = self.summary(limits=limits)
        payload = {
            "@context": "https://schema.org",
            "@type": "Dataset",
            "name": "Autonomous coordinator resource ledger",
            "run_id": self.run_id,
            "summary": _summary_dict(summary),
            "entries": [self._entry_dict(item) for item in self.entries],
            "reservations": [self._reservation_dict(item) for item in self.reservations],
        }
        return html.escape(
            json.dumps(payload, ensure_ascii=True, allow_nan=False, sort_keys=True, separators=(",", ":")),
            quote=True,
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "entries": [self._entry_dict(item) for item in self.entries],
            "reservations": [self._reservation_dict(item) for item in self.reservations],
            "settled_reservation_ids": list(self.settled_reservation_ids),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ResourceLedger":
        ledger = cls.empty()
        for raw in value["entries"]:
            ledger = ledger.record(
                entry_id=raw["entry_id"],
                scope=ResourceScope(**raw["scope"]),
                usage=UsageAmount.from_dict(raw["usage"]),
                observed_at=_datetime(raw["observed_at"]),
                source=raw["source"],
                estimation_source=raw["estimation_source"],
            )
        for raw in value["reservations"]:
            ledger = ledger.reserve(
                reservation_id=raw["reservation_id"],
                scope=ResourceScope(**raw["scope"]),
                amount=UsageAmount.from_dict(raw["amount"]),
                reserved_at=_datetime(raw["reserved_at"]),
                source=raw["source"],
            )
        settled = tuple(value["settled_reservation_ids"])
        active_ids = {item.reservation_id for item in ledger.reservations}
        if active_ids.intersection(settled):
            raise ValueError("reservation cannot be active and settled")
        return replace(ledger, settled_reservation_ids=settled)

    @staticmethod
    def _entry_dict(item: UsageEntry) -> Dict[str, Any]:
        return {
            "entry_id": item.entry_id,
            "scope": asdict(item.scope),
            "usage": item.usage.to_dict(),
            "observed_at": item.observed_at.isoformat(),
            "source": item.source,
            "estimation_source": item.estimation_source,
        }

    @staticmethod
    def _reservation_dict(item: ResourceReservation) -> Dict[str, Any]:
        return {
            "reservation_id": item.reservation_id,
            "scope": asdict(item.scope),
            "amount": item.amount.to_dict(),
            "reserved_at": item.reserved_at.isoformat(),
            "source": item.source,
        }


def _aggregate(values: Sequence[UsageAmount]) -> UsageAmount:
    total = UsageAmount.zero()
    for value in values:
        total = total.plus(value)
    return total


def _matches(scope: ResourceScope, filters: Mapping[str, Optional[str]]) -> bool:
    return all(expected is None or getattr(scope, name) == expected for name, expected in filters.items())


def _budget_view(
    actual: UsageAmount, reserved: UsageAmount, limits: Optional[BudgetLimits]
) -> Tuple[UsageAmount, Mapping[str, Optional[float]]]:
    committed = actual.plus(reserved)
    token_limit = limits.token_limit if limits is not None else None
    cost_limit = limits.cost_limit_micros if limits is not None else None
    remaining = UsageAmount(
        tokens=_remaining(token_limit, committed.tokens),
        elapsed_ms=_remaining(
            limits.time_limit_seconds * 1000 if limits is not None and limits.time_limit_seconds is not None else None,
            committed.elapsed_ms,
        ),
        cost_micros=_remaining(cost_limit, committed.cost_micros),
        resources={},
    )
    return remaining, {
        "tokens": _threshold(token_limit, committed.tokens),
        "elapsed_ms": _threshold(
            limits.time_limit_seconds * 1000 if limits is not None and limits.time_limit_seconds is not None else None,
            committed.elapsed_ms,
        ),
        "cost_micros": _threshold(cost_limit, committed.cost_micros),
    }


def _summary_dict(summary: ResourceSummary) -> Dict[str, Any]:
    return {
        "actual": summary.actual.to_dict(),
        "reserved": summary.reserved.to_dict(),
        "remaining": summary.remaining.to_dict(),
        "thresholds": dict(summary.thresholds),
        "unknown_dimensions": list(summary.unknown_dimensions),
        "estimation_sources": list(summary.estimation_sources),
        "entry_ids": list(summary.entry_ids),
        "reservation_ids": list(summary.reservation_ids),
    }


def _sum_known(left: Optional[Number], right: Optional[Number]) -> Optional[Number]:
    if left is None or right is None:
        return None
    return left + right


def _sum_int(left: Optional[int], right: Optional[int]) -> Optional[int]:
    if left is None or right is None:
        return None
    return left + right


def _remaining(limit: Optional[int], committed: Optional[Number]) -> Optional[int]:
    if limit is None or committed is None:
        return None
    return max(0, limit - int(committed))


def _threshold(limit: Optional[int], committed: Optional[Number]) -> Optional[float]:
    if limit is None or committed is None:
        return None
    return float(committed) / limit


def _validate_usage_entry(entry: UsageEntry) -> None:
    _text(entry.entry_id, "entry_id")
    _text(entry.source, "source")
    _aware(entry.observed_at, "observed_at")
    if entry.estimation_source is not None:
        _text(entry.estimation_source, "estimation_source")


def _validate_reservation(reservation: ResourceReservation) -> None:
    _text(reservation.reservation_id, "reservation_id")
    _text(reservation.source, "source")
    _aware(reservation.reserved_at, "reserved_at")


def _text(value: str, label: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be non-empty")
    if _SECRET_PATTERN.search(value):
        raise ValueError(f"{label} contains secret-like material")


def _aware(value: datetime, label: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware")


def _datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    _aware(parsed, "timestamp")
    return parsed


def _reconciliation_id(entry_id: str) -> str:
    return f"resource-entry:{entry_id}"
