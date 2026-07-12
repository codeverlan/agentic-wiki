from dataclasses import replace

import pytest

from memwiki.coordinator_events import CoordinatorEvent, EventChainError, verify_event_chain


def build_chain() -> list[CoordinatorEvent]:
    events: list[CoordinatorEvent] = []
    prior_hash = None
    for sequence, event_type in enumerate(["run.started", "slice.ready", "slice.started"], start=1):
        event = CoordinatorEvent.create(
            run_id="run-1",
            sequence=sequence,
            projection_revision=sequence,
            event_type=event_type,
            payload={"slice_id": "AC-001", "sequence": sequence},
            actor={"type": "coordinator", "id": "coordinator-1"},
            idempotency_key=f"run-1:{event_type}:{sequence}",
            prior_hash=prior_hash,
            event_id=f"event-{sequence}",
            occurred_at=f"2026-07-11T10:0{sequence}:00-04:00",
            correlation_id="corr-1",
            causation_id=None if sequence == 1 else f"event-{sequence - 1}",
        )
        events.append(event)
        prior_hash = event.event_hash
    return events


def test_event_chain_round_trips_and_verifies() -> None:
    events = build_chain()

    restored = [CoordinatorEvent.from_dict(event.to_dict()) for event in events]

    verify_event_chain(restored, run_id="run-1")
    assert [event.to_dict() for event in restored] == [event.to_dict() for event in events]


@pytest.mark.parametrize(
    "events",
    [
        lambda chain: [chain[1], chain[0], chain[2]],
        lambda chain: [chain[0], chain[1], chain[1], chain[2]],
        lambda chain: [chain[0], chain[2]],
    ],
)
def test_chain_detects_reordering_duplication_and_deletion(events: object) -> None:
    chain = events(build_chain())

    with pytest.raises(EventChainError):
        verify_event_chain(chain, run_id="run-1")


def test_chain_detects_payload_mutation_and_forged_hash() -> None:
    chain = build_chain()
    mutated = replace(chain[1], payload={"slice_id": "AC-999", "sequence": 2})
    forged = replace(chain[1], event_hash="0" * 64)

    with pytest.raises(EventChainError, match="payload hash"):
        verify_event_chain([chain[0], mutated, chain[2]], run_id="run-1")
    with pytest.raises(EventChainError, match="event hash"):
        verify_event_chain([chain[0], forged, chain[2]], run_id="run-1")


def test_event_rejects_non_json_payload_and_malformed_timestamp() -> None:
    with pytest.raises(ValueError, match="JSON serializable"):
        CoordinatorEvent.create(
            run_id="run-1",
            sequence=1,
            projection_revision=1,
            event_type="run.started",
            payload={"bad": {1, 2}},
            actor={"type": "coordinator", "id": "one"},
            idempotency_key="key",
            prior_hash=None,
        )
    with pytest.raises(ValueError, match="ISO-8601"):
        CoordinatorEvent.create(
            run_id="run-1",
            sequence=1,
            projection_revision=1,
            event_type="run.started",
            payload={},
            actor={"type": "coordinator", "id": "one"},
            idempotency_key="key",
            prior_hash=None,
            occurred_at="yesterday",
        )


def test_event_rejects_unknown_coordinator_event_type() -> None:
    with pytest.raises(ValueError, match="unsupported coordinator event type"):
        CoordinatorEvent.create(
            run_id="run-1",
            sequence=1,
            projection_revision=1,
            event_type="worker.invented",
            payload={},
            actor={"type": "coordinator", "id": "one"},
            idempotency_key="unknown-event",
            prior_hash=None,
        )
