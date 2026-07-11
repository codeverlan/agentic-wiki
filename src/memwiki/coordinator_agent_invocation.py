from __future__ import annotations

import fcntl
import hashlib
import json
import os
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence

from memwiki.coordinator_agent_policy import QualificationReceipt, QualificationStatus
from memwiki.coordinator_agent_selection import AgentSelectionPlan, SelectionStatus
from memwiki.coordinator_agents import ExecutionMode, SurfaceKind
from memwiki.coordinator_supervision import (
    SupervisionCategory,
    SupervisionQueue,
)


class InvocationStatus(str, Enum):
    PLANNED = "planned"
    AUTHORIZED = "authorized"
    STARTED = "started"
    WAITING = "waiting"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    OUTCOME_UNKNOWN = "outcome_unknown"
    HANDOFF_REQUIRED = "handoff_required"


_TERMINAL = {
    InvocationStatus.SUCCEEDED,
    InvocationStatus.FAILED,
    InvocationStatus.CANCELLED,
    InvocationStatus.OUTCOME_UNKNOWN,
    InvocationStatus.HANDOFF_REQUIRED,
}
_CREDENTIAL_KEYS = {
    "access_token", "api_key", "authorization", "credential", "credentials",
    "password", "private_key", "refresh_token", "secret", "token",
}


def _canonical(value: object, label: str) -> bytes:
    try:
        return json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
        ).encode()
    except (TypeError, ValueError) as error:
        raise ValueError(f"{label} must be JSON serializable") from error


def _redact(value: object, secrets: Sequence[str], key: str = "") -> object:
    if key.lower().replace("-", "_") in _CREDENTIAL_KEYS:
        return "[REDACTED]"
    if isinstance(value, Mapping):
        return {str(item): _redact(value[item], secrets, str(item)) for item in sorted(value)}
    if isinstance(value, (list, tuple)):
        return [_redact(item, secrets) for item in value]
    if isinstance(value, str):
        result = value
        for secret in sorted((item for item in secrets if item), key=len, reverse=True):
            result = result.replace(secret, "[REDACTED]")
        return result
    if value is None or isinstance(value, (bool, int, float)):
        return value
    raise ValueError("receipt content must be JSON serializable")


def _stamp(value: Optional[datetime] = None) -> str:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None or current.utcoffset() is None:
        raise ValueError("timestamp must include timezone")
    return current.isoformat()


@dataclass(frozen=True)
class AgentInvocationRequest:
    idempotency_key: str
    slice_id: str
    operation: str
    surface_kind: SurfaceKind
    payload: Mapping[str, Any]
    metadata: Mapping[str, Any]
    correlation_id: str
    causation_id: str
    timeout_seconds: float
    max_output_bytes: int

    def __post_init__(self) -> None:
        values = (self.idempotency_key, self.slice_id, self.operation,
                  self.correlation_id, self.causation_id)
        if any(not item.strip() for item in values):
            raise ValueError("invocation identifiers must be non-empty")
        if self.timeout_seconds <= 0 or self.max_output_bytes <= 0:
            raise ValueError("invocation bounds must be positive")
        _canonical(self.payload, "payload")
        _canonical(self.metadata, "metadata")

    @property
    def input_hash(self) -> str:
        return "sha256:" + hashlib.sha256(_canonical(self.payload, "payload")).hexdigest()


@dataclass(frozen=True)
class AgentInvocationReceipt:
    idempotency_key: str
    request_hash: str
    slice_id: str
    operation: str
    agent_id: str
    stable_identity: str
    agent_version: str
    surface_id: str
    surface_kind: str
    surface_version: str
    qualification_receipt_id: str
    selection_plan_id: str
    input_hash: str
    correlation_id: str
    causation_id: str
    status: InvocationStatus
    planned_at: str
    authorized_at: Optional[str] = None
    started_at: Optional[str] = None
    terminal_at: Optional[str] = None
    output_hash: Optional[str] = None
    output: Optional[object] = None
    error: Optional[str] = None
    supervision_item_id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        value = asdict(self)
        value["status"] = self.status.value
        return value

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "AgentInvocationReceipt":
        expected = {item.name for item in cls.__dataclass_fields__.values()}
        if set(value) != expected:
            raise ValueError("invocation receipt fields do not match schema")
        return cls(**{**value, "status": InvocationStatus(str(value["status"]))})


@dataclass(frozen=True)
class AgentInvocationResult:
    outcome: str
    output: object = None
    error: Optional[str] = None

    @classmethod
    def failed(cls, error: str) -> "AgentInvocationResult":
        return cls("failed", error=error)

    @classmethod
    def timeout(cls) -> "AgentInvocationResult":
        return cls("timeout", error="invocation timed out")

    @classmethod
    def waiting(cls, output: object = None) -> "AgentInvocationResult":
        return cls("waiting", output=output)


@dataclass(frozen=True)
class AgentInvocationPacket:
    receipt: AgentInvocationReceipt


Executor = Callable[[Mapping[str, Any]], object]


