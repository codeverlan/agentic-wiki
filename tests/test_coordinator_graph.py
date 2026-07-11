from __future__ import annotations

import random
from typing import Iterable, List, Sequence

import pytest

from memwiki.coordinator_graph import evaluate_dependency_graph
from memwiki.coordinator_models import SliceRecord


def _slice(slice_id: str, status: str = "waiting", depends_on: Iterable[str] = ()) -> SliceRecord:
    return SliceRecord(
        slice_id=slice_id,
        title=slice_id,
        status=status,
        depends_on=list(depends_on),
        owned_paths=[],
        required=True,
    )


def test_diamond_schedules_each_frontier_deterministically() -> None:
    graph = [
        _slice("D", depends_on=["B", "C"]),
        _slice("C", depends_on=["A"]),
        _slice("A", status="integrated"),
        _slice("B", depends_on=["A"]),
    ]

    result = evaluate_dependency_graph(graph)

    assert result.ready == ("B", "C")
    assert result.waiting == ("D",)
    assert result.terminal == ("A",)


def test_fanout_order_is_stable_across_input_order() -> None:
    nodes = [_slice("root", status="integrated")]
    nodes.extend(_slice(f"child-{index:02d}", depends_on=["root"]) for index in range(12))
    expected = tuple(f"child-{index:02d}" for index in range(12))

    for seed in range(20):
        shuffled = list(nodes)
        random.Random(seed).shuffle(shuffled)
        assert evaluate_dependency_graph(shuffled).ready == expected


def test_blocker_propagates_only_to_descendants() -> None:
    result = evaluate_dependency_graph(
        [
            _slice("blocked-root", status="blocked"),
            _slice("child", depends_on=["blocked-root"]),
            _slice("grandchild", depends_on=["child"]),
            _slice("independent"),
            _slice("independent-child", depends_on=["independent"]),
        ]
    )

    assert result.blocked == ("blocked-root", "child", "grandchild")
    assert result.ready == ("independent",)
    assert result.waiting == ("independent-child",)
    assert result.unreachable == ()


@pytest.mark.parametrize("status", ["failed", "cancelled", "superseded"])
def test_unsuccessful_terminal_dependency_makes_descendants_unreachable(status: str) -> None:
    result = evaluate_dependency_graph(
        [
            _slice("failed-root", status=status),
            _slice("child", depends_on=["failed-root"]),
            _slice("grandchild", depends_on=["child"]),
            _slice("independent"),
        ]
    )

    assert result.terminal == ("failed-root",)
    assert result.unreachable == ("child", "grandchild")
    assert result.ready == ("independent",)


def test_unreachable_takes_precedence_when_ancestry_is_failed_and_blocked() -> None:
    result = evaluate_dependency_graph(
        [
            _slice("failed", status="failed"),
            _slice("blocked", status="blocked"),
            _slice("join", depends_on=["failed", "blocked"]),
        ]
    )

    assert result.blocked == ("blocked",)
    assert result.unreachable == ("join",)


def test_in_progress_work_is_distinct_from_waiting_and_completed_waits_for_integration() -> None:
    result = evaluate_dependency_graph(
        [
            _slice("active", status="active"),
            _slice("validating", status="validating"),
            _slice("completed", status="completed"),
            _slice("dependent", depends_on=["completed"]),
        ]
    )

    assert result.in_progress == ("active", "completed", "validating")
    assert result.waiting == ("dependent",)


@pytest.mark.parametrize(
    "nodes, message",
    [
        ([_slice("A"), _slice("A")], "duplicate slice ID"),
        ([_slice("A", depends_on=["missing"])], "unknown dependency"),
        ([_slice("A", depends_on=["A"])], "depends on itself"),
        ([_slice("A", depends_on=["B"]), _slice("B", depends_on=["A"])], "contains a cycle"),
    ],
)
def test_invalid_graphs_are_rejected(nodes: Sequence[SliceRecord], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        evaluate_dependency_graph(nodes)


def test_generated_dags_are_deterministic_and_preserve_dependency_readiness() -> None:
    for seed in range(100):
        randomizer = random.Random(seed)
        count = randomizer.randint(1, 35)
        statuses: List[str] = []
        nodes: List[SliceRecord] = []
        for index in range(count):
            prior = [f"N-{candidate:02d}" for candidate in range(index)]
            dependencies = [candidate for candidate in prior if randomizer.random() < 0.12]
            status = randomizer.choice(["integrated", "waiting", "waiting", "ready"])
            statuses.append(status)
            nodes.append(_slice(f"N-{index:02d}", status=status, depends_on=dependencies))

        shuffled = list(nodes)
        randomizer.shuffle(shuffled)
        first = evaluate_dependency_graph(shuffled)
        second = evaluate_dependency_graph(list(reversed(shuffled)))

        assert first == second
        for ready_id in first.ready:
            ready_node = next(node for node in nodes if node.slice_id == ready_id)
            assert all(
                statuses[int(dependency.removeprefix("N-"))] == "integrated" for dependency in ready_node.depends_on
            )


def test_generated_cycles_are_always_rejected() -> None:
    for size in range(2, 30):
        nodes = [_slice(f"N-{index:02d}", depends_on=[f"N-{(index - 1) % size:02d}"]) for index in range(size)]
        with pytest.raises(ValueError, match="contains a cycle"):
            evaluate_dependency_graph(nodes)
