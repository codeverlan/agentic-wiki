from __future__ import annotations

import hashlib
import json
import sys
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from memwiki.coordinator_assignments import (
    AssignmentEnvelope,
    BoundedContextManifest,
    ReportContract,
)
from memwiki.coordinator_slice_leases import SliceLeaseAuthority
from memwiki.coordinator_workers import (
    DeterministicWorkerExecutor,
    LocalSubprocessWorkerExecutor,
    WorkerDispatch,
    WorkerError,
    WorkerOutcome,
)

NOW = datetime(2026, 7, 11, 12, tzinfo=timezone.utc)


def assignment(*, worker_id: str = "worker-1") -> AssignmentEnvelope:
    return AssignmentEnvelope.create(
        assignment_id="assignment-1",
        slice_id="AC-013",
        worker_id=worker_id,
        context=BoundedContextManifest.create(
            artifacts=("context/spec.html",),
            context_hashes=(f"sha256:{hashlib.sha256(b'spec').hexdigest()}",),
            token_budget=500,
        ),
        owned_paths=("src/owned.py", "reports/worker.json"),
        forbidden_paths=("src/shared.py",),
        base_revision="abc123",
        required_tests=("pytest",),
        permissions=("read", "write"),
        lease_policy="heartbeat",
        report_contract=ReportContract(
            destination="reports/worker.json",
            required_fields=("status", "summary"),
            schema_version=1,
        ),
        issued_at=NOW,
        acceptance_deadline=NOW + timedelta(minutes=1),
    )


def active_dispatch() -> WorkerDispatch:
    envelope = assignment()
    authority = SliceLeaseAuthority()
    offered = authority.acquire(
        envelope,
        attempt=1,
        coordinator_fencing_token=3,
        now=NOW,
        ttl=timedelta(minutes=5),
    )
    lease = authority.acknowledge(
        envelope.assignment_id,
        worker_id=envelope.worker_id,
        lease_token=offered.lease_token,
        coordinator_fencing_token=3,
        now=NOW,
    )
    return WorkerDispatch(envelope, lease, NOW + timedelta(minutes=2))


def test_deterministic_executor_conforms_to_dispatch_poll_collect_and_cancel() -> None:
    dispatch = active_dispatch()
    executor = DeterministicWorkerExecutor(
        {dispatch.assignment.assignment_id: [WorkerOutcome.success({"summary": "done"})]}
    )

    handle = executor.dispatch(dispatch, now=NOW)
    assert executor.poll(handle, now=NOW).state == "finished"
    outcome = executor.collect(handle, now=NOW)
    assert outcome.kind == "success"
    assert outcome.report == {"summary": "done"}
    assert executor.collect(handle, now=NOW) == outcome

    other = active_dispatch()
    executor2 = DeterministicWorkerExecutor()
    cancelled_handle = executor2.dispatch(other, now=NOW)
    assert executor2.cancel(cancelled_handle, now=NOW, reason="stop").kind == "retryable"
    assert executor2.collect(cancelled_handle, now=NOW).reason == "stop"


@pytest.mark.parametrize("kind", ["success", "retryable", "blocked", "supervision", "unavailable", "malformed"])
def test_all_provider_neutral_outcomes_round_trip(kind: str) -> None:
    outcome = WorkerOutcome(kind=kind, reason="reason", report={"value": 1})
    assert WorkerOutcome.from_dict(outcome.to_dict()) == outcome


def test_dispatch_rejects_unacknowledged_expired_or_mismatched_lease() -> None:
    envelope = assignment()
    authority = SliceLeaseAuthority()
    offered = authority.acquire(
        envelope,
        attempt=1,
        coordinator_fencing_token=1,
        now=NOW,
        ttl=timedelta(seconds=5),
    )
    executor = DeterministicWorkerExecutor()

    with pytest.raises(WorkerError, match="acknowledged"):
        executor.dispatch(WorkerDispatch(envelope, offered, NOW + timedelta(minutes=1)), now=NOW)

    active = authority.acknowledge(
        envelope.assignment_id,
        worker_id=envelope.worker_id,
        lease_token=offered.lease_token,
        coordinator_fencing_token=1,
        now=NOW,
    )
    with pytest.raises(WorkerError, match="expired"):
        executor.dispatch(
            WorkerDispatch(envelope, active, NOW + timedelta(minutes=1)),
            now=NOW + timedelta(seconds=6),
        )
    with pytest.raises(WorkerError, match="assignment hash"):
        executor.dispatch(
            WorkerDispatch(
                replace(envelope, assignment_hash="sha256:other"),
                active,
                NOW + timedelta(seconds=4),
            ),
            now=NOW,
        )


