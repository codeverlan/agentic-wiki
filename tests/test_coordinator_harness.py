from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from memwiki.coordinator_harness import (
    DeterministicExternalAdapter,
    DeterministicWorkerAdapter,
    FakeClock,
    FaultRule,
    HarnessScenario,
    OrchestrationHarness,
    ScenarioSlice,
)

START = datetime(2026, 7, 11, 12, 0, tzinfo=timezone.utc)


def test_fake_clock_is_explicit_and_timezone_aware() -> None:
    clock = FakeClock(START)

    assert clock.now() == START
    assert clock.advance(timedelta(seconds=7)) == START + timedelta(seconds=7)
    with pytest.raises(ValueError, match="timezone-aware"):
        FakeClock(datetime(2026, 7, 11, 12, 0))
    with pytest.raises(ValueError, match="non-negative"):
        clock.advance(timedelta(seconds=-1))


def test_scenario_dsl_round_trips_and_rejects_cycles() -> None:
    scenario = HarnessScenario.from_dict(
        {
            "name": "dependency",
            "seed": 19,
            "max_workers": 2,
            "slices": [
                {"id": "A", "depends_on": [], "owned_paths": ["src/a"], "worker_outcome": "success"},
                {"id": "B", "depends_on": ["A"], "owned_paths": ["src/b"], "worker_outcome": "success"},
            ],
            "faults": [],
        }
    )

    assert HarnessScenario.from_dict(scenario.to_dict()) == scenario
    payload = scenario.to_dict()
    payload["slices"][0]["depends_on"] = ["B"]  # type: ignore[index]
    with pytest.raises(ValueError, match="cycle"):
        HarnessScenario.from_dict(payload)


def test_seeded_runs_have_identical_trace_and_evidence() -> None:
    scenario = HarnessScenario(
        "repeatable",
        812,
        2,
        (
            ScenarioSlice("A", (), ("src/a",), "success", ("lookup",)),
            ScenarioSlice("B", (), ("src/b",), "success", ()),
        ),
        (FaultRule("validation", occurrence=1, effect="retryable"),),
    )

    first = OrchestrationHarness(clock=FakeClock(START)).run(scenario)
    second = OrchestrationHarness(clock=FakeClock(START)).run(scenario)

    assert first == second
    assert first.evidence_sha256 == second.evidence_sha256
    assert first.trace
    assert all(item.before_sha256 and item.after_sha256 for item in first.trace)
    assert all(item.before and item.after for item in first.trace)


@pytest.mark.parametrize("boundary", ["tick", "dispatch", "report", "validation", "integration"])
def test_faults_are_injectable_at_every_orchestration_boundary(boundary: str) -> None:
    scenario = HarnessScenario(
        f"fault-{boundary}",
        3,
        1,
        (ScenarioSlice("A", (), ("src/a",), "success", ()),),
        (FaultRule(boundary, occurrence=1, effect="blocked"),),
    )

    result = OrchestrationHarness(clock=FakeClock(START)).run(scenario)

    assert result.statuses["A"] == "blocked"
    assert any(item.boundary == boundary and item.fault == "blocked" for item in result.trace)


def test_blocked_a_does_not_prevent_independent_b_from_integrating() -> None:
    scenario = HarnessScenario.blocked_a_independent_b(seed=44)

    result = OrchestrationHarness(clock=FakeClock(START)).run(scenario)

    assert result.statuses == {"A": "blocked", "A-child": "blocked", "B": "integrated"}
    assert "B" in result.integrated
    assert result.queue_drained is False
    b_events = [item.boundary for item in result.trace if item.slice_id == "B"]
    assert b_events == ["tick", "dispatch", "report", "validation", "integration"]


def test_external_adapter_is_scripted_bounded_and_offline() -> None:
    adapter = DeterministicExternalAdapter({"lookup": ({"value": 7},)})

    assert adapter.invoke("lookup", {"query": "x"}) == {"value": 7}
    assert adapter.calls[0].capability == "lookup"
    with pytest.raises(ValueError, match="no scripted response"):
        adapter.invoke("network", {})


def test_worker_adapter_consumes_scripted_outcomes_deterministically() -> None:
    adapter = DeterministicWorkerAdapter({"A": ("retryable", "success")})

    assert adapter.report("A") == "retryable"
    assert adapter.report("A") == "success"
    assert adapter.reports == [("A", "retryable"), ("A", "success")]
    with pytest.raises(ValueError, match="no scripted worker outcome"):
        adapter.report("A")


def test_different_seeds_choose_reproducible_ready_order() -> None:
    slices = tuple(
        ScenarioSlice(name, (), (f"src/{name.lower()}",), "success", ())
        for name in ("A", "B", "C", "D")
    )
    first = OrchestrationHarness(clock=FakeClock(START)).run(
        HarnessScenario("order", 1, 1, slices, ())
    )
    repeated = OrchestrationHarness(clock=FakeClock(START)).run(
        HarnessScenario("order", 1, 1, slices, ())
    )
    other = OrchestrationHarness(clock=FakeClock(START)).run(
        HarnessScenario("order", 2, 1, slices, ())
    )

    assert first.dispatch_order == repeated.dispatch_order
    assert first.dispatch_order != other.dispatch_order
