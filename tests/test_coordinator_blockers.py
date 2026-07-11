from __future__ import annotations

from datetime import datetime, timezone

import pytest

from memwiki.coordinator_blockers import (
    BlockerLedger,
    BlockerScope,
    BlockerStatus,
    BlockerType,
)

NOW = datetime(2026, 7, 11, 12, 0, tzinfo=timezone.utc)


def test_slice_blocker_impacts_only_slice_and_descendants() -> None:
    ledger = BlockerLedger.empty()
    blocker = ledger.open(
        blocker_id="blk-a",
        blocker_type=BlockerType.USER_INPUT,
        cause="Need an irreversible product decision",
        source_slice_id="A",
        scope=BlockerScope.SLICE,
        authority_need="user",
        evidence_ids=("ev-1",),
        recorded_at=NOW,
    )

    impact = ledger.evaluate_impact(
        slice_dependencies={"A": (), "B": ("A",), "C": (), "D": ("B",)},
        schedulable_slice_ids=("A", "B", "C", "D"),
    )

    assert blocker.notify_user is False
    assert impact.blocked_slice_ids == ("A", "B", "D")
    assert impact.schedulable_slice_ids == ("C",)


def test_missing_expected_capability_stops_slice_and_requests_chat_notification() -> None:
    blocker = BlockerLedger.empty().open(
        blocker_id="blk-capability",
        blocker_type=BlockerType.MISSING_EXPECTED_CAPABILITY,
        cause="Configured browser controller is unavailable",
        source_slice_id="browser-test",
        scope=BlockerScope.SLICE,
        authority_need="capability-restoration",
        evidence_ids=("probe-1",),
        recorded_at=NOW,
        notification_metadata={"channel": "chat", "summary": "Browser controller unavailable"},
    )

    assert blocker.notify_user is True
    assert blocker.notification_metadata["channel"] == "chat"


def test_nonexistent_helpful_resource_is_advisory_and_does_not_block() -> None:
    ledger = BlockerLedger.empty()
    ledger.open(
        blocker_id="adv-1",
        blocker_type=BlockerType.FUTURE_RESOURCE_ADVISORY,
        cause="A specialized claims simulator would improve coverage",
        source_slice_id="claims",
        scope=BlockerScope.SLICE,
        authority_need=None,
        evidence_ids=("research-1",),
        recorded_at=NOW,
    )

    impact = ledger.evaluate_impact(
        slice_dependencies={"claims": (), "portal": ()},
        schedulable_slice_ids=("claims", "portal"),
    )

    assert impact.blocked_slice_ids == ()
    assert impact.schedulable_slice_ids == ("claims", "portal")
    assert impact.advisory_ids == ("adv-1",)


def test_global_policy_blocker_explicitly_blocks_all_work() -> None:
    ledger = BlockerLedger.empty()
    ledger.open(
        blocker_id="blk-global",
        blocker_type=BlockerType.POLICY_SUPERVISION,
        cause="Repository integrity cannot be established",
        source_slice_id=None,
        scope=BlockerScope.GLOBAL,
        authority_need="user-supervision",
        evidence_ids=("integrity-1",),
        recorded_at=NOW,
    )

    impact = ledger.evaluate_impact(
        slice_dependencies={"A": (), "B": ()},
        schedulable_slice_ids=("A", "B"),
    )
    assert impact.blocked_slice_ids == ("A", "B")
    assert impact.schedulable_slice_ids == ()


def test_update_resolve_reopen_and_supersede_preserve_chronological_evidence() -> None:
    ledger = BlockerLedger.empty()
    ledger.open(
        blocker_id="blk-1",
        blocker_type=BlockerType.TECHNICAL_RETRYABLE,
        cause="Dependency registry timeout",
        source_slice_id="A",
        scope=BlockerScope.SLICE,
        authority_need=None,
        evidence_ids=("ev-1",),
        recorded_at=NOW,
    )
    ledger.update(
        "blk-1",
        cause="Registry still unavailable",
        evidence_ids=("ev-2",),
        recorded_at=NOW.replace(minute=1),
    )
    ledger.resolve("blk-1", resolution="Registry recovered", evidence_ids=("ev-3",), recorded_at=NOW.replace(minute=2))
    ledger.reopen("blk-1", cause="Timeout returned", evidence_ids=("ev-4",), recorded_at=NOW.replace(minute=3))
    ledger.open(
        blocker_id="blk-2",
        blocker_type=BlockerType.TECHNICAL_RETRYABLE,
        cause="Root network failure",
        source_slice_id="A",
        scope=BlockerScope.SLICE,
        authority_need=None,
        evidence_ids=("ev-5",),
        recorded_at=NOW.replace(minute=4),
    )
    ledger.supersede("blk-1", superseded_by="blk-2", evidence_ids=("ev-6",), recorded_at=NOW.replace(minute=5))

    record = ledger.get("blk-1")
    assert record.status is BlockerStatus.SUPERSEDED
    assert record.superseded_by == "blk-2"
    assert [event.action for event in record.history] == ["opened", "updated", "resolved", "reopened", "superseded"]
    assert [event.evidence_ids for event in record.history] == [
        ("ev-1",), ("ev-2",), ("ev-3",), ("ev-4",), ("ev-6",)
    ]


@pytest.mark.parametrize("blocker_type", list(BlockerType))
def test_all_typed_categories_round_trip(blocker_type: BlockerType) -> None:
    scope = BlockerScope.ADVISORY if blocker_type is BlockerType.FUTURE_RESOURCE_ADVISORY else BlockerScope.SLICE
    ledger = BlockerLedger.empty()
    ledger.open(
        blocker_id=f"blk-{blocker_type.value}",
        blocker_type=blocker_type,
        cause="typed cause",
        source_slice_id="A",
        scope=scope,
        authority_need=None,
        evidence_ids=("ev",),
        recorded_at=NOW,
        notification_metadata={"channel": "chat"} if blocker_type is BlockerType.MISSING_EXPECTED_CAPABILITY else None,
    )

    restored = BlockerLedger.from_dict(ledger.to_dict())
    assert restored.to_dict() == ledger.to_dict()


def test_rejects_invalid_lifecycle_and_graph_inputs() -> None:
    ledger = BlockerLedger.empty()
    with pytest.raises(ValueError, match="source slice"):
        ledger.open(
            blocker_id="bad",
            blocker_type=BlockerType.USER_INPUT,
            cause="cause",
            source_slice_id=None,
            scope=BlockerScope.SLICE,
            authority_need="user",
            evidence_ids=("ev",),
            recorded_at=NOW,
        )
    with pytest.raises(ValueError, match="notification metadata"):
        ledger.open(
            blocker_id="bad-cap",
            blocker_type=BlockerType.MISSING_EXPECTED_CAPABILITY,
            cause="cause",
            source_slice_id="A",
            scope=BlockerScope.SLICE,
            authority_need=None,
            evidence_ids=("ev",),
            recorded_at=NOW,
        )
    with pytest.raises(ValueError, match="unknown dependency"):
        ledger.evaluate_impact({"A": ("missing",)}, ("A",))
