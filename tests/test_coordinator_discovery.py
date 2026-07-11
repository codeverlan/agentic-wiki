from __future__ import annotations

from dataclasses import replace

import pytest

from memwiki.coordinator_discovery import (
    DiscoveryProposal,
    ProposalProvenance,
    replenish_queue,
)
from memwiki.coordinator_models import SliceRecord


def _slice(
    slice_id: str,
    *,
    status: str = "waiting",
    depends_on: tuple[str, ...] = (),
    paths: tuple[str, ...] = (),
) -> SliceRecord:
    return SliceRecord(slice_id, slice_id, status, list(depends_on), list(paths), True)


def _proposal(
    slice_id: str = "DISC-001",
    *,
    depends_on: tuple[str, ...] = ("AC-001",),
    paths: tuple[str, ...] = ("src/new.py",),
    plugin_scoped: bool = False,
) -> DiscoveryProposal:
    return DiscoveryProposal.create(
        proposal_id=f"proposal:{slice_id.lower()}",
        slice_id=slice_id,
        title="Implement discovered work",
        depends_on=depends_on,
        owned_paths=paths,
        acceptance_criteria=("behavior is implemented",),
        required_tests=("uv run pytest tests/test_new.py",),
        reversible=True,
        plugin_scoped=plugin_scoped,
        provenance=ProposalProvenance(
            source_kind="worker-report",
            source_id="attempt-7",
            source_hash="sha256:abc123",
        ),
    )


def test_admits_safe_independent_work_without_user_approval() -> None:
    current = (_slice("AC-001", status="integrated"), _slice("AC-900", paths=("src/other.py",)))

    result = replenish_queue(current, (_proposal(),))

    assert [item.slice_id for item in result.slices] == ["AC-001", "AC-900", "DISC-001"]
    admitted = result.decisions[0]
    assert admitted.disposition == "admitted"
    assert admitted.requires_supervision is False
    assert admitted.provenance.source_id == "attempt-7"
    assert admitted.decision_id.startswith("discovery-decision:")
    assert result.slices[-1].status == "ready"


def test_enabling_work_is_ready_and_keeps_parallel_work_available() -> None:
    current = (
        _slice("AC-001", status="integrated"),
        _slice("AC-002", depends_on=("DISC-001",)),
        _slice("AC-003", status="ready", paths=("docs/independent.html",)),
    )

    result = replenish_queue(current, (_proposal(),))

    assert result.admitted_ids == ("DISC-001",)
    assert {item.slice_id for item in result.slices if item.status == "ready"} == {
        "AC-003",
        "DISC-001",
    }


@pytest.mark.parametrize(
    ("proposal", "reason"),
    [
        (_proposal("AC-001", depends_on=()), "slice-id-conflict"),
        (_proposal("DISC-001"), "duplicate-proposal"),
    ],
)
def test_deduplicates_existing_and_repeated_proposals(
    proposal: DiscoveryProposal, reason: str
) -> None:
    current = (_slice("AC-001", status="integrated"),)
    proposals = (proposal, proposal) if reason == "duplicate-proposal" else (proposal,)

    result = replenish_queue(current, proposals)

    assert result.decisions[-1].reason == reason
    assert len({item.slice_id for item in result.slices}) == len(result.slices)


def test_identical_replenishment_is_deterministic() -> None:
    current = (_slice("AC-001", status="integrated"),)
    proposals = (_proposal(),)

    assert replenish_queue(current, proposals).to_dict() == replenish_queue(current, proposals).to_dict()


def test_rejects_unknown_dependency() -> None:
    result = replenish_queue((), (_proposal(depends_on=("MISSING",)),))

    assert result.admitted_ids == ()
    assert result.decisions[0].reason == "unknown-dependency"


def test_rejects_cycle_across_proposal_batch_atomically() -> None:
    first = _proposal("DISC-001", depends_on=("DISC-002",))
    second = _proposal("DISC-002", depends_on=("DISC-001",), paths=("src/two.py",))

    result = replenish_queue((), (first, second))

    assert result.slices == ()
    assert result.admitted_ids == ()
    assert {decision.reason for decision in result.decisions} == {"dependency-cycle"}


