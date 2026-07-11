from __future__ import annotations

import hashlib
import json
import random
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

from memwiki.coordinator_lease import CoordinatorLease, LeaseIdentity
from memwiki.coordinator_models import SliceRecord
from memwiki.coordinator_scheduler import SchedulerInput, reconciliation_tick

BOUNDARIES = ("tick", "dispatch", "report", "validation", "integration")
FAULT_EFFECTS = ("blocked", "retryable")
WORKER_OUTCOMES = ("success", "blocked", "retryable", "malformed")


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=True, allow_nan=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _aware(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("clock value must be timezone-aware")


class FakeClock:
    """Manually advanced wall clock for hermetic orchestration tests."""

    def __init__(self, start: datetime) -> None:
        _aware(start)
        self._value = start

    def now(self) -> datetime:
        return self._value

    def advance(self, amount: timedelta) -> datetime:
        if amount < timedelta(0):
            raise ValueError("clock advance must be non-negative")
        self._value += amount
        return self._value


@dataclass(frozen=True)
class FaultRule:
    boundary: str
    occurrence: int
    effect: str
    slice_id: Optional[str] = None

    def __post_init__(self) -> None:
        if self.boundary not in BOUNDARIES:
            raise ValueError("fault boundary is invalid")
        if isinstance(self.occurrence, bool) or self.occurrence < 1:
            raise ValueError("fault occurrence must be a positive integer")
        if self.effect not in FAULT_EFFECTS:
            raise ValueError("fault effect is invalid")
        if self.slice_id is not None and not self.slice_id:
            raise ValueError("fault slice_id must be non-empty or null")


@dataclass(frozen=True)
class ScenarioSlice:
    slice_id: str
    depends_on: Tuple[str, ...]
    owned_paths: Tuple[str, ...]
    worker_outcome: str
    external_calls: Tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.slice_id:
            raise ValueError("slice id must be non-empty")
        if self.worker_outcome not in WORKER_OUTCOMES:
            raise ValueError("worker outcome is invalid")
        if not self.owned_paths or not all(self.owned_paths):
            raise ValueError("owned paths must be non-empty")
        if len(self.depends_on) != len(set(self.depends_on)):
            raise ValueError("slice dependencies must be unique")


@dataclass(frozen=True)
class HarnessScenario:
    name: str
    seed: int
    max_workers: int
    slices: Tuple[ScenarioSlice, ...]
    faults: Tuple[FaultRule, ...]

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("scenario name must be non-empty")
        if isinstance(self.seed, bool) or not isinstance(self.seed, int):
            raise ValueError("scenario seed must be an integer")
        if isinstance(self.max_workers, bool) or not 1 <= self.max_workers <= 6:
            raise ValueError("max_workers must be from 1 through 6")
        self._validate_graph()

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> "HarnessScenario":
        if set(payload) != {"name", "seed", "max_workers", "slices", "faults"}:
            raise ValueError("scenario has unknown or missing fields")
        raw_slices = payload["slices"]
        raw_faults = payload["faults"]
        if not isinstance(raw_slices, list) or not isinstance(raw_faults, list):
            raise ValueError("scenario slices and faults must be lists")
        slices = []
        for raw in raw_slices:
            required = {"id", "depends_on", "owned_paths", "worker_outcome"}
            if (
                not isinstance(raw, Mapping)
                or not required <= set(raw)
                or set(raw) - required != {"external_calls"} and set(raw) != required
            ):
                raise ValueError("scenario slice is malformed")
            slices.append(
                ScenarioSlice(
                    str(raw["id"]),
                    _strings(raw["depends_on"], "depends_on"),
                    _strings(raw["owned_paths"], "owned_paths"),
                    str(raw["worker_outcome"]),
                    _strings(raw.get("external_calls", []), "external_calls"),
                )
            )
        faults = []
        for raw in raw_faults:
            if not isinstance(raw, Mapping):
                raise ValueError("scenario fault is malformed")
            faults.append(
                FaultRule(
                    str(raw["boundary"]),
                    int(raw["occurrence"]),
                    str(raw["effect"]),
                    None if raw.get("slice_id") is None else str(raw["slice_id"]),
                )
            )
        return cls(
            str(payload["name"]),
            _integer(payload["seed"], "seed"),
            _integer(payload["max_workers"], "max_workers"),
            tuple(slices),
            tuple(faults),
        )

    def to_dict(self) -> Dict[str, object]:
        return {
            "name": self.name,
            "seed": self.seed,
            "max_workers": self.max_workers,
            "slices": [
                {
                    "id": item.slice_id,
                    "depends_on": list(item.depends_on),
                    "owned_paths": list(item.owned_paths),
                    "worker_outcome": item.worker_outcome,
                    "external_calls": list(item.external_calls),
                }
                for item in self.slices
            ],
            "faults": [asdict(item) for item in self.faults],
        }

    @classmethod
    def blocked_a_independent_b(cls, *, seed: int) -> "HarnessScenario":
        return cls(
            "blocked-a-independent-b",
            seed,
            2,
            (
                ScenarioSlice("A", (), ("src/a",), "blocked", ()),
                ScenarioSlice("A-child", ("A",), ("src/a-child",), "success", ()),
                ScenarioSlice("B", (), ("src/b",), "success", ()),
            ),
            (),
        )

    def _validate_graph(self) -> None:
        nodes = {item.slice_id: item for item in self.slices}
        if len(nodes) != len(self.slices):
            raise ValueError("scenario slice ids must be unique")
        for item in self.slices:
            if any(dependency not in nodes for dependency in item.depends_on):
                raise ValueError("scenario has an unknown dependency")
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(slice_id: str) -> None:
            if slice_id in visiting:
                raise ValueError("scenario dependency graph contains a cycle")
            if slice_id in visited:
                return
            visiting.add(slice_id)
            for dependency in nodes[slice_id].depends_on:
                visit(dependency)
            visiting.remove(slice_id)
            visited.add(slice_id)

        for slice_id in sorted(nodes):
            visit(slice_id)


@dataclass(frozen=True)
class ExternalCall:
    capability: str
    input_sha256: str
    output_sha256: str


class DeterministicExternalAdapter:
    """Scripted resource double that performs no network or host I/O."""

    def __init__(self, scripts: Optional[Mapping[str, Sequence[Mapping[str, object]]]] = None) -> None:
        self._scripts = {key: list(values) for key, values in (scripts or {}).items()}
        self.calls: List[ExternalCall] = []

    def invoke(self, capability: str, payload: Mapping[str, object]) -> Mapping[str, object]:
        scripted = self._scripts.get(capability)
        if not scripted:
            raise ValueError(f"no scripted response for capability: {capability}")
        result = dict(scripted.pop(0))
        self.calls.append(ExternalCall(capability, _digest(payload), _digest(result)))
        return result


class DeterministicWorkerAdapter:
    """Seed-independent scripted worker double with one outcome per slice attempt."""

    def __init__(self, scripts: Mapping[str, Sequence[str]]) -> None:
        self._scripts = {key: list(values) for key, values in scripts.items()}
        self.reports: List[Tuple[str, str]] = []

    def report(self, slice_id: str) -> str:
        scripted = self._scripts.get(slice_id)
        if not scripted:
            raise ValueError(f"no scripted worker outcome for slice: {slice_id}")
        outcome = scripted.pop(0)
        if outcome not in WORKER_OUTCOMES:
            raise ValueError("scripted worker outcome is invalid")
        self.reports.append((slice_id, outcome))
        return outcome


@dataclass(frozen=True)
class TraceEntry:
    sequence: int
    at: str
    boundary: str
    slice_id: Optional[str]
    before_sha256: str
    after_sha256: str
    fault: Optional[str]
    before: Mapping[str, str]
    after: Mapping[str, str]


@dataclass(frozen=True)
class HarnessResult:
    scenario: str
    seed: int
    statuses: Mapping[str, str]
    dispatch_order: Tuple[str, ...]
    integrated: Tuple[str, ...]
    trace: Tuple[TraceEntry, ...]
    external_calls: Tuple[ExternalCall, ...]
    queue_drained: bool
    evidence_sha256: str


class OrchestrationHarness:
    """Run the real scheduler entry point against deterministic orchestration doubles."""

    def __init__(
        self,
        *,
        clock: FakeClock,
        external: Optional[DeterministicExternalAdapter] = None,
    ) -> None:
        self._clock = clock
        self._external = external

    def run(self, scenario: HarnessScenario) -> HarnessResult:
        rng = random.Random(scenario.seed)
        order = [item.slice_id for item in scenario.slices]
        rng.shuffle(order)
        definitions = {item.slice_id: item for item in scenario.slices}
        statuses = {item.slice_id: "waiting" for item in scenario.slices}
        occurrences: Dict[Tuple[str, Optional[str]], int] = {}
        consumed: set[int] = set()
        trace: List[TraceEntry] = []
        dispatched: List[str] = []
        owner = LeaseIdentity("harness", 1, f"seed-{scenario.seed}")
        started = self._clock.now()
        lease = CoordinatorLease(
            owner, 1, 1, "active", started, started, started + timedelta(days=1)
        )
        max_ticks = max(8, len(scenario.slices) * 8)
        external = self._external or self._default_external(scenario, max_ticks)
        workers = DeterministicWorkerAdapter(
            {item.slice_id: (item.worker_outcome,) * max_ticks for item in scenario.slices}
        )

        for _ in range(max_ticks):
            runnable = self._runnable(statuses, definitions)
            if not runnable:
                break
            tick_target = next(slice_id for slice_id in order if slice_id in runnable)
            if self._boundary(
                "tick", tick_target, statuses, scenario.faults, occurrences, consumed, trace
            ):
                continue
            state = SchedulerInput(
                slices=tuple(self._records(statuses, scenario.slices)),
                slice_leases=(),
                worker_observations=(),
                discovery_proposals=(),
                coordinator_lease=lease,
                owner=owner,
                fencing_token=1,
                max_workers=scenario.max_workers,
                ready_order=tuple(order),
            )
            decision = reconciliation_tick(state, now=self._clock.now())
            for dispatch in decision.dispatches:
                slice_id = dispatch.slice_id
                dispatched.append(slice_id)
                if self._boundary(
                    "dispatch", slice_id, statuses, scenario.faults, occurrences, consumed, trace
                ):
                    continue
                statuses[slice_id] = "active"
                if self._boundary(
                    "report", slice_id, statuses, scenario.faults, occurrences, consumed, trace
                ):
                    continue
                definition = definitions[slice_id]
                for capability in definition.external_calls:
                    external.invoke(capability, {"slice_id": slice_id})
                worker_outcome = workers.report(slice_id)
                if worker_outcome != "success":
                    statuses[slice_id] = (
                        "blocked" if worker_outcome == "blocked" else "waiting"
                    )
                    continue
                statuses[slice_id] = "validating"
                if self._boundary(
                    "validation", slice_id, statuses, scenario.faults, occurrences, consumed, trace
                ):
                    continue
                statuses[slice_id] = "completed"
                if self._boundary(
                    "integration", slice_id, statuses, scenario.faults, occurrences, consumed, trace
                ):
                    continue
                statuses[slice_id] = "integrated"
            self._propagate_blocked(statuses, definitions)

        self._propagate_blocked(statuses, definitions)
        integrated = tuple(sorted(key for key, value in statuses.items() if value == "integrated"))
        queue_drained = len(integrated) == len(statuses)
        evidence = {
            "scenario": scenario.name,
            "seed": scenario.seed,
            "statuses": statuses,
            "dispatch_order": dispatched,
            "integrated": integrated,
            "trace": [asdict(item) for item in trace],
            "external_calls": [asdict(item) for item in external.calls],
            "queue_drained": queue_drained,
        }
        return HarnessResult(
            scenario.name,
            scenario.seed,
            dict(sorted(statuses.items())),
            tuple(dispatched),
            integrated,
            tuple(trace),
            tuple(external.calls),
            queue_drained,
            _digest(evidence),
        )

    def _boundary(
        self,
        boundary: str,
        slice_id: str,
        statuses: Dict[str, str],
        rules: Sequence[FaultRule],
        occurrences: Dict[Tuple[str, Optional[str]], int],
        consumed: set[int],
        trace: List[TraceEntry],
    ) -> bool:
        before = dict(sorted(statuses.items()))
        keys: Tuple[Tuple[str, Optional[str]], ...] = (
            (boundary, slice_id),
            (boundary, None),
        )
        for key in keys:
            occurrences[key] = occurrences.get(key, 0) + 1
        effect = None
        for index, rule in enumerate(rules):
            key = (boundary, rule.slice_id)
            if (
                index not in consumed
                and rule.boundary == boundary
                and key in keys
                and occurrences[key] == rule.occurrence
            ):
                consumed.add(index)
                effect = rule.effect
                break
        if effect == "blocked":
            statuses[slice_id] = "blocked"
        elif effect == "retryable":
            statuses[slice_id] = "waiting"
        self._clock.advance(timedelta(seconds=1))
        trace.append(
            TraceEntry(
                len(trace) + 1,
                self._clock.now().isoformat(),
                boundary,
                slice_id,
                _digest(before),
                _digest(statuses),
                effect,
                before,
                dict(sorted(statuses.items())),
            )
        )
        return effect is not None

    @staticmethod
    def _records(
        statuses: Mapping[str, str], definitions: Sequence[ScenarioSlice]
    ) -> Sequence[SliceRecord]:
        return [
            SliceRecord(
                item.slice_id,
                item.slice_id,
                statuses[item.slice_id],
                list(item.depends_on),
                list(item.owned_paths),
                True,
            )
            for item in definitions
        ]

    @staticmethod
    def _runnable(
        statuses: Mapping[str, str], definitions: Mapping[str, ScenarioSlice]
    ) -> set[str]:
        return {
            slice_id
            for slice_id, status in statuses.items()
            if status in {"waiting", "ready"}
            and all(statuses[item] == "integrated" for item in definitions[slice_id].depends_on)
        }

    @staticmethod
    def _propagate_blocked(
        statuses: Dict[str, str], definitions: Mapping[str, ScenarioSlice]
    ) -> None:
        changed = True
        while changed:
            changed = False
            for slice_id, definition in definitions.items():
                if statuses[slice_id] in {"waiting", "ready"} and any(
                    statuses[dependency] == "blocked" for dependency in definition.depends_on
                ):
                    statuses[slice_id] = "blocked"
                    changed = True

    @staticmethod
    def _default_external(
        scenario: HarnessScenario, attempts: int
    ) -> DeterministicExternalAdapter:
        scripts: Dict[str, List[Mapping[str, object]]] = {}
        for item in scenario.slices:
            for capability in item.external_calls:
                scripts.setdefault(capability, []).extend(
                    {"capability": capability, "slice_id": item.slice_id, "offline": True}
                    for _ in range(attempts)
                )
        return DeterministicExternalAdapter(scripts)


def _strings(value: object, label: str) -> Tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        raise ValueError(f"{label} must be a list of non-empty strings")
    return tuple(value)


def _integer(value: object, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{label} must be an integer")
    return value