def test_fake_models_no_ack_timeout_and_crash_without_host_dependencies() -> None:
    dispatch = active_dispatch()
    no_ack = DeterministicWorkerExecutor(acknowledge=False)
    handle = no_ack.dispatch(dispatch, now=NOW)
    assert no_ack.poll(handle, now=NOW).state == "awaiting_acknowledgement"
    assert no_ack.poll(handle, now=dispatch.deadline).state == "finished"
    assert no_ack.collect(handle, now=dispatch.deadline).reason == "worker did not acknowledge"

    timeout = DeterministicWorkerExecutor()
    timeout_handle = timeout.dispatch(dispatch, now=NOW)
    assert timeout.poll(timeout_handle, now=dispatch.deadline).state == "finished"
    assert timeout.collect(timeout_handle, now=dispatch.deadline).reason == "worker timed out"

    crash = DeterministicWorkerExecutor({dispatch.assignment.assignment_id: [RuntimeError("lost worker")]})
    crash_handle = crash.dispatch(dispatch, now=NOW)
    assert crash.collect(crash_handle, now=NOW).kind == "retryable"
    assert crash.collect(crash_handle, now=NOW).reason == "worker crashed"


def test_local_subprocess_collects_json_report(tmp_path: Path) -> None:
    dispatch = active_dispatch()
    (tmp_path / "context").mkdir()
    (tmp_path / "context/spec.html").write_text("spec", encoding="utf-8")
    command = (
        sys.executable,
        "-c",
        "import json; print(json.dumps({'status':'success','summary':'ok'}))",
    )
    executor = LocalSubprocessWorkerExecutor(tmp_path, command)

    handle = executor.dispatch(dispatch, now=NOW)
    outcome = executor.collect(handle, now=NOW + timedelta(seconds=1))

    assert outcome.kind == "success"
    assert outcome.report == {"status": "success", "summary": "ok"}


def test_local_subprocess_rejects_missing_context_and_unowned_edits(tmp_path: Path) -> None:
    dispatch = active_dispatch()
    executor = LocalSubprocessWorkerExecutor(tmp_path, (sys.executable, "-c", "print('{}')"))
    with pytest.raises(WorkerError, match="context artifact"):
        executor.dispatch(dispatch, now=NOW)

    (tmp_path / "context").mkdir()
    (tmp_path / "context/spec.html").write_text("spec", encoding="utf-8")
    command = (
        sys.executable,
        "-c",
        "from pathlib import Path; Path('outside.txt').write_text('bad'); print('{}')",
    )
    executor = LocalSubprocessWorkerExecutor(tmp_path, command)
    outcome = executor.collect(executor.dispatch(dispatch, now=NOW), now=NOW)
    assert outcome.kind == "malformed"
    assert outcome.reason == "worker changed paths outside assignment ownership"
    assert outcome.details["changed_paths"] == ["outside.txt"]


def test_local_subprocess_rejects_malformed_output_and_can_cancel(tmp_path: Path) -> None:
    dispatch = active_dispatch()
    (tmp_path / "context").mkdir()
    (tmp_path / "context/spec.html").write_text("spec", encoding="utf-8")
    malformed = LocalSubprocessWorkerExecutor(tmp_path, (sys.executable, "-c", "print('not json')"))
    assert malformed.collect(malformed.dispatch(dispatch, now=NOW), now=NOW).kind == "malformed"

    sleeping = LocalSubprocessWorkerExecutor(tmp_path, (sys.executable, "-c", "import time; time.sleep(30)"))
    handle = sleeping.dispatch(dispatch, now=NOW)
    cancelled = sleeping.cancel(handle, now=NOW, reason="requested")
    assert cancelled.kind == "retryable"
    assert sleeping.poll(handle, now=NOW).state == "finished"


def test_worker_outcome_rejects_unknown_kind_and_unbounded_payload() -> None:
    with pytest.raises(ValueError, match="kind"):
        WorkerOutcome(kind="other", reason=None, report=None)
    with pytest.raises(ValueError, match="bounded"):
        WorkerOutcome(kind="success", reason=None, report={"blob": "x" * 70_000})
    with pytest.raises(ValueError, match="malformed"):
        WorkerOutcome.from_dict(json.loads('{"kind":"success"}'))
