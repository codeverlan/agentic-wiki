from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Dict, Mapping, Sequence, Set, Tuple, cast

REQUIRED_VALIDATION_GATES = (
    "unit",
    "property",
    "concurrency",
    "fault_injection",
    "bmad_e2e",
    "lightweight_e2e",
    "phi",
    "git",
    "plugin",
    "documentation",
    "security",
    "soak",
)
REQUIRED_PROJECTIONS = (
    "run_state",
    "handoff",
    "branch_ledger",
    "incident_summary",
    "dashboard_snapshot",
    "control_index",
)
QUEUE_BLOCKING_BUCKETS = (
    "ready",
    "active",
    "stale",
    "waiting_resolvable",
    "undisposed_reports",
    "uncertain_commands",
    "reachable_discoveries",
)
RESIDUAL_BUCKETS = (
    "credentials",
    "phi_canaries",
    "worker_processes",
    "leases",
    "locks",
    "temporary_worktrees",
    "unbounded_artifacts",
)
_HASH = re.compile(r"^sha256:[0-9a-f]{64}$")


class CompletionEvidenceError(ValueError):
    """Raised when completion evidence is incomplete, stale, or altered."""


@dataclass(frozen=True)
class CompletionCondition:
    condition_id: str
    passed: bool
    details: Tuple[str, ...]

    def to_dict(self) -> Dict[str, object]:
        return {
            "condition_id": self.condition_id,
            "passed": self.passed,
            "details": list(self.details),
        }


@dataclass(frozen=True)
class CompletionResult:
    complete: bool
    conditions: Tuple[CompletionCondition, ...]
    advisory_ids: Tuple[str, ...] = ()

    @property
    def disposition(self) -> str:
        """Return the only two successful terminal dispositions."""
        if not self.complete:
            return "incomplete"
        return "complete-with-advisories" if self.advisory_ids else "complete"

    @property
    def failed_conditions(self) -> Tuple[str, ...]:
        return tuple(item.condition_id for item in self.conditions if not item.passed)


def _canonical(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise CompletionEvidenceError("completion evidence must be JSON-compatible") from exc


def _digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value)).hexdigest()


def _mapping(value: object) -> Mapping[str, Any]:
    return cast(Mapping[str, Any], value) if isinstance(value, Mapping) else {}


def _sequence(value: object) -> Sequence[object]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return value
    return ()


def _condition(condition_id: str, problems: Sequence[str]) -> CompletionCondition:
    return CompletionCondition(condition_id, not problems, tuple(sorted(problems)))


def _final_revision(snapshot: Mapping[str, Any]) -> object:
    return snapshot.get("final_revision", snapshot.get("current_revision"))


def _required_work(snapshot: Mapping[str, Any]) -> CompletionCondition:
    problems = []
    revision = _final_revision(snapshot)
    slices = _mapping(snapshot.get("slices"))
    if not slices:
        problems.append("no required slices are recorded")
    for slice_id, raw in sorted(slices.items()):
        item = _mapping(raw)
        if not (item.get("required") is True or item.get("accepted_discovery") is True):
            continue
        if item.get("disposition") != "integrated":
            problems.append(f"{slice_id} is not integrated")
        if item.get("integrated_revision") != revision:
            problems.append(f"{slice_id} is not bound to the final revision")
        if not item.get("report_evidence_id") or not item.get("integration_evidence_id"):
            problems.append(f"{slice_id} lacks report or integration evidence")
        if item.get("evaluation_passed") is not True:
            problems.append(f"{slice_id} lacks a passing evaluation comparison")
        if item.get("evaluation_revision") != revision:
            problems.append(f"{slice_id} evaluation is not bound to the final revision")
        if not item.get("evaluation_evidence_id"):
            problems.append(f"{slice_id} lacks evaluation evidence")
    return _condition("required_work_integrated", problems)


def _queue_drained(snapshot: Mapping[str, Any]) -> CompletionCondition:
    queue = _mapping(snapshot.get("queue"))
    problems = []
    for bucket in QUEUE_BLOCKING_BUCKETS:
        entries = _sequence(queue.get(bucket))
        if entries:
            problems.append(f"{bucket} contains {len(entries)} item(s)")
    if not isinstance(snapshot.get("queue_digest"), str) or not _HASH.fullmatch(snapshot["queue_digest"]):
        problems.append("queue digest is missing or invalid")
    return _condition("queue_drained", problems)


