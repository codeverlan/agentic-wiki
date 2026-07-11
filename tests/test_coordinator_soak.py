from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

from memwiki.coordinator_checkpoints import CheckpointManager
from memwiki.coordinator_events import CoordinatorEvent
from memwiki.coordinator_journal import CoordinatorJournal
from memwiki.coordinator_lease import CoordinatorLease, LeaseIdentity
from memwiki.coordinator_models import SliceRecord
from memwiki.coordinator_projection import CoordinatorProjection, reduce_events
from memwiki.coordinator_scheduler import SchedulerInput, reconciliation_tick

FIXTURE = Path(__file__).parent / "fixtures/coordinator_soak/nightly_seed_4601.json"
START = datetime(2026, 7, 11, 16, 0, tzinfo=timezone.utc)


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("ascii")


def _sha256(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


@dataclass(frozen=True)
class SliceDefinition:
    slice_id: str
    depends_on: Tuple[str, ...]
    owned_paths: Tuple[str, ...]
    external: bool
    retry_once: bool
    block_once: bool


@dataclass(frozen=True)
class SoakResult:
    projection: bytes
    evidence: bytes
    ticks: int
    restarts: int
    peak_workers: int
    external_effects: int


class SoakRun:
    def __init__(self, root: Path, config: Mapping[str, object]) -> None:
        self.root = root
        self.config = config
        self.run_id = f"soak-{config['seed']}"
        self.journal = CoordinatorJournal(root / "journal.bin")
        self.checkpoints = CheckpointManager(root / "checkpoints", self.journal)
        self.now = START
        self.last_checkpoint_revision = 0
        self.events: List[CoordinatorEvent] = []
        self.projection: Optional[CoordinatorProjection] = None

    def append(self, event_type: str, payload: Dict[str, object]) -> CoordinatorProjection:
        sequence = len(self.events) + 1
        event = CoordinatorEvent.create(
            run_id=self.run_id,
            sequence=sequence,
            projection_revision=sequence,
            event_type=event_type,
            payload=payload,
            actor={"kind": "soak", "id": "deterministic-driver"},
            idempotency_key=f"soak:{sequence}",
            prior_hash=self.events[-1].event_hash if self.events else None,
            occurred_at=self.now.isoformat(),
        )
        self.journal.append_event(event)
        self.events.append(event)
        self.now += timedelta(milliseconds=1)
        if self.projection is None:
            self.projection = reduce_events(self.events)
        else:
            slices = self.projection.slices
            if event_type == "slice.registered":
                slices[str(payload["slice_id"])] = dict(payload)
            elif event_type == "slice.status_changed":
                slice_id = str(payload["slice_id"])
                slices[slice_id] = {**slices[slice_id], **payload}
            status = (
                str(payload.get("status", "active"))
                if event_type in {"run.started", "run.status_changed"}
                else self.projection.status
            )
            self.projection = CoordinatorProjection(
                schema_version=1,
                run_id=self.run_id,
                revision=sequence,
                status=status,
                max_workers=self.projection.max_workers,
                slices=slices,
                blockers=self.projection.blockers,
                stop_requests=self.projection.stop_requests,
                last_event_hash=event.event_hash,
            )
        return self.projection

    def checkpoint(self, projection: CoordinatorProjection, tick: int) -> None:
        interval = int(self.config["checkpoint_every_events"])
        if projection.revision - self.last_checkpoint_revision < interval:
            return
        self.checkpoints.create(
            projection=projection,
            authority={"fencing_token": 1},
            active_attempts=[],
            active_leases=[],
            in_flight_receipts=[],
            budgets={"ticks": tick},
            git_state={"worktrees": []},
            continuation_cursor={"tick": tick},
        )
        self.last_checkpoint_revision = projection.revision

    def restart(self, tick: int) -> CoordinatorProjection:
        acknowledgement = self.checkpoints.resume(run_id=self.run_id)
        assert acknowledgement.acknowledged
        assert acknowledgement.checkpoint.continuation_cursor["tick"] <= tick
        self.journal = CoordinatorJournal(self.root / "journal.bin")
        self.checkpoints = CheckpointManager(self.root / "checkpoints", self.journal)
        self.events = self.journal.read_events(run_id=self.run_id)
        self.now = START + timedelta(milliseconds=len(self.events))
        self.projection = reduce_events(self.events)
        return self.projection


def _definitions(config: Mapping[str, object]) -> Tuple[SliceDefinition, ...]:
    initial = int(config["initial_slices"])
    discovered = int(config["discovered_slices"])
    retry_modulus = int(config["retry_modulus"])
    block_modulus = int(config["block_modulus"])
    external_modulus = int(config["external_modulus"])
    values = []
    for index in range(initial + discovered):
        # Six lanes keep all workers useful while preserving a long dependency chain.
        dependencies = () if index < 6 else (f"S{index - 6:04d}",)
        values.append(
            SliceDefinition(
                slice_id=f"S{index:04d}",
                depends_on=dependencies,
                owned_paths=(f"src/soak/lane-{index % 6}/slice-{index:04d}",),
                external=index % external_modulus == 0,
                retry_once=index % retry_modulus == 0,
                block_once=index % block_modulus == 0,
            )
        )
    return tuple(values)


def _registered_count(config: Mapping[str, object], tick: int) -> int:
    initial = int(config["initial_slices"])
    discovered = int(config["discovered_slices"])
    discovery_ticks = tuple(int(value) for value in config["discovery_ticks"])  # type: ignore[union-attr]
    batches = sum(tick >= value for value in discovery_ticks)
    return initial + discovered * batches // len(discovery_ticks)


def _attempts(projection: CoordinatorProjection, slice_id: str) -> int:
    return int(projection.slices[slice_id].get("attempt", 0))


def _receipt_keys(events: Iterable[CoordinatorEvent]) -> Set[str]:
    return {
        str(event.payload["effect_key"])
        for event in events
        if event.event_type == "external.succeeded"
    }


def _records(
    definitions: Sequence[SliceDefinition], projection: CoordinatorProjection
) -> Tuple[SliceRecord, ...]:
    return tuple(
        SliceRecord(
            item.slice_id,
            item.slice_id,
            str(projection.slices[item.slice_id]["status"]),
            list(item.depends_on),
            list(item.owned_paths),
            True,
        )
        for item in definitions
        if item.slice_id in projection.slices
    )


def _run(root: Path, config: Mapping[str, object], *, restart: bool) -> SoakResult:
    run = SoakRun(root, config)
    definitions = _definitions(config)
    projection = run.append(
        "run.started", {"status": "active", "max_workers": int(config["max_workers"])}
    )
    crash_ticks = {int(value) for value in config["crash_ticks"]}  # type: ignore[union-attr]
    restarts = 0
    peak_workers = 0
    owner = LeaseIdentity("soak-host", 4601, "deterministic-process")
    lease = CoordinatorLease(
        owner,
        1,
        1,
        "active",
        START,
        START,
        START + timedelta(days=1),
    )

    for tick in range(int(config["max_ticks"])):
        target_count = _registered_count(config, tick)
        registered = len(projection.slices)
        for definition in definitions[registered:target_count]:
            projection = run.append(
                "slice.registered",
                {
                    "slice_id": definition.slice_id,
                    "title": definition.slice_id,
                    "status": "waiting",
                    "depends_on": list(definition.depends_on),
                    "owned_paths": list(definition.owned_paths),
                    "attempt": 0,
                },
            )

        receipts = _receipt_keys(run.events)
        scheduler_input = SchedulerInput(
            slices=_records(definitions[:target_count], projection),
            slice_leases=(),
            worker_observations=(),
            discovery_proposals=(),
            coordinator_lease=lease,
            owner=owner,
            fencing_token=1,
            max_workers=int(config["max_workers"]),
            ready_order=tuple(item.slice_id for item in definitions[:target_count]),
        )
        decision = reconciliation_tick(scheduler_input, now=run.now)
        dispatches = decision.dispatches[: int(config["max_workers"])]
        peak_workers = max(peak_workers, len(dispatches))

        for dispatch in dispatches:
            definition = definitions[int(dispatch.slice_id[1:])]
            attempt = _attempts(projection, definition.slice_id) + 1
            projection = run.append(
                "slice.status_changed",
                {"slice_id": definition.slice_id, "status": "active", "attempt": attempt},
            )
            if (definition.retry_once or definition.block_once) and attempt == 1:
                projection = run.append(
                    "slice.status_changed",
                    {
                        "slice_id": definition.slice_id,
                        "status": "waiting",
                        "attempt": attempt,
                        "transient": "blocked" if definition.block_once else "retryable",
                    },
                )
                continue
            effect_key = f"effect:{definition.slice_id}"
            if definition.external and effect_key not in receipts:
                projection = run.append(
                    "external.succeeded",
                    {
                        "slice_id": definition.slice_id,
                        "effect_key": effect_key,
                        "receipt_sha256": _sha256({"effect_key": effect_key}),
                    },
                )
                receipts.add(effect_key)
            for status in ("validating", "completed", "integrated"):
                projection = run.append(
                    "slice.status_changed",
                    {"slice_id": definition.slice_id, "status": status, "attempt": attempt},
                )

        run.checkpoint(projection, tick)
        if restart and tick in crash_ticks:
            projection = run.restart(tick)
            restarts += 1

        if tick + 1 >= int(config["minimum_ticks"]) and len(projection.slices) == len(
            definitions
        ) and all(
            item["status"] == "integrated" for item in projection.slices.values()
        ):
            final_tick = tick + 1
            break
    else:
        raise AssertionError("bounded soak did not drain before max_ticks")

    projection = run.append("run.status_changed", {"status": "completed"})
    events = run.journal.read_events(run_id=run.run_id)
    receipts = sorted(_receipt_keys(events))
    evidence = _canonical(
        {
            "projection": projection.to_dict(),
            "event_hashes": [event.event_hash for event in events],
            "external_receipts": receipts,
            "queue_drained": all(
                item["status"] == "integrated" for item in projection.slices.values()
            ),
        }
    )
    return SoakResult(
        projection=projection.to_json_bytes(),
        evidence=evidence,
        ticks=final_tick,
        restarts=restarts,
        peak_workers=peak_workers,
        external_effects=len(receipts),
    )


def test_long_running_soak_is_restart_equivalent_and_drains(tmp_path: Path) -> None:
    config = json.loads(FIXTURE.read_text(encoding="utf-8"))

    uninterrupted = _run(tmp_path / "control", config, restart=False)
    restarted = _run(tmp_path / "restarted", config, restart=True)

    assert restarted.projection == uninterrupted.projection
    assert restarted.evidence == uninterrupted.evidence
    assert restarted.restarts == len(config["crash_ticks"])
    assert restarted.peak_workers == config["max_workers"] == 6
    assert restarted.external_effects == 22
    assert restarted.ticks == config["minimum_ticks"]
    assert len(json.loads(restarted.projection)["slices"]) == 240
    evidence = json.loads(restarted.evidence)
    assert evidence["queue_drained"] is True
    assert len(evidence["external_receipts"]) == restarted.external_effects
    assert len(evidence["external_receipts"]) == len(set(evidence["external_receipts"]))
