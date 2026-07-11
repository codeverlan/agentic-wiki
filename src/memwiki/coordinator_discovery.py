from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from pathlib import PurePosixPath
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from memwiki.coordinator_graph import evaluate_dependency_graph
from memwiki.coordinator_models import SliceRecord

_ID_PATTERN = re.compile(r"^[A-Z][A-Z0-9]*(?:-[A-Z0-9]+)+$")
_ACTIVE_OWNERSHIP_STATUSES = frozenset({"active", "validating", "completed"})
_PLUGIN_ROOTS = frozenset({"plugin", "plugins"})


def _nonempty(value: str, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    return value


def _values(items: Iterable[str], label: str, *, required: bool = True) -> Tuple[str, ...]:
    values = tuple(items)
    if required and not values:
        raise ValueError(f"{label} must not be empty")
    if any(not isinstance(item, str) or not item.strip() for item in values):
        raise ValueError(f"{label} must contain non-empty strings")
    if len(values) != len(set(values)):
        raise ValueError(f"{label} must not contain duplicates")
    return values


def _path(value: str) -> str:
    _nonempty(value, "owned path")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise ValueError("owned paths must be confined relative paths")
    return value.rstrip("/")


def _overlap(left: str, right: str) -> bool:
    left_parts = PurePosixPath(left).parts
    right_parts = PurePosixPath(right).parts
    common = min(len(left_parts), len(right_parts))
    return left_parts[:common] == right_parts[:common]


def _canonical_hash(payload: Mapping[str, object]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _proposal_fingerprint(proposal: "DiscoveryProposal") -> str:
    payload = proposal.to_dict()
    del payload["content_hash"]
    return _canonical_hash(payload)


@dataclass(frozen=True)
class ProposalProvenance:
    source_kind: str
    source_id: str
    source_hash: str

    def __post_init__(self) -> None:
        _nonempty(self.source_kind, "provenance source_kind")
        _nonempty(self.source_id, "provenance source_id")
        _nonempty(self.source_hash, "provenance source_hash")

    def to_dict(self) -> Dict[str, str]:
        return asdict(self)


@dataclass(frozen=True)
class DiscoveryProposal:
    proposal_id: str
    slice_id: str
    title: str
    depends_on: Tuple[str, ...]
    owned_paths: Tuple[str, ...]
    acceptance_criteria: Tuple[str, ...]
    required_tests: Tuple[str, ...]
    reversible: bool
    plugin_scoped: bool
    provenance: ProposalProvenance
    content_hash: str

    @classmethod
    def create(
        cls,
        *,
        proposal_id: str,
        slice_id: str,
        title: str,
        depends_on: Iterable[str],
        owned_paths: Iterable[str],
        acceptance_criteria: Iterable[str],
        required_tests: Iterable[str],
        reversible: bool,
        plugin_scoped: bool,
        provenance: ProposalProvenance,
    ) -> "DiscoveryProposal":
        _nonempty(proposal_id, "proposal_id")
        if not _ID_PATTERN.fullmatch(slice_id):
            raise ValueError("slice_id must be a typed stable identifier")
        dependencies = _values(depends_on, "depends_on", required=False)
        if slice_id in dependencies:
            raise ValueError("a proposal cannot depend on itself")
        paths = tuple(_path(item) for item in _values(owned_paths, "owned_paths"))
        if not isinstance(reversible, bool) or not reversible:
            raise ValueError("discovered work must be explicitly reversible")
        if not isinstance(plugin_scoped, bool):
            raise ValueError("plugin_scoped must be a boolean")
        if not isinstance(provenance, ProposalProvenance):
            raise ValueError("provenance must be a ProposalProvenance")
        values: Dict[str, object] = {
            "proposal_id": proposal_id,
            "slice_id": slice_id,
            "title": _nonempty(title, "title"),
            "depends_on": list(dependencies),
            "owned_paths": list(paths),
            "acceptance_criteria": list(_values(acceptance_criteria, "acceptance_criteria")),
            "required_tests": list(_values(required_tests, "required_tests")),
            "reversible": reversible,
            "plugin_scoped": plugin_scoped,
            "provenance": provenance.to_dict(),
        }
        return cls(
            proposal_id=proposal_id,
            slice_id=slice_id,
            title=str(values["title"]),
            depends_on=dependencies,
            owned_paths=paths,
            acceptance_criteria=tuple(values["acceptance_criteria"]),  # type: ignore[arg-type]
            required_tests=tuple(values["required_tests"]),  # type: ignore[arg-type]
            reversible=reversible,
            plugin_scoped=plugin_scoped,
            provenance=provenance,
            content_hash=_canonical_hash(values),
        )

    def to_dict(self) -> Dict[str, object]:
        return {
            "proposal_id": self.proposal_id,
            "slice_id": self.slice_id,
            "title": self.title,
            "depends_on": list(self.depends_on),
            "owned_paths": list(self.owned_paths),
            "acceptance_criteria": list(self.acceptance_criteria),
            "required_tests": list(self.required_tests),
            "reversible": self.reversible,
            "plugin_scoped": self.plugin_scoped,
            "provenance": self.provenance.to_dict(),
            "content_hash": self.content_hash,
        }


@dataclass(frozen=True)
class DiscoveryDecision:
    decision_id: str
    proposal_id: str
    slice_id: str
    content_hash: str
    disposition: str
    reason: str
    requires_supervision: bool
    conflicts_with: Tuple[str, ...]
    provenance: ProposalProvenance

    @classmethod
    def create(
        cls,
        proposal: DiscoveryProposal,
        *,
        disposition: str,
        reason: str,
        requires_supervision: bool = False,
        conflicts_with: Iterable[str] = (),
    ) -> "DiscoveryDecision":
        conflicts = tuple(sorted(set(conflicts_with)))
        identity: Dict[str, object] = {
            "proposal_id": proposal.proposal_id,
            "content_hash": proposal.content_hash,
            "disposition": disposition,
            "reason": reason,
            "requires_supervision": requires_supervision,
            "conflicts_with": list(conflicts),
        }
        return cls(
            decision_id=f"discovery-decision:{_canonical_hash(identity)}",
            proposal_id=proposal.proposal_id,
            slice_id=proposal.slice_id,
            content_hash=proposal.content_hash,
            disposition=disposition,
            reason=reason,
            requires_supervision=requires_supervision,
            conflicts_with=conflicts,
            provenance=proposal.provenance,
        )

    def to_dict(self) -> Dict[str, object]:
        payload = asdict(self)
        payload["conflicts_with"] = list(self.conflicts_with)
        return payload


@dataclass(frozen=True)
class QueueReplenishment:
    slices: Tuple[SliceRecord, ...]
    decisions: Tuple[DiscoveryDecision, ...]
    admitted_ids: Tuple[str, ...]

    def to_dict(self) -> Dict[str, object]:
        return {
            "slices": [asdict(item) for item in self.slices],
            "decisions": [item.to_dict() for item in self.decisions],
            "admitted_ids": list(self.admitted_ids),
        }


def replenish_queue(
    current_slices: Sequence[SliceRecord],
    proposals: Sequence[DiscoveryProposal],
) -> QueueReplenishment:
    """Deterministically admit reversible discovered work into a valid slice DAG."""
    current = tuple(current_slices)
    current_by_id = {item.slice_id: item for item in current}
    all_proposal_ids = {item.slice_id for item in proposals}
    seen_proposals: Dict[str, str] = {}
    decisions: List[Optional[DiscoveryDecision]] = [None] * len(proposals)
    candidates: List[Tuple[int, DiscoveryProposal]] = []
    candidate_by_slice: Dict[str, DiscoveryProposal] = {}

    for index, proposal in enumerate(proposals):
        fingerprint = _proposal_fingerprint(proposal)
        prior_hash = seen_proposals.get(proposal.proposal_id)
        if prior_hash is not None:
            reason = "duplicate-proposal" if prior_hash == fingerprint else "proposal-id-collision"
            decisions[index] = DiscoveryDecision.create(
                proposal, disposition="rejected", reason=reason
            )
            continue
        seen_proposals[proposal.proposal_id] = fingerprint
        if proposal.slice_id in current_by_id:
            decisions[index] = (
                DiscoveryDecision.create(proposal, disposition="rejected", reason="slice-id-conflict")
            )
            continue
        if proposal.slice_id in candidate_by_slice:
            decisions[index] = DiscoveryDecision.create(
                proposal,
                disposition="rejected",
                reason="slice-id-conflict",
                conflicts_with=(proposal.slice_id,),
            )
            continue
        if any(_is_plugin_path(path) for path in proposal.owned_paths) and not proposal.plugin_scoped:
            decisions[index] = (
                DiscoveryDecision.create(
                    proposal,
                    disposition="supervision-required",
                    reason="plugin-mutation-not-authorized",
                    requires_supervision=True,
                )
            )
            continue
        unknown = sorted(
            dependency
            for dependency in proposal.depends_on
            if dependency not in current_by_id and dependency not in all_proposal_ids
        )
        if unknown:
            decisions[index] = (
                DiscoveryDecision.create(
                    proposal,
                    disposition="rejected",
                    reason="unknown-dependency",
                    conflicts_with=unknown,
                )
            )
            continue
        conflicts = _active_path_conflicts(proposal, current)
        conflicts += tuple(
            sorted(
                item.slice_id
                for item in candidate_by_slice.values()
                if _any_path_overlap(proposal.owned_paths, item.owned_paths)
            )
        )
        if conflicts:
            decisions[index] = (
                DiscoveryDecision.create(
                    proposal,
                    disposition="rejected",
                    reason="path-conflict",
                    conflicts_with=conflicts,
                )
            )
            continue
        candidates.append((index, proposal))
        candidate_by_slice[proposal.slice_id] = proposal

    while True:
        admitted_slice_ids = {item.slice_id for _, item in candidates}
        invalid = [
            (index, item)
            for index, item in candidates
            if any(
                dependency not in current_by_id and dependency not in admitted_slice_ids
                for dependency in item.depends_on
            )
        ]
        if not invalid:
            break
        invalid_indexes = {index for index, _ in invalid}
        for index, item in invalid:
            missing = (
                dependency
                for dependency in item.depends_on
                if dependency not in current_by_id and dependency not in admitted_slice_ids
            )
            decisions[index] = DiscoveryDecision.create(
                item,
                disposition="rejected",
                reason="dependency-not-admitted",
                conflicts_with=missing,
            )
        candidates = [item for item in candidates if item[0] not in invalid_indexes]

    provisional = current + tuple(
        _as_slice(item, current_by_id, current) for _, item in candidates
    )
    try:
        evaluate_dependency_graph(provisional)
    except ValueError as exc:
        if "cycle" not in str(exc):
            raise
        for index, item in candidates:
            decisions[index] = DiscoveryDecision.create(
                item, disposition="rejected", reason="dependency-cycle"
            )
        candidates = []
        provisional = current
    else:
        for index, item in candidates:
            decisions[index] = DiscoveryDecision.create(
                item, disposition="admitted", reason="safe-reversible-work"
            )

    admitted = tuple(item.slice_id for _, item in candidates)
    if any(item is None for item in decisions):
        raise RuntimeError("discovery did not produce a decision for every proposal")
    complete_decisions = tuple(item for item in decisions if item is not None)
    return QueueReplenishment(provisional, complete_decisions, admitted)


def _as_slice(
    proposal: DiscoveryProposal,
    current_by_id: Mapping[str, SliceRecord],
    current: Sequence[SliceRecord],
) -> SliceRecord:
    dependencies_ready = all(
        dependency in current_by_id and current_by_id[dependency].status == "integrated"
        for dependency in proposal.depends_on
    )
    path_is_available = not _any_path_overlap(
        proposal.owned_paths,
        (
            path
            for item in current
            if item.status not in {"integrated", "cancelled", "failed", "superseded"}
            for path in item.owned_paths
        ),
    )
    status = "ready" if dependencies_ready and path_is_available else "waiting"
    return SliceRecord(
        slice_id=proposal.slice_id,
        title=proposal.title,
        status=status,
        depends_on=list(proposal.depends_on),
        owned_paths=list(proposal.owned_paths),
        required=True,
    )


def _active_path_conflicts(
    proposal: DiscoveryProposal, current: Sequence[SliceRecord]
) -> Tuple[str, ...]:
    return tuple(
        sorted(
            item.slice_id
            for item in current
            if item.status in _ACTIVE_OWNERSHIP_STATUSES
            and _any_path_overlap(proposal.owned_paths, item.owned_paths)
        )
    )


def _any_path_overlap(left: Iterable[str], right: Iterable[str]) -> bool:
    right_values = tuple(right)
    return any(_overlap(a, b) for a in left for b in right_values)


def _is_plugin_path(value: str) -> bool:
    parts = PurePosixPath(value).parts
    return bool(parts) and (parts[0].lower() in _PLUGIN_ROOTS or ".codex/plugins" in value.lower())
