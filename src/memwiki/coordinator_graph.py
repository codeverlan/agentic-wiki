from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, List, Mapping, Sequence, Set, Tuple

from memwiki.coordinator_models import SliceRecord

SUCCESSFUL_TERMINAL_STATUSES = frozenset({"integrated"})
UNSUCCESSFUL_TERMINAL_STATUSES = frozenset({"failed", "cancelled", "superseded"})
TERMINAL_STATUSES = SUCCESSFUL_TERMINAL_STATUSES | UNSUCCESSFUL_TERMINAL_STATUSES
PENDING_STATUSES = frozenset({"proposed", "ready", "waiting"})
IN_PROGRESS_STATUSES = frozenset({"active", "validating", "completed"})


@dataclass(frozen=True)
class GraphEvaluation:
    ready: Tuple[str, ...]
    waiting: Tuple[str, ...]
    blocked: Tuple[str, ...]
    terminal: Tuple[str, ...]
    unreachable: Tuple[str, ...]
    in_progress: Tuple[str, ...]


def evaluate_dependency_graph(slices: Sequence[SliceRecord]) -> GraphEvaluation:
    """Derive deterministic scheduler classifications from a slice DAG."""
    nodes = _index_slices(slices)
    dependents = _validate_and_build_dependents(nodes)
    topological_order = _topological_order(nodes, dependents)

    terminal = {slice_id for slice_id, node in nodes.items() if node.status in TERMINAL_STATUSES}
    unsuccessful = {slice_id for slice_id, node in nodes.items() if node.status in UNSUCCESSFUL_TERMINAL_STATUSES}
    unreachable = _propagate_descendants(unsuccessful, dependents) - terminal

    direct_blockers = {slice_id for slice_id, node in nodes.items() if node.status == "blocked"}
    blocked = (_propagate_descendants(direct_blockers, dependents) - terminal) - unreachable

    ready: Set[str] = set()
    waiting: Set[str] = set()
    in_progress: Set[str] = set()
    for slice_id in topological_order:
        node = nodes[slice_id]
        if slice_id in terminal or slice_id in blocked or slice_id in unreachable:
            continue
        if node.status in IN_PROGRESS_STATUSES:
            in_progress.add(slice_id)
            continue
        if node.status not in PENDING_STATUSES:
            raise ValueError(f"unsupported graph status for {slice_id}: {node.status}")
        if all(nodes[dependency].status in SUCCESSFUL_TERMINAL_STATUSES for dependency in node.depends_on):
            ready.add(slice_id)
        else:
            waiting.add(slice_id)

    return GraphEvaluation(
        ready=_sorted(ready),
        waiting=_sorted(waiting),
        blocked=_sorted(blocked),
        terminal=_sorted(terminal),
        unreachable=_sorted(unreachable),
        in_progress=_sorted(in_progress),
    )


def _index_slices(slices: Sequence[SliceRecord]) -> Dict[str, SliceRecord]:
    nodes: Dict[str, SliceRecord] = {}
    for node in slices:
        if node.slice_id in nodes:
            raise ValueError(f"duplicate slice ID: {node.slice_id}")
        nodes[node.slice_id] = node
    return nodes


def _validate_and_build_dependents(
    nodes: Mapping[str, SliceRecord],
) -> Dict[str, List[str]]:
    dependents: Dict[str, List[str]] = {slice_id: [] for slice_id in nodes}
    for slice_id, node in nodes.items():
        for dependency in node.depends_on:
            if dependency == slice_id:
                raise ValueError(f"slice {slice_id} depends on itself")
            if dependency not in nodes:
                raise ValueError(f"slice {slice_id} has unknown dependency: {dependency}")
            dependents[dependency].append(slice_id)
    for children in dependents.values():
        children.sort()
    return dependents


def _topological_order(nodes: Mapping[str, SliceRecord], dependents: Mapping[str, Sequence[str]]) -> Tuple[str, ...]:
    indegree = {slice_id: len(node.depends_on) for slice_id, node in nodes.items()}
    ready = sorted(slice_id for slice_id, degree in indegree.items() if degree == 0)
    ordered: List[str] = []
    while ready:
        slice_id = ready.pop(0)
        ordered.append(slice_id)
        for child in dependents[slice_id]:
            indegree[child] -= 1
            if indegree[child] == 0:
                ready.append(child)
                ready.sort()
    if len(ordered) != len(nodes):
        cyclic = sorted(slice_id for slice_id, degree in indegree.items() if degree > 0)
        raise ValueError(f"dependency graph contains a cycle involving: {', '.join(cyclic)}")
    return tuple(ordered)


def _propagate_descendants(roots: Iterable[str], dependents: Mapping[str, Sequence[str]]) -> Set[str]:
    reached = set(roots)
    pending = sorted(reached)
    while pending:
        current = pending.pop(0)
        for child in dependents[current]:
            if child not in reached:
                reached.add(child)
                pending.append(child)
        pending.sort()
    return reached


def _sorted(values: Iterable[str]) -> Tuple[str, ...]:
    return tuple(sorted(values))
