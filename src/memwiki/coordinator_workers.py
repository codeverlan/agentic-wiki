from __future__ import annotations

import hashlib
import json
import os
import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Dict, Mapping, Optional, Protocol, Sequence, Union

from memwiki.coordinator_assignments import AssignmentEnvelope
from memwiki.coordinator_slice_leases import SliceLease

OUTCOME_KINDS = {
    "success",
    "retryable",
    "blocked",
    "supervision",
    "unavailable",
    "malformed",
}
MAX_OUTCOME_BYTES = 65_536


class WorkerError(RuntimeError):
    """A worker operation violates the provider-neutral execution contract."""


def _aware(value: datetime, label: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware")
    return value


def _confined(root: Path, relative: str) -> Path:
    path = PurePosixPath(relative)
    if path.is_absolute() or not path.parts or ".." in path.parts:
        raise WorkerError("worker path must be a confined relative path")
    candidate = root.joinpath(*path.parts)
    try:
        candidate.resolve(strict=False).relative_to(root.resolve())
    except ValueError as exc:
        raise WorkerError("worker path escapes the workspace") from exc
    return candidate


def _overlap(left: str, right: str) -> bool:
    left_parts = tuple(part.casefold() for part in PurePosixPath(left).parts)
    right_parts = tuple(part.casefold() for part in PurePosixPath(right).parts)
    common = min(len(left_parts), len(right_parts))
    return left_parts[:common] == right_parts[:common]


@dataclass(frozen=True)
class WorkerOutcome:
    kind: str
    reason: Optional[str]
    report: Optional[Mapping[str, object]]
    details: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.kind not in OUTCOME_KINDS:
            raise ValueError("worker outcome kind is invalid")
        if self.reason is not None and (not isinstance(self.reason, str) or not self.reason):
            raise ValueError("worker outcome reason must be null or a non-empty string")
        if self.report is not None and not isinstance(self.report, Mapping):
            raise ValueError("worker outcome report must be an object or null")
        if not isinstance(self.details, Mapping):
            raise ValueError("worker outcome details must be an object")
        try:
            encoded = json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":")).encode()
        except (TypeError, ValueError) as exc:
            raise ValueError("worker outcome must be JSON serializable") from exc
        if len(encoded) > MAX_OUTCOME_BYTES:
            raise ValueError("worker outcome must be bounded")

    @classmethod
    def success(cls, report: Mapping[str, object]) -> "WorkerOutcome":
        return cls("success", None, report)

    def to_dict(self) -> Dict[str, object]:
        return {
            "kind": self.kind,
            "reason": self.reason,
            "report": dict(self.report) if self.report is not None else None,
            "details": dict(self.details),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> "WorkerOutcome":
        if set(payload) != {"kind", "reason", "report", "details"}:
            raise ValueError("malformed worker outcome")
        report = payload["report"]
        details = payload["details"]
        if report is not None and not isinstance(report, Mapping):
            raise ValueError("malformed worker outcome report")
        if not isinstance(details, Mapping):
            raise ValueError("malformed worker outcome details")
        reason = payload["reason"]
        return cls(
            kind=str(payload["kind"]),
            reason=None if reason is None else str(reason),
            report=report,
            details=details,
        )


@dataclass(frozen=True)
class WorkerDispatch:
    assignment: AssignmentEnvelope
    lease: SliceLease
    deadline: datetime

    def __post_init__(self) -> None:
        _aware(self.deadline, "worker deadline")


@dataclass(frozen=True)
class WorkerHandle:
    handle_id: str
    assignment_id: str


@dataclass(frozen=True)
class WorkerStatus:
    state: str
    observed_at: datetime

    def __post_init__(self) -> None:
        if self.state not in {"awaiting_acknowledgement", "running", "finished"}:
            raise ValueError("worker state is invalid")
        _aware(self.observed_at, "observed_at")


class WorkerExecutor(Protocol):
    def dispatch(self, request: WorkerDispatch, *, now: datetime) -> WorkerHandle: ...

    def poll(self, handle: WorkerHandle, *, now: datetime) -> WorkerStatus: ...

    def heartbeat(self, handle: WorkerHandle, *, now: datetime) -> WorkerStatus: ...

    def collect(self, handle: WorkerHandle, *, now: datetime) -> WorkerOutcome: ...

    def cancel(self, handle: WorkerHandle, *, now: datetime, reason: str) -> WorkerOutcome: ...


def _validate_dispatch(request: WorkerDispatch, now: datetime) -> None:
    _aware(now, "now")
    assignment = request.assignment
    lease = request.lease
    if lease.status != "active" or lease.acknowledged_at is None:
        raise WorkerError("slice lease must be acknowledged before dispatch")
    if now >= lease.expires_at:
        raise WorkerError("slice lease expired before dispatch")
    if request.deadline <= now or request.deadline > lease.expires_at:
        raise WorkerError("worker deadline must be live and within the slice lease")
    if assignment.assignment_id != lease.assignment_id:
        raise WorkerError("assignment identity does not match slice lease")
    if assignment.assignment_hash != lease.assignment_hash:
        raise WorkerError("assignment hash does not match slice lease")
    if assignment.worker_id != lease.worker_id:
        raise WorkerError("assignment worker does not match slice lease")
    if assignment.slice_id != lease.slice_id:
        raise WorkerError("assignment slice does not match slice lease")
    if assignment.base_revision != lease.base_revision:
        raise WorkerError("assignment base revision does not match slice lease")
    if assignment.owned_paths != lease.owned_paths:
        raise WorkerError("assignment owned paths do not match slice lease")


ScriptedResult = Union[WorkerOutcome, BaseException]


@dataclass
class _FakeRun:
    request: WorkerDispatch
    acknowledged: bool
    outcome: Optional[WorkerOutcome]
    scripted: Optional[ScriptedResult]


class DeterministicWorkerExecutor:
    """Scriptable executor for scheduler tests with no host dependencies."""

    def __init__(
        self,
        scripts: Optional[Mapping[str, Sequence[ScriptedResult]]] = None,
        *,
        acknowledge: bool = True,
    ) -> None:
        self._scripts = {key: list(values) for key, values in (scripts or {}).items()}
        self._acknowledge = acknowledge
        self._runs: Dict[str, _FakeRun] = {}
        self._sequence = 0

    def dispatch(self, request: WorkerDispatch, *, now: datetime) -> WorkerHandle:
        _validate_dispatch(request, now)
        self._sequence += 1
        handle = WorkerHandle(f"fake-{self._sequence}", request.assignment.assignment_id)
        values = self._scripts.get(request.assignment.assignment_id, [])
        scripted = values.pop(0) if values else None
        self._runs[handle.handle_id] = _FakeRun(request, self._acknowledge, None, scripted)
        return handle

    def poll(self, handle: WorkerHandle, *, now: datetime) -> WorkerStatus:
        run = self._run(handle)
        _aware(now, "now")
        if run.outcome is not None:
            return WorkerStatus("finished", now)
        if not run.acknowledged and now < run.request.deadline:
            return WorkerStatus("awaiting_acknowledgement", now)
        if now >= run.request.deadline:
            reason = "worker did not acknowledge" if not run.acknowledged else "worker timed out"
            run.outcome = WorkerOutcome("retryable", reason, None)
            return WorkerStatus("finished", now)
        if run.scripted is not None:
            run.outcome = self._resolve(run.scripted)
            return WorkerStatus("finished", now)
        return WorkerStatus("running", now)

    def heartbeat(self, handle: WorkerHandle, *, now: datetime) -> WorkerStatus:
        return self.poll(handle, now=now)

    def collect(self, handle: WorkerHandle, *, now: datetime) -> WorkerOutcome:
        run = self._run(handle)
        self.poll(handle, now=now)
        if run.outcome is None:
            raise WorkerError("worker has not produced a terminal outcome")
        return run.outcome

    def cancel(self, handle: WorkerHandle, *, now: datetime, reason: str) -> WorkerOutcome:
        run = self._run(handle)
        _aware(now, "now")
        if not reason:
            raise ValueError("cancellation reason must be non-empty")
        if run.outcome is None:
            run.outcome = WorkerOutcome("retryable", reason, None, {"cancelled": True})
        return run.outcome

    def _run(self, handle: WorkerHandle) -> _FakeRun:
        run = self._runs.get(handle.handle_id)
        if run is None or run.request.assignment.assignment_id != handle.assignment_id:
            raise WorkerError("unknown worker handle")
        return run

    @staticmethod
    def _resolve(scripted: ScriptedResult) -> WorkerOutcome:
        if isinstance(scripted, BaseException):
            return WorkerOutcome("retryable", "worker crashed", None)
        return scripted


@dataclass
class _ProcessRun:
    request: WorkerDispatch
    process: subprocess.Popen[str]
    before: Mapping[str, str]
    outcome: Optional[WorkerOutcome]


class LocalSubprocessWorkerExecutor:
    """Reference adapter that executes a fixed argv in a confined workspace."""

    def __init__(self, workspace: Path, command: Sequence[str]) -> None:
        self._workspace = workspace.resolve()
        self._command = tuple(command)
        if not self._command or any(not isinstance(value, str) or not value for value in self._command):
            raise ValueError("worker command must be a non-empty argv")
        self._runs: Dict[str, _ProcessRun] = {}
        self._sequence = 0

    def dispatch(self, request: WorkerDispatch, *, now: datetime) -> WorkerHandle:
        _validate_dispatch(request, now)
        if not self._workspace.is_dir():
            raise WorkerError("worker workspace does not exist")
        context_bytes = 0
        for artifact, expected_hash in zip(
            request.assignment.context.artifacts,
            request.assignment.context.context_hashes,
        ):
            path = _confined(self._workspace, artifact)
            if not path.is_file() or path.is_symlink():
                raise WorkerError(f"context artifact is unavailable: {artifact}")
            content = path.read_bytes()
            context_bytes += len(content)
            actual_hash = f"sha256:{hashlib.sha256(content).hexdigest()}"
            if actual_hash != expected_hash:
                raise WorkerError(f"context artifact hash mismatch: {artifact}")
        if context_bytes > request.assignment.context.token_budget * 4:
            raise WorkerError("bounded context exceeds its token budget")
        before = self._snapshot()
        environment = {"PATH": os.environ.get("PATH", ""), "PYTHONUNBUFFERED": "1"}
        process = subprocess.Popen(
            self._command,
            cwd=self._workspace,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        self._sequence += 1
        handle = WorkerHandle(f"local-{self._sequence}", request.assignment.assignment_id)
        self._runs[handle.handle_id] = _ProcessRun(request, process, before, None)
        return handle

    def poll(self, handle: WorkerHandle, *, now: datetime) -> WorkerStatus:
        run = self._run(handle)
        _aware(now, "now")
        if run.outcome is not None:
            return WorkerStatus("finished", now)
        if run.process.poll() is not None:
            return WorkerStatus("finished", now)
        if now >= run.request.deadline:
            self.cancel(handle, now=now, reason="worker timed out")
            return WorkerStatus("finished", now)
        return WorkerStatus("running", now)

    def heartbeat(self, handle: WorkerHandle, *, now: datetime) -> WorkerStatus:
        return self.poll(handle, now=now)

    def collect(self, handle: WorkerHandle, *, now: datetime) -> WorkerOutcome:
        run = self._run(handle)
        if run.outcome is not None:
            return run.outcome
        if self.poll(handle, now=now).state != "finished":
            try:
                stdout, stderr = run.process.communicate(timeout=0.2)
            except subprocess.TimeoutExpired as exc:
                raise WorkerError("worker has not produced a terminal outcome") from exc
        else:
            stdout, stderr = run.process.communicate()
        if run.outcome is not None:
            return run.outcome
        changed = self._changed_paths(run.before, self._snapshot())
        unsafe_symlinks = [path for path in changed if _confined(self._workspace, path).is_symlink()]
        if unsafe_symlinks:
            run.outcome = WorkerOutcome(
                "malformed",
                "worker created or changed symbolic links",
                None,
                {"changed_paths": unsafe_symlinks},
            )
            return run.outcome
        unauthorized = [
            path for path in changed if not any(_overlap(path, owned) for owned in run.request.assignment.owned_paths)
        ]
        if unauthorized:
            run.outcome = WorkerOutcome(
                "malformed",
                "worker changed paths outside assignment ownership",
                None,
                {"changed_paths": unauthorized},
            )
            return run.outcome
        if run.process.returncode != 0:
            run.outcome = WorkerOutcome(
                "retryable", "worker process failed", None, {"returncode": run.process.returncode}
            )
            return run.outcome
        try:
            payload = json.loads(stdout)
        except json.JSONDecodeError:
            run.outcome = WorkerOutcome("malformed", "worker output is not valid JSON", None)
            return run.outcome
        if not isinstance(payload, dict):
            run.outcome = WorkerOutcome("malformed", "worker report must be a JSON object", None)
            return run.outcome
        kind = payload.get("status")
        if kind not in OUTCOME_KINDS:
            kind = (
                "success"
                if all(field in payload for field in run.request.assignment.report_contract.required_fields)
                else "malformed"
            )
        reason = None if kind == "success" else str(payload.get("reason", "malformed worker report"))
        details = {"stderr_sha256": hashlib.sha256(stderr.encode()).hexdigest()}
        run.outcome = WorkerOutcome(str(kind), reason, payload, details)
        return run.outcome

    def cancel(self, handle: WorkerHandle, *, now: datetime, reason: str) -> WorkerOutcome:
        run = self._run(handle)
        _aware(now, "now")
        if not reason:
            raise ValueError("cancellation reason must be non-empty")
        if run.outcome is None:
            if run.process.poll() is None:
                run.process.terminate()
                try:
                    run.process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    run.process.kill()
                    run.process.wait()
            run.outcome = WorkerOutcome("retryable", reason, None, {"cancelled": True})
        return run.outcome

    def _run(self, handle: WorkerHandle) -> _ProcessRun:
        run = self._runs.get(handle.handle_id)
        if run is None or run.request.assignment.assignment_id != handle.assignment_id:
            raise WorkerError("unknown worker handle")
        return run

    def _snapshot(self) -> Mapping[str, str]:
        result: Dict[str, str] = {}
        for path in sorted(self._workspace.rglob("*")):
            relative = path.relative_to(self._workspace).as_posix()
            if path.is_symlink():
                result[relative] = f"symlink:{os.readlink(path)}"
            elif path.is_file():
                result[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
        return result

    @staticmethod
    def _changed_paths(before: Mapping[str, str], after: Mapping[str, str]) -> list[str]:
        return sorted(path for path in set(before) | set(after) if before.get(path) != after.get(path))
