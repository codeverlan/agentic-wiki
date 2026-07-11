from __future__ import annotations

import random
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from memwiki.coordinator_events import CoordinatorEvent, EventChainError, verify_event_chain
from memwiki.coordinator_lease import CoordinatorLease, LeaseIdentity
from memwiki.coordinator_models import SliceRecord
from memwiki.coordinator_privacy import DataProfile, PrivacyPolicy, PrivacyViolationError, RuntimeSurface
from memwiki.coordinator_scheduler import SchedulerInput, reconciliation_tick

NOW = datetime(2026, 7, 11, 12, 0, tzinfo=timezone.utc)
OWNER = LeaseIdentity("property-host", 4300, "property-coordinator")
LEASE = CoordinatorLease(OWNER, 1, 17, "active", NOW, NOW, NOW + timedelta(minutes=5))
SEEDS = (1, 7, 43, 20260711, 0xAC043)


def _chain(seed: int, count: int) -> list[CoordinatorEvent]:
    randomizer = random.Random(seed)
    events: list[CoordinatorEvent] = []
    prior_hash = None
    for sequence in range(1, count + 1):
        event = CoordinatorEvent.create(
            run_id=f"run-{seed}",
            sequence=sequence,
            projection_revision=sequence,
            event_type=randomizer.choice(("slice.ready", "slice.started", "slice.updated")),
            payload={"seed": seed, "sequence": sequence, "value": randomizer.randrange(10**9)},
            actor={"kind": "coordinator", "worker": randomizer.randrange(6)},
            idempotency_key=f"key-{seed}-{sequence}",
            prior_hash=prior_hash,
            occurred_at=f"2026-07-11T12:{sequence // 60:02d}:{sequence % 60:02d}+00:00",
        )
        events.append(event)
        prior_hash = event.event_hash
    return events


@pytest.mark.parametrize("seed", SEEDS)
def test_generated_event_chains_round_trip_and_detect_single_mutations(seed: int) -> None:
    chain = _chain(seed, 48)
    restored = [CoordinatorEvent.from_dict(event.to_dict()) for event in chain]
    verify_event_chain(restored, run_id=f"run-{seed}")

    randomizer = random.Random(seed ^ 0xBAD)
    index = randomizer.randrange(len(chain))
    mutated = list(chain)
    mutated[index] = replace(mutated[index], payload={"mutated": True, "seed": seed})
    with pytest.raises(EventChainError):
        verify_event_chain(mutated, run_id=f"run-{seed}")


@pytest.mark.parametrize("seed", SEEDS)
def test_generated_event_stream_structural_damage_is_always_rejected(seed: int) -> None:
    chain = _chain(seed, 24)
    randomizer = random.Random(seed)
    index = randomizer.randrange(1, len(chain) - 1)
    variants = (
        chain[:index] + chain[index + 1 :],
        chain[:index] + [chain[index], chain[index]] + chain[index + 1 :],
        chain[: index - 1] + [chain[index], chain[index - 1]] + chain[index + 1 :],
    )
    for damaged in variants:
        with pytest.raises(EventChainError):
            verify_event_chain(damaged, run_id=f"run-{seed}")


def _node(slice_id: str, dependencies: tuple[str, ...], path: str) -> SliceRecord:
    return SliceRecord(slice_id, slice_id, "waiting", list(dependencies), [path], True)


@pytest.mark.parametrize("seed", SEEDS)
def test_generated_dags_schedule_deterministically_with_bounded_nonoverlapping_work(seed: int) -> None:
    randomizer = random.Random(seed)
    slices: list[SliceRecord] = []
    for index in range(40):
        candidates = [item.slice_id for item in slices]
        dependencies = tuple(sorted(randomizer.sample(candidates, k=min(len(candidates), randomizer.randrange(3)))))
        slices.append(_node(f"S-{index:02d}", dependencies, f"area/{index % 11}"))
    state = SchedulerInput(
        slices=tuple(slices),
        slice_leases=(),
        worker_observations=(),
        discovery_proposals=(),
        coordinator_lease=LEASE,
        owner=OWNER,
        fencing_token=17,
        max_workers=6,
    )

    first = reconciliation_tick(state, now=NOW)
    second = reconciliation_tick(state, now=NOW)

    assert first == second
    assert len(first.dispatches) <= 6
    dispatched_paths = [dispatch.owned_paths[0] for dispatch in first.dispatches]
    assert len(dispatched_paths) == len(set(dispatched_paths))
    assert state.slices == tuple(slices)


@pytest.mark.parametrize("seed", SEEDS)
def test_generated_privacy_canaries_never_appear_in_diagnostics(seed: int) -> None:
    canary = f"SYNTHETIC-PHI-{random.Random(seed).getrandbits(96):024x}"
    policy = PrivacyPolicy(DataProfile.YES, phi_canaries=(canary,))

    with pytest.raises(PrivacyViolationError) as caught:
        policy.inspect(RuntimeSurface.REPORT, {"nested": ["safe", canary]}, location=canary)

    serialized = str(caught.value) + repr(caught.value.to_dict())
    assert canary not in serialized
    assert caught.value.violations[0].evidence_sha256
