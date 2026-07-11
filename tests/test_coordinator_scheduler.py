from __future__ import annotations

import random
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from memwiki.coordinator_discovery import DiscoveryProposal, ProposalProvenance
from memwiki.coordinator_lease import CoordinatorLease, LeaseIdentity
from memwiki.coordinator_models import SliceRecord
from memwiki.coordinator_scheduler import (
    SchedulerInput,
    SliceLeaseSnapshot,
    WorkerObservation,
    reconciliation_tick,
)
from memwiki.coordinator_workers import WorkerOutcome

NOW = datetime(2026, 7, 11, 12, 0, tzinfo=timezone.utc)
OWNER = LeaseIdentity("host", 42, "coordinator-1")
LEASE = CoordinatorLease(OWNER, 3, 9, "active", NOW, NOW, NOW + timedelta(minutes=1))


def node(
    slice_id: str,
    *,
    status: str = "waiting",
    depends_on: tuple[str, ...] = (),
    paths: tuple[str, ...] | None = None,
) -> SliceRecord:
    return SliceRecord(slice_id, slice_id, status, list(depends_on), list(paths or (slice_id,)), True)


def tick_input(*slices: SliceRecord, max_workers: int = 6) -> SchedulerInput:
    return SchedulerInput(
        slices=tuple(slices),
        slice_leases=(),
        worker_observations=(),
        discovery_proposals=(),
        coordinator_lease=LEASE,
        owner=OWNER,
        fencing_token=9,
        max_workers=max_workers,
    )


def test_identical_state_and_clock_produce_identical_decisions() -> None:
    state = tick_input(node("AC-B"), node("AC-A"))

    first = reconciliation_tick(state, now=NOW)
    second = reconciliation_tick(state, now=NOW)

    assert first == second
    assert [item.slice_id for item in first.dispatches] == ["AC-A", "AC-B"]
    assert all(event.event_id for event in first.events)


def test_rejects_expired_or_fenced_coordinator() -> None:
    with pytest.raises(ValueError, match="fencing token"):
        reconciliation_tick(replace(tick_input(node("AC-A")), fencing_token=8), now=NOW)
    with pytest.raises(ValueError, match="not live"):
        reconciliation_tick(
            replace(tick_input(node("AC-A")), coordinator_lease=replace(LEASE, expires_at=NOW)),
            now=NOW,
        )


def test_expires_stale_slice_leases_before_calculating_capacity() -> None:
    stale = SliceLeaseSnapshot("assignment-a", "AC-A", ("src/a",), "active", NOW)
    state = replace(
        tick_input(node("AC-A", status="active", paths=("src/a",)), node("AC-B", paths=("src/b",)), max_workers=1),
        slice_leases=(stale,),
    )

    result = reconciliation_tick(state, now=NOW)

    assert [(item.kind, item.slice_id) for item in result.intents] == [
        ("expire_lease", "AC-A"),
        ("retry", "AC-A"),
        ("dispatch", "AC-B"),
    ]


@pytest.mark.parametrize(
    ("kind", "intent"),
    [
        ("success", "validate"),
        ("retryable", "retry"),
        ("blocked", "open_blocker"),
        ("supervision", "request_supervision"),
        ("unavailable", "open_blocker"),
        ("malformed", "retry"),
    ],
)
def test_reconciles_worker_outcomes_into_typed_intents(kind: str, intent: str) -> None:
    outcome = WorkerOutcome(kind, None if kind == "success" else "reason", {} if kind == "success" else None)
    observation = WorkerObservation("assignment-a", "AC-A", "finished", outcome)
    state = replace(
        tick_input(node("AC-A", status="active")), worker_observations=(observation,)
    )

    result = reconciliation_tick(state, now=NOW)

    assert any(item.kind == intent and item.slice_id == "AC-A" for item in result.intents)
    assert not result.dispatches