def test_rejects_path_conflict_with_active_ownership() -> None:
    current = (_slice("AC-001", status="active", paths=("src/service",)),)

    result = replenish_queue(current, (_proposal(paths=("src/service/api.py",), depends_on=()),))

    assert result.decisions[0].reason == "path-conflict"
    assert result.decisions[0].conflicts_with == ("AC-001",)


def test_waiting_slice_path_does_not_prevent_future_serial_work() -> None:
    current = (_slice("AC-001", paths=("src/service",)),)

    result = replenish_queue(current, (_proposal(paths=("src/service/api.py",), depends_on=()),))

    assert result.admitted_ids == ("DISC-001",)
    assert result.slices[-1].status == "waiting"


def test_plugin_mutation_requires_explicit_plugin_scope() -> None:
    proposal = _proposal(paths=("plugins/coordinator/SKILL.md",), depends_on=())

    result = replenish_queue((), (proposal,))

    assert result.decisions[0].disposition == "supervision-required"
    assert result.decisions[0].reason == "plugin-mutation-not-authorized"
    assert result.decisions[0].requires_supervision is True


def test_explicit_plugin_scoped_work_can_be_admitted() -> None:
    proposal = _proposal(
        paths=("plugins/coordinator/SKILL.md",), depends_on=(), plugin_scoped=True
    )

    assert replenish_queue((), (proposal,)).admitted_ids == ("DISC-001",)


@pytest.mark.parametrize(
    "change",
    [
        {"slice_id": "bad id"},
        {"depends_on": ("DISC-001",)},
        {"owned_paths": ()},
        {"acceptance_criteria": ()},
        {"required_tests": ()},
        {"reversible": False},
    ],
)
def test_invalid_or_irreversible_proposals_are_rejected_at_construction(
    change: dict[str, object]
) -> None:
    proposal = _proposal()

    with pytest.raises(ValueError):
        DiscoveryProposal.create(
            proposal_id=proposal.proposal_id,
            slice_id=str(change.get("slice_id", proposal.slice_id)),
            title=proposal.title,
            depends_on=change.get("depends_on", proposal.depends_on),  # type: ignore[arg-type]
            owned_paths=change.get("owned_paths", proposal.owned_paths),  # type: ignore[arg-type]
            acceptance_criteria=change.get(
                "acceptance_criteria", proposal.acceptance_criteria
            ),  # type: ignore[arg-type]
            required_tests=change.get("required_tests", proposal.required_tests),  # type: ignore[arg-type]
            reversible=bool(change.get("reversible", proposal.reversible)),
            plugin_scoped=proposal.plugin_scoped,
            provenance=proposal.provenance,
        )


def test_same_proposal_id_with_changed_content_is_rejected() -> None:
    original = _proposal()
    changed = replace(original, title="Different work")

    result = replenish_queue((_slice("AC-001", status="integrated"),), (original, changed))

    assert result.decisions[1].reason == "proposal-id-collision"


def test_two_proposals_cannot_target_the_same_slice_id() -> None:
    original = _proposal()
    duplicate_slice = replace(original, proposal_id="proposal:alternate")

    result = replenish_queue((_slice("AC-001", status="integrated"),), (original, duplicate_slice))

    assert result.admitted_ids == ("DISC-001",)
    assert result.decisions[1].reason == "slice-id-conflict"


def test_new_proposals_cannot_claim_overlapping_paths() -> None:
    first = _proposal(paths=("src/service",))
    second = _proposal("DISC-002", paths=("src/service/api.py",))

    result = replenish_queue((_slice("AC-001", status="integrated"),), (first, second))

    assert result.admitted_ids == ("DISC-001",)
    assert result.decisions[1].reason == "path-conflict"
    assert result.decisions[1].conflicts_with == ("DISC-001",)


def test_dependency_on_rejected_batch_proposal_is_also_rejected() -> None:
    rejected = _proposal("DISC-001", paths=("plugins/coordinator/SKILL.md",), depends_on=())
    child = _proposal("DISC-002", depends_on=("DISC-001",), paths=("src/child.py",))

    result = replenish_queue((), (rejected, child))

    assert result.admitted_ids == ()
    assert result.decisions[1].reason == "dependency-not-admitted"