def _mandatory_resolved(snapshot: Mapping[str, Any]) -> CompletionCondition:
    problems = []
    for collection in ("blockers", "supervision"):
        for raw in _sequence(snapshot.get(collection)):
            item = _mapping(raw)
            if item.get("mandatory") is True and item.get("status") not in {"resolved", "superseded"}:
                problems.append(f"{collection}:{item.get('id', 'unknown')} remains mandatory")
    return _condition("mandatory_items_resolved", problems)


def _active_leases_cleared(snapshot: Mapping[str, Any]) -> CompletionCondition:
    problems = []
    for collection in ("active_leases", "leases"):
        for raw in _sequence(snapshot.get(collection)):
            item = _mapping(raw)
            if collection == "active_leases" or item.get("status") in {"offered", "active"}:
                identifier = item.get("assignment_id", item.get("lease_id", "unknown"))
                problems.append(f"{collection}:{identifier} remains live")
    return _condition("active_leases_cleared", problems)


def _worker_reports_disposed(snapshot: Mapping[str, Any]) -> CompletionCondition:
    problems = []
    revision = _final_revision(snapshot)
    reports = _sequence(snapshot.get("worker_reports", snapshot.get("reports", ())))
    terminal = {"integrated", "rejected", "superseded", "preserved"}
    for raw in reports:
        report = _mapping(raw)
        report_id = report.get("report_id", "unknown")
        if report.get("disposition") not in terminal:
            problems.append(f"worker report {report_id} lacks a terminal disposition")
        if not report.get("evidence_id"):
            problems.append(f"worker report {report_id} lacks evidence")
        if "revision" in report and report.get("revision") != revision:
            problems.append(f"worker report {report_id} is not bound to the final revision")
    return _condition("worker_reports_disposed", problems)


def _validation_gates(snapshot: Mapping[str, Any]) -> CompletionCondition:
    gates = _mapping(snapshot.get("validation_gates"))
    revision = _final_revision(snapshot)
    problems = []
    for name in REQUIRED_VALIDATION_GATES:
        gate = _mapping(gates.get(name))
        if gate.get("passed") is not True:
            problems.append(f"{name} did not pass")
        if gate.get("revision") != revision:
            problems.append(f"{name} is stale")
        if not gate.get("evidence_id"):
            problems.append(f"{name} lacks evidence")
    return _condition("validation_gates_pass", problems)


def _boolean_condition(
    snapshot: Mapping[str, Any], condition_id: str, section: str, fields: Sequence[str]
) -> CompletionCondition:
    value = _mapping(snapshot.get(section))
    problems = [f"{section}.{field} is not true" for field in fields if value.get(field) is not True]
    if not value.get("evidence_id"):
        problems.append(f"{section} lacks evidence")
    return _condition(condition_id, problems)


def _git_state_matches(snapshot: Mapping[str, Any]) -> CompletionCondition:
    final_git = _mapping(snapshot.get("final_git"))
    problems = [
        f"final_git.{field} is not true"
        for field in ("local_matches", "remote_matches")
        if final_git.get(field) is not True
    ]
    if final_git.get("revision") != _final_revision(snapshot):
        problems.append("final git state is not bound to the final revision")
    if not final_git.get("evidence_id"):
        problems.append("final git state lacks evidence")
    return _condition("git_state_matches", problems)


def _residuals(snapshot: Mapping[str, Any]) -> CompletionCondition:
    values = _mapping(snapshot.get("residuals"))
    problems = []
    for name in RESIDUAL_BUCKETS:
        value = values.get(name)
        if not isinstance(value, int) or isinstance(value, bool) or value != 0:
            problems.append(f"{name} is not zero")
    if not values.get("evidence_id"):
        problems.append("residual scan lacks evidence")
    return _condition("no_residual_runtime_or_sensitive_data", problems)