def test_dependency_block_on_a_does_not_stop_independent_b() -> None:
    state = tick_input(
        node("AC-A", status="blocked"),
        node("AC-A-CHILD", depends_on=("AC-A",)),
        node("AC-B"),
    )

    result = reconciliation_tick(state, now=NOW)

    assert [item.slice_id for item in result.dispatches] == ["AC-B"]
    assert result.graph.blocked == ("AC-A", "AC-A-CHILD")


def test_capacity_never_exceeds_six_and_live_paths_are_respected() -> None:
    active = SliceLeaseSnapshot(
        "assignment-active", "AC-ACTIVE", ("src/shared",), "active", NOW + timedelta(seconds=30)
    )
    slices = [node("AC-ACTIVE", status="active", paths=("src/shared",))]
    slices.extend(node(f"AC-{index}", paths=(f"src/{index}",)) for index in range(10))
    slices.append(node("AC-CONFLICT", paths=("src/shared/child",)))
    state = replace(tick_input(*slices), slice_leases=(active,))

    result = reconciliation_tick(state, now=NOW)

    assert len(result.dispatches) == 5
    assert "AC-CONFLICT" not in {item.slice_id for item in result.dispatches}


def test_admits_discovery_before_graph_and_dispatch_decisions() -> None:
    proposal = DiscoveryProposal.create(
        proposal_id="proposal-new",
        slice_id="AC-NEW",
        title="new",
        depends_on=(),
        owned_paths=("src/new",),
        acceptance_criteria=("works",),
        required_tests=("tests",),
        reversible=True,
        plugin_scoped=False,
        provenance=ProposalProvenance("worker", "AC-A", "abc"),
    )
    state = replace(tick_input(node("AC-A"), max_workers=2), discovery_proposals=(proposal,))

    result = reconciliation_tick(state, now=NOW)

    assert result.admitted_slice_ids == ("AC-NEW",)
    assert [item.slice_id for item in result.dispatches] == ["AC-A", "AC-NEW"]
    assert result.intents[0].kind == "admit_discovery"


def test_overlapping_ready_paths_are_not_dispatched_together() -> None:
    state = tick_input(
        node("AC-A", paths=("src/shared",)),
        node("AC-B", paths=("src/shared/child",)),
        max_workers=2,
    )

    result = reconciliation_tick(state, now=NOW)

    assert [item.slice_id for item in result.dispatches] == ["AC-A"]
    assert result.deferred == ("AC-B",)


def test_persisted_ready_order_prevents_older_work_from_being_starved() -> None:
    state = replace(
        tick_input(node("AC-A"), node("AC-B"), max_workers=1),
        ready_order=("AC-B", "AC-A"),
    )

    result = reconciliation_tick(state, now=NOW)

    assert [item.slice_id for item in result.dispatches] == ["AC-B"]


def test_seeded_scheduler_invariants() -> None:
    randomizer = random.Random(22022)
    for iteration in range(100):
        count = randomizer.randint(1, 20)
        max_workers = randomizer.randint(1, 6)
        slices = tuple(
            node(
                f"AC-{iteration}-{index}",
                paths=(f"area/{randomizer.randint(0, count // 2)}",),
            )
            for index in range(count)
        )
        state = tick_input(*slices, max_workers=max_workers)

        first = reconciliation_tick(state, now=NOW)
        second = reconciliation_tick(state, now=NOW)

        assert first == second
        assert len(first.dispatches) <= max_workers <= 6
        paths = [item.owned_paths[0] for item in first.dispatches]
        assert len(paths) == len(set(paths))


def test_tick_is_pure_and_validates_worker_observation_identity() -> None:
    slices = (node("AC-A"),)
    state = tick_input(*slices)
    reconciliation_tick(state, now=NOW)
    assert state.slices == slices

    bad = WorkerObservation("assignment-x", "UNKNOWN", "running", None)
    with pytest.raises(ValueError, match="unknown slice"):
        reconciliation_tick(replace(state, worker_observations=(bad,)), now=NOW)
