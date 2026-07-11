from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from memwiki.coordinator_limits import BudgetLimits, BudgetUsage, LimitController
from memwiki.coordinator_resources import ResourceLedger, ResourceScope, UsageAmount

NOW = datetime(2026, 7, 11, 16, 0, tzinfo=timezone.utc)


def scope(**overrides: str) -> ResourceScope:
    values = {
        "run_id": "run-1",
        "slice_id": "AC-035",
        "attempt_id": "attempt-1",
        "capability_id": "model:test",
    }
    values.update(overrides)
    return ResourceScope(**values)


def test_append_and_aggregate_every_attribution_level() -> None:
    ledger = ResourceLedger.empty().record(
        entry_id="usage-1",
        scope=scope(),
        usage=UsageAmount(tokens=120, elapsed_ms=2500, cost_micros=90, resources={"requests": 2}),
        observed_at=NOW,
        source="provider_receipt",
    )
    ledger = ledger.record(
        entry_id="usage-2",
        scope=scope(attempt_id="attempt-2", capability_id="validator"),
        usage=UsageAmount(tokens=30, elapsed_ms=500, cost_micros=10, resources={"requests": 1}),
        observed_at=NOW + timedelta(seconds=1),
        source="local_measurement",
    )

    assert ledger.summary().actual == UsageAmount(
        tokens=150, elapsed_ms=3000, cost_micros=100, resources={"requests": 3}
    )
    assert ledger.summary(attempt_id="attempt-1").actual.tokens == 120
    assert ledger.summary(capability_id="validator").actual.elapsed_ms == 500
    assert ledger.summary(slice_id="missing").actual == UsageAmount.zero()


def test_unknown_values_remain_explicit_and_are_never_estimated() -> None:
    ledger = ResourceLedger.empty().record(
        entry_id="unknown-model",
        scope=scope(),
        usage=UsageAmount(tokens=None, elapsed_ms=42, cost_micros=None, resources={"gpu_seconds": None}),
        observed_at=NOW,
        source="adapter_without_usage_reporting",
        estimation_source=None,
    )
    summary = ledger.summary()

    assert summary.actual.tokens is None
    assert summary.actual.cost_micros is None
    assert summary.actual.resources["gpu_seconds"] is None
    assert set(summary.unknown_dimensions) == {"cost_micros", "resource:gpu_seconds", "tokens"}
    assert summary.actual.elapsed_ms == 42


def test_reservations_are_atomic_idempotent_and_restart_safe() -> None:
    ledger = ResourceLedger.empty().reserve(
        reservation_id="reservation-1",
        scope=scope(),
        amount=UsageAmount(tokens=100, cost_micros=50),
        reserved_at=NOW,
        source="scheduler",
    )
    assert (
        ledger.reserve(
            reservation_id="reservation-1",
            scope=scope(),
            amount=UsageAmount(tokens=100, cost_micros=50),
            reserved_at=NOW,
            source="scheduler",
        )
        == ledger
    )
    with pytest.raises(ValueError, match="conflicting reservation"):
        ledger.reserve(
            reservation_id="reservation-1",
            scope=scope(),
            amount=UsageAmount(tokens=101),
            reserved_at=NOW,
            source="scheduler",
        )

    restored = ResourceLedger.from_dict(ledger.to_dict())
    assert restored.to_dict() == ledger.to_dict()
    assert restored.summary().reserved.tokens == 100


def test_settlement_records_actual_once_and_releases_reservation() -> None:
    ledger = ResourceLedger.empty().reserve(
        reservation_id="r1",
        scope=scope(),
        amount=UsageAmount(tokens=100, cost_micros=50),
        reserved_at=NOW,
        source="scheduler",
    )
    settled = ledger.settle(
        reservation_id="r1",
        entry_id="actual-r1",
        actual=UsageAmount(tokens=130, cost_micros=None),
        observed_at=NOW + timedelta(seconds=1),
        source="worker_report",
    )

    assert settled.summary().reserved.tokens == 0
    assert settled.summary().actual.tokens == 130
    assert settled.summary().actual.cost_micros is None
    assert (
        settled.settle(
            reservation_id="r1",
            entry_id="actual-r1",
            actual=UsageAmount(tokens=130, cost_micros=None),
            observed_at=NOW + timedelta(seconds=1),
            source="worker_report",
        )
        == settled
    )
    with pytest.raises(ValueError, match="conflicting settlement"):
        settled.settle(
            reservation_id="r1",
            entry_id="actual-r1",
            actual=UsageAmount(tokens=99),
            observed_at=NOW + timedelta(seconds=1),
            source="worker_report",
        )


def test_budget_reconciliation_charges_only_known_actual_values() -> None:
    controller = LimitController(
        run_id="run-1",
        started_at=NOW,
        started_monotonic=10.0,
        limits=BudgetLimits(token_limit=1000, cost_limit_micros=500),
    )
    ledger = ResourceLedger.empty().record(
        entry_id="usage-1",
        scope=scope(),
        usage=UsageAmount(tokens=None, elapsed_ms=20, cost_micros=75),
        observed_at=NOW,
        source="partial_receipt",
    )
    reconciled, evidence = ledger.reconcile_budget(controller)

    assert reconciled.usage == BudgetUsage(tokens=0, cost_micros=75)
    assert evidence.charged == BudgetUsage(cost_micros=75)
    assert evidence.unknown_dimensions == ("tokens",)
    assert evidence.source_entry_ids == ("usage-1",)
    again, same_evidence = ledger.reconcile_budget(reconciled)
    assert again == reconciled
    assert same_evidence.charged == BudgetUsage()


def test_deterministic_summary_thresholds_remaining_and_html_safe_metadata() -> None:
    ledger = ResourceLedger.empty().record(
        entry_id="usage-<script>",
        scope=scope(capability_id="model<&>"),
        usage=UsageAmount(tokens=80, elapsed_ms=1000, cost_micros=20),
        observed_at=NOW,
        source="receipt<&>",
        estimation_source="provider<&>",
    )
    summary = ledger.summary(limits=BudgetLimits(token_limit=100, cost_limit_micros=100))
    metadata = ledger.html_metadata(limits=BudgetLimits(token_limit=100))

    assert summary.remaining.tokens == 20
    assert summary.thresholds["tokens"] == pytest.approx(0.8)
    assert summary.estimation_sources == ("provider<&>",)
    assert "<script>" not in metadata
    assert "&lt;script&gt;" in metadata
    assert "model&lt;&amp;&gt;" in metadata


def test_rejects_invalid_values_cross_run_scope_and_secret_metadata() -> None:
    with pytest.raises(ValueError, match="non-negative"):
        UsageAmount(tokens=-1)
    with pytest.raises(ValueError, match="finite"):
        UsageAmount(resources={"cpu": float("nan")})
    with pytest.raises(ValueError, match="single run"):
        ResourceLedger.empty().record(
            entry_id="one",
            scope=scope(run_id="run-2"),
            usage=UsageAmount(tokens=1),
            observed_at=NOW,
            source="test",
        ).record(
            entry_id="two",
            scope=scope(run_id="run-3"),
            usage=UsageAmount(tokens=1),
            observed_at=NOW,
            source="test",
        )
    with pytest.raises(ValueError, match="secret-like"):
        ResourceLedger.empty().record(
            entry_id="one",
            scope=scope(),
            usage=UsageAmount(tokens=1),
            observed_at=NOW,
            source="api_key=super-secret",
        )