def _runtime_reconciled(snapshot: Mapping[str, Any]) -> CompletionCondition:
    runtime = _mapping(snapshot.get("runtime"))
    if not runtime:
        # The original completion schema splits runtime reconciliation between
        # journal, budgets, and residual scans. Keep that payload valid.
        return _condition("runtime_reconciled", ())
    problems = [
        f"runtime.{field} is not true"
        for field in ("reconciled", "workers_reconciled", "effects_reconciled")
        if runtime.get(field) is not True
    ]
    if not runtime.get("evidence_id"):
        problems.append("runtime lacks evidence")
    return _condition("runtime_reconciled", problems)


def _memory_dispositions_terminal(snapshot: Mapping[str, Any]) -> CompletionCondition:
    values = _sequence(snapshot.get("memory_dispositions"))
    terminal = {"applied", "no_change", "rejected", "superseded", "advisory"}
    problems = []
    for raw in values:
        item = _mapping(raw)
        identifier = item.get("id", item.get("slice_id", "unknown"))
        if item.get("disposition") not in terminal:
            problems.append(f"memory disposition {identifier} is not terminal")
        if not item.get("evidence_id"):
            problems.append(f"memory disposition {identifier} lacks evidence")
    return _condition("memory_dispositions_terminal", problems)


def _sha_parity(snapshot: Mapping[str, Any]) -> CompletionCondition:
    parity = _mapping(snapshot.get("sha_parity"))
    if not parity:
        return _condition("sha_parity", ())
    revision = _final_revision(snapshot)
    problems = []
    for field in ("local_matches", "remote_matches", "artifacts_match"):
        if parity.get(field) is not True:
            problems.append(f"sha_parity.{field} is not true")
    if parity.get("revision") != revision:
        problems.append("sha parity is not bound to the final revision")
    if not parity.get("evidence_id"):
        problems.append("sha parity lacks evidence")
    return _condition("sha_parity", problems)


def _advisory_ids(snapshot: Mapping[str, Any]) -> Tuple[str, ...]:
    advisories = []
    for collection in ("blockers", "supervision"):
        for raw in _sequence(snapshot.get(collection)):
            item = _mapping(raw)
            if item.get("status") in {"resolved", "superseded"}:
                continue
            advisory = item.get("mandatory") is False or item.get("category") == "advisory"
            if advisory:
                advisories.append(f"{collection}:{item.get('id', item.get('item_id', 'unknown'))}")
    for raw in _sequence(snapshot.get("memory_dispositions")):
        item = _mapping(raw)
        if item.get("disposition") == "advisory":
            advisories.append(f"memory:{item.get('id', item.get('slice_id', 'unknown'))}")
    return tuple(sorted(set(advisories)))


def _projections(snapshot: Mapping[str, Any]) -> CompletionCondition:
    projections = _mapping(snapshot.get("projections"))
    revision = _final_revision(snapshot)
    problems = []
    for name in REQUIRED_PROJECTIONS:
        projection = _mapping(projections.get(name))
        if projection.get("revision") != revision:
            problems.append(f"{name} is missing or stale")
        if not projection.get("evidence_id"):
            problems.append(f"{name} lacks evidence")
    return _condition("status_projections_current", problems)


def _referenced_evidence_ids(value: object, *, parent_key: str = "") -> Set[str]:
    found: Set[str] = set()
    if isinstance(value, Mapping):
        for raw_key, item in value.items():
            key = str(raw_key)
            if key == "evidence_hashes":
                continue
            if key.endswith("evidence_id") and isinstance(item, str):
                found.add(item)
            elif key.endswith("evidence_ids"):
                found.update(str(entry) for entry in _sequence(item) if isinstance(entry, str))
            else:
                found.update(_referenced_evidence_ids(item, parent_key=key))
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for item in value:
            found.update(_referenced_evidence_ids(item, parent_key=parent_key))
    return found


def _evidence_complete(snapshot: Mapping[str, Any]) -> CompletionCondition:
    evidence = _mapping(snapshot.get("evidence_hashes"))
    references = _referenced_evidence_ids(snapshot)
    problems = []
    for evidence_id in sorted(references):
        digest = evidence.get(evidence_id)
        if not isinstance(digest, str) or not _HASH.fullmatch(digest):
            problems.append(f"{evidence_id} has no valid content hash")
    for evidence_id, digest in sorted(evidence.items()):
        if not isinstance(evidence_id, str) or not evidence_id:
            problems.append("evidence contains an invalid identifier")
        if not isinstance(digest, str) or not _HASH.fullmatch(digest):
            problems.append(f"{evidence_id} has an invalid content hash")
    if not references:
        problems.append("no evidence is referenced")
    return _condition("evidence_hashes_complete", problems)