class AgentInvocationProtocol:
    def __init__(self, path: Path, *, supervision: Optional[SupervisionQueue] = None) -> None:
        self.path = path
        self.lock_path = path.with_suffix(path.suffix + ".lock")
        self.supervision = supervision

    def plan(
        self,
        request: AgentInvocationRequest,
        qualification: QualificationReceipt,
        selection: AgentSelectionPlan,
        *,
        secret_values: Sequence[str] = (),
        recorded_at: Optional[datetime] = None,
    ) -> AgentInvocationReceipt:
        self._validate_authority(request, qualification, selection)
        request_hash = self._request_hash(request, qualification, selection)
        with self._locked():
            receipts = self._read()
            existing = receipts.get(request.idempotency_key)
            if existing is not None:
                if existing.request_hash != request_hash:
                    raise ValueError("idempotency key is already bound to another invocation")
                return existing
            receipt = AgentInvocationReceipt(
                idempotency_key=request.idempotency_key,
                request_hash=request_hash,
                slice_id=request.slice_id,
                operation=request.operation,
                agent_id=qualification.agent_id,
                stable_identity=qualification.stable_identity,
                agent_version=qualification.agent_version,
                surface_id=qualification.surface_id or "",
                surface_kind=request.surface_kind.value,
                surface_version=qualification.agent_version,
                qualification_receipt_id=qualification.receipt_id,
                selection_plan_id=selection.plan_id,
                input_hash=request.input_hash,
                correlation_id=request.correlation_id,
                causation_id=request.causation_id,
                status=InvocationStatus.PLANNED,
                planned_at=_stamp(recorded_at),
                output=_redact(request.metadata, secret_values),
            )
            receipts[request.idempotency_key] = receipt
            self._write(receipts)
            return receipt

    def authorize(self, key: str, at: Optional[datetime] = None) -> AgentInvocationReceipt:
        return self._transition(key, InvocationStatus.AUTHORIZED, authorized_at=_stamp(at))

    def start(self, key: str, at: Optional[datetime] = None) -> AgentInvocationReceipt:
        return self._transition(key, InvocationStatus.STARTED, started_at=_stamp(at))

    def invoke(
        self,
        request: AgentInvocationRequest,
        qualification: QualificationReceipt,
        selection: AgentSelectionPlan,
        executor: Executor,
        *,
        cancelled: Optional[Callable[[], bool]] = None,
        secret_values: Sequence[str] = (),
        recorded_at: Optional[datetime] = None,
    ) -> AgentInvocationPacket:
        receipt = self.plan(
            request, qualification, selection,
            secret_values=secret_values, recorded_at=recorded_at,
        )
        if receipt.status in _TERMINAL or receipt.status is InvocationStatus.WAITING:
            return AgentInvocationPacket(receipt)
        if qualification.execution_mode is ExecutionMode.HANDOFF_ONLY:
            return AgentInvocationPacket(self._handoff(receipt, recorded_at))
        if cancelled is not None and cancelled():
            return AgentInvocationPacket(
                self._transition(receipt.idempotency_key, InvocationStatus.CANCELLED,
                                 terminal_at=_stamp(recorded_at))
            )
        if receipt.status is InvocationStatus.PLANNED:
            receipt = self.authorize(receipt.idempotency_key, recorded_at)
        if receipt.status is InvocationStatus.AUTHORIZED:
            self.start(receipt.idempotency_key, recorded_at)
        try:
            raw = executor(request.payload)
        except Exception as error:
            safe = str(_redact(str(error), secret_values))
            return AgentInvocationPacket(
                self._transition(request.idempotency_key, InvocationStatus.OUTCOME_UNKNOWN,
                                 terminal_at=_stamp(recorded_at), error=safe)
            )
        result = raw if isinstance(raw, AgentInvocationResult) else AgentInvocationResult("succeeded", raw)
        return AgentInvocationPacket(self._finish(request, result, secret_values, recorded_at))

    def reconcile_interrupted(self) -> List[AgentInvocationReceipt]:
        changed = []
        with self._locked():
            receipts = self._read()
            for key in sorted(receipts):
                receipt = receipts[key]
                if receipt.status is InvocationStatus.STARTED:
                    updated = replace(receipt, status=InvocationStatus.OUTCOME_UNKNOWN,
                                      terminal_at=_stamp(), error="interrupted; outcome unknown")
                    receipts[key] = updated
                    changed.append(updated)
            if changed:
                self._write(receipts)
        return changed

    def _finish(self, request: AgentInvocationRequest, result: AgentInvocationResult,
                secrets: Sequence[str], at: Optional[datetime]) -> AgentInvocationReceipt:
        if result.outcome == "waiting":
            return self._transition(request.idempotency_key, InvocationStatus.WAITING)
        if result.outcome == "timeout":
            return self._transition(request.idempotency_key, InvocationStatus.OUTCOME_UNKNOWN,
                                    terminal_at=_stamp(at), error="invocation timed out")
        if result.outcome == "failed":
            error = str(_redact(result.error or "agent invocation failed", secrets))
            return self._transition(request.idempotency_key, InvocationStatus.FAILED,
                                    terminal_at=_stamp(at), error=error)
        safe = _redact(result.output, secrets)
        encoded = _canonical(safe, "output")
        bounded = safe if len(encoded) <= request.max_output_bytes else "[OUTPUT TRUNCATED]"
        return self._transition(
            request.idempotency_key, InvocationStatus.SUCCEEDED, terminal_at=_stamp(at),
            output=bounded, output_hash="sha256:" + hashlib.sha256(encoded).hexdigest(),
        )

    def _handoff(self, receipt: AgentInvocationReceipt,
                 at: Optional[datetime]) -> AgentInvocationReceipt:
        item_id = "agent-handoff-" + receipt.request_hash.removeprefix("sha256:")[:16]
        if self.supervision is not None:
            self.supervision.open(
                item_id=item_id, category=SupervisionCategory.AUTHORIZATION,
                summary=f"Complete conversational handoff to {receipt.agent_id}",
                choices=("complete handoff", "cancel handoff"),
                evidence_ids=(receipt.qualification_receipt_id, receipt.selection_plan_id),
                affected_slice_ids=(receipt.slice_id,), resumable=True,
                created_at=at or datetime.now(timezone.utc),
            )
        return self._transition(receipt.idempotency_key, InvocationStatus.HANDOFF_REQUIRED,
                                terminal_at=_stamp(at), supervision_item_id=item_id, output=None)

    @staticmethod
    def _validate_authority(request: AgentInvocationRequest,
                            receipt: QualificationReceipt,
                            selection: AgentSelectionPlan) -> None:
        if receipt.status is QualificationStatus.REJECTED or receipt.execution_mode is None:
            raise ValueError("agent is not qualified for invocation")
        if selection.status is not SelectionStatus.SELECTED:
            raise ValueError("agent selection plan has no selected agent")
        if selection.selected_agent_id != receipt.agent_id:
            raise ValueError("selected agent does not match qualification")
        if selection.slice_id != request.slice_id or selection.operation != request.operation:
            raise ValueError("selection does not match invocation")
        if receipt.operation != request.operation:
            raise ValueError("qualification does not match invocation")

    @staticmethod
    def _request_hash(request: AgentInvocationRequest, qualification: QualificationReceipt,
                      selection: AgentSelectionPlan) -> str:
        value = {
            "slice_id": request.slice_id, "operation": request.operation,
            "surface_kind": request.surface_kind.value, "input_hash": request.input_hash,
            "correlation_id": request.correlation_id, "causation_id": request.causation_id,
            "qualification": qualification.receipt_id, "selection": selection.plan_id,
        }
        return "sha256:" + hashlib.sha256(_canonical(value, "request")).hexdigest()

    def _transition(self, key: str, status: InvocationStatus, **changes: Any) -> AgentInvocationReceipt:
        allowed = {
            InvocationStatus.PLANNED: {InvocationStatus.AUTHORIZED, InvocationStatus.CANCELLED,
                                       InvocationStatus.HANDOFF_REQUIRED},
            InvocationStatus.AUTHORIZED: {InvocationStatus.STARTED, InvocationStatus.CANCELLED},
            InvocationStatus.STARTED: {InvocationStatus.WAITING, InvocationStatus.SUCCEEDED,
                                       InvocationStatus.FAILED, InvocationStatus.CANCELLED,
                                       InvocationStatus.OUTCOME_UNKNOWN},
            InvocationStatus.WAITING: {InvocationStatus.STARTED, InvocationStatus.CANCELLED,
                                       InvocationStatus.SUCCEEDED, InvocationStatus.FAILED,
                                       InvocationStatus.OUTCOME_UNKNOWN},
        }
        with self._locked():
            receipts = self._read()
            current = receipts[key]
            if status not in allowed.get(current.status, set()):
                raise ValueError(f"illegal invocation transition: {current.status.value} -> {status.value}")
            updated = replace(current, status=status, **changes)
            receipts[key] = updated
            self._write(receipts)
            return updated

    def _read(self) -> Dict[str, AgentInvocationReceipt]:
        try:
            raw = json.loads(self.path.read_text())
        except FileNotFoundError:
            return {}
        if not isinstance(raw, dict):
            raise ValueError("invocation receipt store must contain an object")
        return {str(key): AgentInvocationReceipt.from_dict(value) for key, value in raw.items()}

    def _write(self, receipts: Mapping[str, AgentInvocationReceipt]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        content = json.dumps({key: value.to_dict() for key, value in sorted(receipts.items())},
                             sort_keys=True, indent=2, ensure_ascii=True) + "\n"
        temporary = self.path.with_name(self.path.name + ".tmp")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(descriptor, content.encode())
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        os.replace(temporary, self.path)

    class _Lock:
        def __init__(self, path: Path) -> None:
            path.parent.mkdir(parents=True, exist_ok=True)
            self.stream = path.open("a+b")

        def __enter__(self) -> None:
            fcntl.flock(self.stream.fileno(), fcntl.LOCK_EX)

        def __exit__(self, *args: object) -> None:
            fcntl.flock(self.stream.fileno(), fcntl.LOCK_UN)
            self.stream.close()

    def _locked(self) -> "AgentInvocationProtocol._Lock":
        return self._Lock(self.lock_path)
