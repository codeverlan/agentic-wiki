"""Deterministic interruption and recovery exercise for coordinator refinements.

This is deliberately a harness, not a second coordinator implementation.  It
drives the public journal gateway, packet contract, slice leases, and completion
predicate through the failure sequence a refinement must survive.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Tuple

from .coordinator_assignments import AssignmentEnvelope, BoundedContextManifest, ReportContract
from .coordinator_commands import CoordinatorCommandGateway
from .coordinator_completion import (
    REQUIRED_PROJECTIONS,
    REQUIRED_VALIDATION_GATES,
    CompletionResult,
    evaluate_completion,
)
from .coordinator_slice_leases import ReportAdmission, SliceLease, SliceLeaseAuthority
from .coordinator_worker_packets import (
    AcceptanceContract,
    AssignmentPacket,
    ContextEntry,
    ContextPacket,
    MemoryDelta,
    PacketAdmission,
    PrivacyAttestation,
    ResultPacket,
    Route,
    ValidationReceipt,
    WorkerReportPacket,
)


def _sha(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class CoordinatorRefinementResult:
    """Observable evidence from the canonical interruption/recovery scenario."""

    run_id: str
    event_count: int
    replay_revision: int
    old_lease: SliceLease
    replacement_lease: SliceLease
    late_admission: ReportAdmission
    current_admission: ReportAdmission
    duplicate_report_appended: bool
    independent_slice_reported: bool
    refusal: CompletionResult
    completion: CompletionResult


class CoordinatorRefinementHarness:
    """Execute one real recovery sequence against a workspace-local journal."""

    def __init__(self, workspace: Path | str, *, now: datetime | None = None) -> None:
        self.workspace = Path(workspace).expanduser().resolve()
        self.now = now or datetime(2026, 7, 12, 16, 0, tzinfo=timezone.utc)
        if self.now.tzinfo is None or self.now.utcoffset() is None:
            raise ValueError("now must be timezone-aware")

    def run(self, *, run_id: str = "refinement-recovery") -> CoordinatorRefinementResult:
        gateway = CoordinatorCommandGateway(self.workspace)
        leases = SliceLeaseAuthority()
        moment = self.now

        def record(event_type: str, payload: Dict[str, Any], key: str) -> bool:
            nonlocal moment
            result = gateway.append(
                run_id=run_id,
                event_type=event_type,
                payload=payload,
                actor={"type": "coordinator", "id": "recovery-harness"},
                idempotency_key=f"{run_id}:{key}",
                occurred_at=moment.isoformat(),
            )
            moment += timedelta(seconds=1)
            return result.appended

        record("run.started", {"status": "active", "max_workers": 2}, "start")
        for slice_id, path in (("refine", "src/memwiki/refinement.py"), ("independent", "tests/test_independent.py")):
            record(
                "slice.proposed",
                {
                    "slice_id": slice_id,
                    "title": slice_id,
                    "status": "ready",
                    "depends_on": [],
                    "owned_paths": [path],
                    "required": True,
                },
                f"slice:{slice_id}",
            )

        old_assignment = self._assignment("refine-old", "refine", "worker-old", "src/memwiki/refinement.py", moment)
        old_lease = leases.acquire(
            old_assignment, attempt=1, coordinator_fencing_token=1, now=moment, ttl=timedelta(seconds=20)
        )
        old_lease = leases.acknowledge(
            old_assignment.assignment_id,
            worker_id=old_assignment.worker_id,
            lease_token=old_lease.lease_token,
            coordinator_fencing_token=1,
            now=moment,
        )
        record(
            "slice.assigned",
            {"slice_id": "refine", "assignment_id": old_assignment.assignment_id, "attempt": 1, "status": "active"},
            "assign:refine:1",
        )
        record(
            "worker.started",
            {"worker_id": "worker-old", "slice_id": "refine", "assignment_id": old_assignment.assignment_id},
            "worker:old:start",
        )
        record("worker.heartbeat", {"worker_id": "worker-old", "slice_id": "refine"}, "worker:old:heartbeat")
        record("run.status_changed", {"status": "interrupted", "reason": "coordinator process exited"}, "interrupted")
        record("runtime.observed", {"checkpoint": "before-recovery", "slice_leases": leases.to_dict()}, "checkpoint")

        moment += timedelta(seconds=20)
        assert old_assignment.assignment_id in leases.expire(now=moment)
        record(
            "slice.transitioned", {"slice_id": "refine", "status": "stale", "reason": "lease expired"}, "refine:stale"
        )
        record("run.status_changed", {"status": "recovering", "reason": "journal replay verified"}, "recovering")
        replacement = self._assignment("refine-new", "refine", "worker-new", "src/memwiki/refinement.py", moment)
        replacement_lease = leases.takeover(
            replacement, attempt=2, coordinator_fencing_token=2, now=moment, ttl=timedelta(seconds=20)
        )
        replacement_lease = leases.acknowledge(
            replacement.assignment_id,
            worker_id=replacement.worker_id,
            lease_token=replacement_lease.lease_token,
            coordinator_fencing_token=2,
            now=moment,
        )
        record(
            "slice.assigned",
            {"slice_id": "refine", "assignment_id": replacement.assignment_id, "attempt": 2, "status": "active"},
            "assign:refine:2",
        )
        record(
            "worker.started",
            {"worker_id": "worker-new", "slice_id": "refine", "assignment_id": replacement.assignment_id},
            "worker:new:start",
        )

        late_packet = self._packet_chain(old_assignment, "old")
        assert PacketAdmission().admit(*late_packet).admitted
        late_admission = leases.admit_report(
            old_assignment.assignment_id,
            worker_id=old_assignment.worker_id,
            lease_token=old_lease.lease_token,
            coordinator_fencing_token=1,
            attempt=1,
            assignment_hash=old_assignment.assignment_hash,
            received_at=moment,
        )
        record(
            "worker.reported",
            {
                "report_id": late_packet[-1].report_id,
                "worker_id": "worker-old",
                "slice_id": "refine",
                "disposition": "superseded",
                "evidence_id": "late-report",
            },
            "report:late",
        )

        independent = self._assignment(
            "independent-1", "independent", "worker-independent", "tests/test_independent.py", moment
        )
        independent_lease = leases.acquire(
            independent, attempt=1, coordinator_fencing_token=2, now=moment, ttl=timedelta(seconds=20)
        )
        independent_lease = leases.acknowledge(
            independent.assignment_id,
            worker_id=independent.worker_id,
            lease_token=independent_lease.lease_token,
            coordinator_fencing_token=2,
            now=moment,
        )
        record(
            "slice.assigned",
            {"slice_id": "independent", "assignment_id": independent.assignment_id, "attempt": 1, "status": "active"},
            "assign:independent",
        )
        independent_packet = self._packet_chain(independent, "independent")
        independent_admission = leases.admit_report(
            independent.assignment_id,
            worker_id=independent.worker_id,
            lease_token=independent_lease.lease_token,
            coordinator_fencing_token=2,
            attempt=1,
            assignment_hash=independent.assignment_hash,
            received_at=moment,
        )
        assert PacketAdmission().admit(*independent_packet).admitted and independent_admission.integrable
        record(
            "worker.reported",
            {
                "report_id": independent_packet[-1].report_id,
                "worker_id": "worker-independent",
                "slice_id": "independent",
                "disposition": "integrated",
                "evidence_id": "independent-report",
            },
            "report:independent",
        )
        duplicate_report_appended = record(
            "worker.reported",
            {
                "report_id": independent_packet[-1].report_id,
                "worker_id": "worker-independent",
                "slice_id": "independent",
                "disposition": "integrated",
                "evidence_id": "independent-report",
            },
            "report:independent",
        )
        leases.release(
            independent.assignment_id,
            worker_id=independent.worker_id,
            lease_token=independent_lease.lease_token,
            coordinator_fencing_token=2,
            now=moment,
        )
        record(
            "blocker.recorded",
            {"id": "refine-awaiting-replacement", "slice_id": "refine", "status": "open", "mandatory": True},
            "blocker:refine",
        )

        refusal = evaluate_completion(self._completion_snapshot(run_id, complete=False, leases=leases.leases))
        current_packet = self._packet_chain(replacement, "replacement")
        assert PacketAdmission().admit(*current_packet).admitted
        current_admission = leases.admit_report(
            replacement.assignment_id,
            worker_id=replacement.worker_id,
            lease_token=replacement_lease.lease_token,
            coordinator_fencing_token=2,
            attempt=2,
            assignment_hash=replacement.assignment_hash,
            received_at=moment,
        )
        record(
            "worker.reported",
            {
                "report_id": current_packet[-1].report_id,
                "worker_id": "worker-new",
                "slice_id": "refine",
                "disposition": "integrated",
                "evidence_id": "replacement-report",
            },
            "report:replacement",
        )
        leases.release(
            replacement.assignment_id,
            worker_id=replacement.worker_id,
            lease_token=replacement_lease.lease_token,
            coordinator_fencing_token=2,
            now=moment,
        )
        record("slice.transitioned", {"slice_id": "refine", "status": "integrated"}, "refine:integrated")
        record("run.status_changed", {"status": "active", "reason": "recovery complete"}, "resumed")

        gateway.cache.path.unlink()
        replay = gateway.status(run_id=run_id)
        completion = evaluate_completion(self._completion_snapshot(run_id, complete=True, leases=leases.leases))
        return CoordinatorRefinementResult(
            run_id,
            len(gateway.journal.read_events(run_id=run_id)),
            replay.revision,
            old_lease,
            replacement_lease,
            late_admission,
            current_admission,
            duplicate_report_appended,
            independent_admission.integrable,
            refusal,
            completion,
        )

    def _assignment(
        self, assignment_id: str, slice_id: str, worker_id: str, path: str, issued_at: datetime
    ) -> AssignmentEnvelope:
        return AssignmentEnvelope.create(
            assignment_id=assignment_id,
            slice_id=slice_id,
            worker_id=worker_id,
            context=BoundedContextManifest.create(
                artifacts=("recovery-plan",), context_hashes=("sha256:plan",), token_budget=100
            ),
            owned_paths=(path,),
            forbidden_paths=("wiki",),
            base_revision="a" * 40,
            required_tests=("uv run pytest",),
            permissions=("write_owned_paths",),
            lease_policy="acknowledge-before-write",
            report_contract=ReportContract(
                destination=f"reports/{assignment_id}.json", required_fields=("tests",), schema_version=1
            ),
            issued_at=issued_at,
            acceptance_deadline=issued_at + timedelta(minutes=5),
        )

    def _packet_chain(
        self, assignment: AssignmentEnvelope, suffix: str
    ) -> Tuple[AssignmentPacket, ContextPacket, ResultPacket, WorkerReportPacket]:
        privacy = PrivacyAttestation.create(data_profile="no", local_only=True, synthetic_only=False)
        packet = AssignmentPacket.create(
            assignment_id=assignment.assignment_id,
            worker_id=assignment.worker_id,
            owned_paths=assignment.owned_paths,
            acceptance=AcceptanceContract.create(criteria=("tests pass",), required_validations=("uv run pytest",)),
            route=Route.create(channel="coordinator", destination=assignment.report_contract.destination),
            base_revision=assignment.base_revision,
            privacy=privacy,
        )
        context = ContextPacket.create(
            context_id=f"context-{suffix}",
            assignment_id=packet.assignment_id,
            assignment_hash=packet.assignment_hash,
            entries=(ContextEntry.create(source_id="recovery", revision="1", content_hash=_sha(f"context-{suffix}")),),
            privacy=privacy,
        )
        result = ResultPacket.create(
            result_id=f"result-{suffix}",
            assignment_id=packet.assignment_id,
            assignment_hash=packet.assignment_hash,
            context_id=context.context_id,
            context_hash=context.context_hash,
            base_revision=packet.base_revision,
            result_revision="b" * 40,
            changed_paths={assignment.owned_paths[0]: _sha(f"change-{suffix}")},
            validations=(
                ValidationReceipt.create(
                    command="uv run pytest", outcome="passed", evidence_hash=_sha(f"test-{suffix}")
                ),
            ),
            privacy=privacy,
            memory_delta=MemoryDelta.not_applicable("No durable memory change."),
        )
        report = WorkerReportPacket.create(
            report_id=f"report-{suffix}",
            assignment_id=packet.assignment_id,
            assignment_hash=packet.assignment_hash,
            context_id=context.context_id,
            context_hash=context.context_hash,
            result_id=result.result_id,
            result_hash=result.result_hash,
            route=packet.route,
            acceptance_status="accepted",
            privacy=privacy,
            memory_delta=result.memory_delta,
            summary="Recovery harness result.",
        )
        return packet, context, result, report

    def _completion_snapshot(self, run_id: str, *, complete: bool, leases: Iterable[SliceLease]) -> Dict[str, Any]:
        revision = "recovery-final"

        def evidence(label: str) -> Dict[str, str]:
            return {"evidence_id": label}

        snapshot: Dict[str, Any] = {
            "schema_version": 1,
            "run_id": run_id,
            "objective": "refinement recovery",
            "authority_evidence_id": "authority",
            "initial_git": {"revision": "initial", **evidence("initial")},
            "final_git": {"revision": revision, "local_matches": True, "remote_matches": True, **evidence("final")},
            "current_revision": revision,
            "queue_digest": _sha("queue"),
            "slices": {
                "refine": {
                    "required": True,
                    "disposition": "integrated" if complete else "blocked",
                    "integrated_revision": revision,
                    "report_evidence_id": "refine-report",
                    "integration_evidence_id": "refine-integration",
                    "evaluation_passed": complete,
                    "evaluation_revision": revision,
                    "evaluation_evidence_id": "refine-evaluation",
                },
                "independent": {
                    "required": True,
                    "disposition": "integrated",
                    "integrated_revision": revision,
                    "report_evidence_id": "independent-report",
                    "integration_evidence_id": "independent-integration",
                    "evaluation_passed": True,
                    "evaluation_revision": revision,
                    "evaluation_evidence_id": "independent-evaluation",
                },
            },
            "queue": {
                "ready": [] if complete else ["refine"],
                "active": [],
                "stale": [],
                "waiting_resolvable": [],
                "undisposed_reports": [],
                "uncertain_commands": [],
                "reachable_discoveries": [],
            },
            "blockers": [
                {"id": "refine-awaiting-replacement", "status": "resolved" if complete else "open", "mandatory": True}
            ],
            "supervision": [],
            "validation_gates": {
                name: {"passed": True, "revision": revision, **evidence(f"gate-{name}")}
                for name in REQUIRED_VALIDATION_GATES
            },
            "journal": {"verified": True, "projection_rebuilt": True, "rebuild_matches": True, **evidence("journal")},
            "budgets": {"reconciled": True, **evidence("budgets")},
            "residuals": {
                "credentials": 0,
                "phi_canaries": 0,
                "worker_processes": 0,
                "leases": 0,
                "locks": 0,
                "temporary_worktrees": 0,
                "unbounded_artifacts": 0,
                **evidence("residuals"),
            },
            "plugin": {"source_cache_parity": True, "fresh_context_smoke": True, **evidence("plugin")},
            "projections": {
                name: {"revision": revision, **evidence(f"projection-{name}")} for name in REQUIRED_PROJECTIONS
            },
            "resources": evidence("resources"),
            "commits": [{"revision": revision, **evidence("commit")}],
            "incidents": [],
            "repairs": [],
            "leases": [lease.to_dict() for lease in leases],
            "worker_reports": [
                {"report_id": "report-old", "disposition": "superseded", "evidence_id": "late-report"},
                {
                    "report_id": "report-independent",
                    "disposition": "integrated",
                    "revision": revision,
                    "evidence_id": "independent-report",
                },
                {
                    "report_id": "report-replacement",
                    "disposition": "integrated",
                    "revision": revision,
                    "evidence_id": "replacement-report",
                },
            ],
            "resume_instruction": "No resume required.",
            "evidence_hashes": {},
        }
        references = self._evidence_ids(snapshot)
        snapshot["evidence_hashes"] = {item: _sha(item) for item in references}
        return snapshot

    @staticmethod
    def _evidence_ids(value: object) -> Tuple[str, ...]:
        found: set[str] = set()
        if isinstance(value, Mapping):
            for key, item in value.items():
                if str(key).endswith("evidence_id") and isinstance(item, str):
                    found.add(item)
                else:
                    found.update(CoordinatorRefinementHarness._evidence_ids(item))
        elif isinstance(value, (list, tuple)):
            for item in value:
                found.update(CoordinatorRefinementHarness._evidence_ids(item))
        return tuple(sorted(found))


__all__ = ["CoordinatorRefinementHarness", "CoordinatorRefinementResult"]
