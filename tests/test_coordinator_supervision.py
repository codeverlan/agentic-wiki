from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from memwiki.coordinator_supervision import (
    SupervisionCategory,
    SupervisionQueue,
    SupervisionStatus,
)

NOW = datetime(2026, 7, 11, 12, 0, tzinfo=timezone.utc)


def _open(
    queue: SupervisionQueue,
    item_id: str,
    category: SupervisionCategory,
    *,
    minute: int = 0,
    expires_at: datetime | None = None,
) -> None:
    queue.open(
        item_id=item_id,
        category=category,
        summary=f"Decision for {item_id}",
        choices=("approve", "decline"),
        evidence_ids=(f"evidence-{item_id}",),
        affected_slice_ids=(f"slice-{item_id}",),
        resumable=True,
        created_at=NOW + timedelta(minutes=minute),
        expires_at=expires_at,
    )


def test_ledger_is_chronological_while_pending_view_uses_mandatory_priority() -> None:
    queue = SupervisionQueue.empty()
    _open(queue, "advisory", SupervisionCategory.ADVISORY, minute=0)
    _open(queue, "auth", SupervisionCategory.AUTHORIZATION, minute=1)
    _open(queue, "financial", SupervisionCategory.FINANCIAL, minute=2)
    _open(queue, "safety", SupervisionCategory.SAFETY, minute=3)
    _open(queue, "legal", SupervisionCategory.LEGAL, minute=4)

    assert [item.item_id for item in queue.chronological()] == [
        "advisory",
        "auth",
        "financial",
        "safety",
        "legal",
    ]
    assert [item.item_id for item in queue.pending_for_presentation(NOW + timedelta(minutes=5))] == [
        "safety",
        "legal",
        "financial",
        "auth",
        "advisory",
    ]


@pytest.mark.parametrize(
    "category",
    [
        SupervisionCategory.SAFETY,
        SupervisionCategory.LEGAL,
        SupervisionCategory.IRREVERSIBLE,
        SupervisionCategory.DESTRUCTIVE,
        SupervisionCategory.FINANCIAL,
        SupervisionCategory.EXTERNAL_PUBLICATION,
        SupervisionCategory.AUTHORIZATION,
    ],
)
def test_mandatory_categories_require_supervision(category: SupervisionCategory) -> None:
    queue = SupervisionQueue.empty()
    _open(queue, "request", category)
    assert queue.get("request").mandatory is True


def test_other_decisions_remain_advisory() -> None:
    queue = SupervisionQueue.empty()
    _open(queue, "request", SupervisionCategory.ADVISORY)
    assert queue.get("request").mandatory is False


def test_duplicate_ids_and_nonchronological_mutations_are_rejected() -> None:
    queue = SupervisionQueue.empty()
    _open(queue, "request", SupervisionCategory.SAFETY)
    with pytest.raises(ValueError, match="duplicate supervision item"):
        _open(queue, "request", SupervisionCategory.LEGAL, minute=1)
    with pytest.raises(ValueError, match="chronological"):
        queue.defer(
            "request",
            reason="Wait for owner",
            resume_after=NOW + timedelta(hours=1),
            recorded_at=NOW - timedelta(seconds=1),
        )


def test_resolve_and_defer_append_immutable_history() -> None:
    queue = SupervisionQueue.empty()
    _open(queue, "deferred", SupervisionCategory.LEGAL)
    original = queue.get("deferred")
    queue.defer(
        "deferred",
        reason="Counsel unavailable",
        resume_after=NOW + timedelta(hours=2),
        recorded_at=NOW + timedelta(minutes=1),
    )
    assert original.status is SupervisionStatus.OPEN
    assert queue.get("deferred").status is SupervisionStatus.DEFERRED
    assert queue.pending_for_presentation(NOW + timedelta(hours=1)) == ()
    assert [item.item_id for item in queue.pending_for_presentation(NOW + timedelta(hours=3))] == [
        "deferred"
    ]

    queue.resolve(
        "deferred",
        disposition="approved",
        evidence_ids=("user-decision",),
        recorded_at=NOW + timedelta(hours=3, minutes=1),
    )
    record = queue.get("deferred")
    assert record.status is SupervisionStatus.RESOLVED
    assert [event.action for event in record.history] == ["opened", "deferred", "resolved"]
    with pytest.raises(ValueError, match="open or deferred"):
        queue.resolve(
            "deferred",
            disposition="approved again",
            evidence_ids=(),
            recorded_at=NOW + timedelta(hours=4),
        )


def test_expired_items_are_visible_and_cannot_be_resolved_without_reopening() -> None:
    queue = SupervisionQueue.empty()
    _open(
        queue,
        "expiring",
        SupervisionCategory.EXTERNAL_PUBLICATION,
        expires_at=NOW + timedelta(minutes=5),
    )
    pending = queue.pending_for_presentation(NOW + timedelta(minutes=6))
    assert pending[0].expired is True
    with pytest.raises(ValueError, match="expired"):
        queue.resolve(
            "expiring",
            disposition="publish",
            evidence_ids=("late",),
            recorded_at=NOW + timedelta(minutes=6),
        )


def test_user_return_records_priority_without_interrupting_active_work() -> None:
    queue = SupervisionQueue.empty()
    _open(queue, "auth", SupervisionCategory.AUTHORIZATION)
    event = queue.record_user_return(
        recorded_at=NOW + timedelta(minutes=2),
        active_slice_ids=("safe-active",),
    )

    assert event.action == "user_returned"
    assert event.active_slice_ids == ("safe-active",)
    assert event.cancel_active_work is False
    assert queue.presentation_due is True
    queue.mark_presented(recorded_at=NOW + timedelta(minutes=3), item_ids=("auth",))
    assert queue.presentation_due is False


def test_round_trip_is_stable_and_html_is_semantic_secret_free() -> None:
    queue = SupervisionQueue.empty()
    _open(queue, "publish", SupervisionCategory.EXTERNAL_PUBLICATION)
    queue.record_user_return(recorded_at=NOW + timedelta(minutes=1), active_slice_ids=())
    restored = SupervisionQueue.from_dict(queue.to_dict())
    assert restored.to_dict() == queue.to_dict()

    html = restored.render_html(generated_at=NOW + timedelta(minutes=2))
    assert '<script type="application/ld+json">' in html
    assert "Supervision queue" in html
    assert "publish" in html
    assert "evidence-publish" in html
    assert "<table" in html

    with pytest.raises(ValueError, match="secret"):
        queue.open(
            item_id="secret",
            category=SupervisionCategory.AUTHORIZATION,
            summary="Use api_key=super-secret-value",
            choices=("approve",),
            evidence_ids=("ev",),
            affected_slice_ids=("slice",),
            resumable=True,
            created_at=NOW + timedelta(minutes=3),
        )


def test_invalid_scope_choices_and_timestamps_are_rejected() -> None:
    queue = SupervisionQueue.empty()
    with pytest.raises(ValueError, match="choice"):
        queue.open(
            item_id="bad",
            category=SupervisionCategory.SAFETY,
            summary="Need a decision",
            choices=(),
            evidence_ids=("ev",),
            affected_slice_ids=("slice",),
            resumable=False,
            created_at=NOW,
        )
    with pytest.raises(ValueError, match="timezone"):
        queue.open(
            item_id="bad-time",
            category=SupervisionCategory.SAFETY,
            summary="Need a decision",
            choices=("stop",),
            evidence_ids=("ev",),
            affected_slice_ids=(),
            resumable=False,
            created_at=NOW.replace(tzinfo=None),
        )
