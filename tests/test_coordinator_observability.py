from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from memwiki.coordinator_observability import (
    CorrelationIds,
    EvidenceIndex,
    ObservabilityEvent,
    SecuritySignal,
)

NOW = datetime(2026, 7, 11, 17, 0, tzinfo=timezone.utc)


def event(**overrides: object) -> ObservabilityEvent:
    values: dict[str, object] = {
        "event_id": "evt-1",
        "occurred_at": NOW,
        "level": "info",
        "category": "validation",
        "message": "Validation receipt accepted",
        "correlation": CorrelationIds(
            run_id="run-1",
            slice_id="AC-036",
            attempt_id="attempt-1",
            assignment_id="assignment-1",
            lease_id="lease-1",
            command_id="command-1",
            capability_id="capability-1",
            validation_id="validation-1",
            integration_id="integration-1",
            resource_id="resource-1",
        ),
        "evidence_ids": ("receipt:validation-1",),
        "attributes": {"result": "passed", "count": 3},
    }
    values.update(overrides)
    return ObservabilityEvent(**values)  # type: ignore[arg-type]


def test_records_all_correlation_ids_and_round_trips() -> None:
    index = EvidenceIndex.empty().append(event())
    restored = EvidenceIndex.from_dict(index.to_dict())

    assert restored == index
    assert restored.events[0].correlation.validation_id == "validation-1"
    assert restored.evidence_to_events == {"receipt:validation-1": ("evt-1",)}


def test_append_is_idempotent_but_conflicting_or_reordered_history_is_rejected() -> None:
    first = event()
    index = EvidenceIndex.empty().append(first)
    assert index.append(first) == index
    with pytest.raises(ValueError, match="conflicting event"):
        index.append(event(message="different"))
    with pytest.raises(ValueError, match="chronological"):
        index.append(event(event_id="evt-0", occurred_at=NOW - timedelta(seconds=1)))


def test_query_is_deterministic_and_filters_by_level_category_correlation_and_evidence() -> None:
    index = EvidenceIndex.empty().append(event())
    index = index.append(
        event(
            event_id="evt-2",
            occurred_at=NOW + timedelta(seconds=1),
            level="warning",
            category="security",
            correlation=CorrelationIds(run_id="run-1", slice_id="AC-037"),
            evidence_ids=("incident:1",),
        )
    )

    assert [item.event_id for item in index.query(run_id="run-1")] == ["evt-1", "evt-2"]
    assert [item.event_id for item in index.query(slice_id="AC-036")] == ["evt-1"]
    assert [item.event_id for item in index.query(levels=("warning",))] == ["evt-2"]
    assert [item.event_id for item in index.query(categories=("security",))] == ["evt-2"]
    assert [item.event_id for item in index.query(evidence_id="receipt:validation-1")] == ["evt-1"]


@pytest.mark.parametrize(
    "field,value",
    [
        ("message", "password=hunter2"),
        ("attributes", {"access_token": "abc"}),
        ("attributes", {"note": "patient John Doe has MRN 123456"}),
    ],
)
def test_rejects_secrets_and_phi_in_every_exported_payload(field: str, value: object) -> None:
    with pytest.raises(ValueError, match="sensitive data"):
        event(**{field: value})


def test_security_hooks_create_correlated_signals_without_mutating_events() -> None:
    def failed_validation(item: ObservabilityEvent) -> SecuritySignal | None:
        if item.attributes.get("result") == "failed":
            return SecuritySignal(
                signal_id="signal-1",
                event_id=item.event_id,
                kind="repeated_validation_failure",
                severity="warning",
                summary="Validation failure requires review",
            )
        return None

    index = EvidenceIndex.empty(hooks=(failed_validation,)).append(
        event(attributes={"result": "failed"})
    )
    assert index.signals == (
        SecuritySignal(
            signal_id="signal-1",
            event_id="evt-1",
            kind="repeated_validation_failure",
            severity="warning",
            summary="Validation failure requires review",
        ),
    )
    assert index.events[0].attributes == {"result": "failed"}


def test_rejects_invalid_signal_and_duplicate_signal_identity() -> None:
    with pytest.raises(ValueError, match="event_id"):
        SecuritySignal("signal-1", "", "kind", "warning", "summary")

    def duplicate(_: ObservabilityEvent) -> SecuritySignal:
        return SecuritySignal("signal-1", "evt-1", "kind", "warning", "summary")

    index = EvidenceIndex.empty(hooks=(duplicate, duplicate))
    with pytest.raises(ValueError, match="duplicate security signal"):
        index.append(event())


def test_semantic_html_is_safe_deterministic_and_contains_json_ld() -> None:
    index = EvidenceIndex.empty().append(
        event(message="Validation <complete>", attributes={"path": "src/<safe>.py"})
    )
    rendered = index.to_html(title="Run <one>")

    assert rendered == index.to_html(title="Run <one>")
    assert '<script type="application/ld+json">' in rendered
    assert "Run &lt;one&gt;" in rendered
    assert "Validation &lt;complete&gt;" in rendered
    assert "src/&lt;safe&gt;.py" in rendered
    assert "src/<safe>.py" not in rendered


def test_projection_fields_can_be_traced_to_evidence() -> None:
    index = EvidenceIndex.empty().append(event(evidence_ids=("journal:42", "validation:v1")))
    assert index.require_evidence("evt-1") == ("journal:42", "validation:v1")
    with pytest.raises(ValueError, match="has no evidence"):
        EvidenceIndex.empty().append(event(evidence_ids=())).require_evidence("evt-1")