def evaluate_completion(snapshot: Mapping[str, Any]) -> CompletionResult:
    """Evaluate every completion condition without trusting lifecycle labels."""
    if not isinstance(snapshot, Mapping):
        raise CompletionEvidenceError("completion snapshot must be an object")
    _canonical(snapshot)
    conditions = (
        _required_work(snapshot),
        _queue_drained(snapshot),
        _mandatory_resolved(snapshot),
        _active_leases_cleared(snapshot),
        _worker_reports_disposed(snapshot),
        _validation_gates(snapshot),
        _boolean_condition(
            snapshot,
            "journal_reconstructs",
            "journal",
            ("verified", "projection_rebuilt", "rebuild_matches"),
        ),
        _git_state_matches(snapshot),
        _boolean_condition(snapshot, "budgets_reconciled", "budgets", ("reconciled",)),
        _runtime_reconciled(snapshot),
        _residuals(snapshot),
        _memory_dispositions_terminal(snapshot),
        _sha_parity(snapshot),
        _boolean_condition(
            snapshot,
            "plugin_verified",
            "plugin",
            ("source_cache_parity", "fresh_context_smoke"),
        ),
        _projections(snapshot),
        _evidence_complete(snapshot),
    )
    return CompletionResult(
        all(item.passed for item in conditions), conditions, advisory_ids=_advisory_ids(snapshot)
    )


def create_completion_manifest(snapshot: Mapping[str, Any]) -> Dict[str, object]:
    """Create a deterministic content-addressed manifest only for real completion."""
    result = evaluate_completion(snapshot)
    if not result.complete:
        failed = ", ".join(result.failed_conditions)
        raise CompletionEvidenceError(f"completion predicate failed: {failed}")
    frozen_snapshot = json.loads(_canonical(snapshot))
    payload: Dict[str, object] = {
        "schema_version": 1,
        "manifest_type": "memwiki.autonomous-coordinator-completion",
        "run_id": snapshot.get("run_id"),
        "objective": snapshot.get("objective"),
        "complete": True,
        "disposition": result.disposition,
        "advisory_ids": list(result.advisory_ids),
        "conditions": [item.to_dict() for item in result.conditions],
        "evidence_snapshot": frozen_snapshot,
    }
    payload["manifest_hash"] = _digest(payload)
    return payload


def verify_completion_manifest(manifest: Mapping[str, Any]) -> CompletionResult:
    """Verify content integrity and independently reconstruct the predicate result."""
    if not isinstance(manifest, Mapping):
        raise CompletionEvidenceError("completion manifest must be an object")
    supplied_hash = manifest.get("manifest_hash")
    unhashed = dict(manifest)
    unhashed.pop("manifest_hash", None)
    if not isinstance(supplied_hash, str) or supplied_hash != _digest(unhashed):
        raise CompletionEvidenceError("manifest hash mismatch")
    snapshot = _mapping(manifest.get("evidence_snapshot"))
    reconstructed = evaluate_completion(snapshot)
    expected_conditions = [item.to_dict() for item in reconstructed.conditions]
    if not reconstructed.complete or manifest.get("complete") is not True:
        raise CompletionEvidenceError("manifest does not reconstruct completion")
    if manifest.get("conditions") != expected_conditions:
        raise CompletionEvidenceError("manifest condition results do not reconstruct")
    if manifest.get("run_id") != snapshot.get("run_id") or manifest.get("objective") != snapshot.get("objective"):
        raise CompletionEvidenceError("manifest identity does not match evidence")
    if manifest.get("disposition") != reconstructed.disposition:
        raise CompletionEvidenceError("manifest disposition does not reconstruct")
    if manifest.get("advisory_ids") != list(reconstructed.advisory_ids):
        raise CompletionEvidenceError("manifest advisories do not reconstruct")
    return reconstructed
