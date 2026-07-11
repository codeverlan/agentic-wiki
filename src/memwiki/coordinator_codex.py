from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, Mapping, Optional, Protocol, Sequence, Tuple, Union

from memwiki.coordinator_capabilities import (
    AuthRequirement,
    Availability,
    CapabilityDescriptor,
    CapabilityKind,
    CostClass,
    MutationScope,
    NetworkRequirement,
    PrivacyClass,
    ReplaySafety,
    SupervisionRequirement,
)
from memwiki.coordinator_workers import (
    WorkerDispatch,
    WorkerError,
    WorkerHandle,
    WorkerOutcome,
    WorkerStatus,
    _validate_dispatch,
)

_CREDENTIAL_KEYS = {
    "access_token",
    "api_key",
    "authorization",
    "credential",
    "credentials",
    "password",
    "private_key",
    "refresh_token",
    "secret",
    "token",
}
_KIND_MAP = {
    "plugin": CapabilityKind.PLUGIN,
    "skill": CapabilityKind.SKILL,
    "mcp": CapabilityKind.MCP,
    "api": CapabilityKind.API,
    "browser": CapabilityKind.BROWSER,
    "image": CapabilityKind.IMAGE_TOOL,
    "command": CapabilityKind.LOCAL_COMMAND,
    "service": CapabilityKind.SERVICE,
    "agent": CapabilityKind.SPAWNED_AGENT,
}
_AVAILABILITY_MAP = {
    "available": Availability.AVAILABLE,
    "unavailable": Availability.EXPECTED_UNAVAILABLE,
    "nonexistent": Availability.DOES_NOT_EXIST,
    "disabled": Availability.DISABLED,
    "unknown": Availability.UNKNOWN,
}


def _credential_free(value: object, path: str = "payload") -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError(f"{path} keys must be strings")
            normalized = key.lower().replace("-", "_")
            if normalized in _CREDENTIAL_KEYS:
                raise ValueError(f"credential material is forbidden in {path}")
            _credential_free(item, f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _credential_free(item, f"{path}[{index}]")
    elif value is not None and not isinstance(value, (str, int, float, bool)):
        raise ValueError(f"{path} must contain JSON-compatible values")


@dataclass(frozen=True)
class HostWorkerRequest:
    assignment: Mapping[str, object]
    lease_token: str
    coordinator_fencing_token: int
    deadline: str


class CodexHost(Protocol):
    """Host callbacks supplied by Codex or a deterministic test implementation."""

    def spawn_worker(self, request: HostWorkerRequest) -> str: ...

    def poll_worker(self, host_handle: str) -> str: ...

    def heartbeat_worker(self, host_handle: str) -> str: ...

    def collect_worker(self, host_handle: str) -> WorkerOutcome: ...

    def cancel_worker(self, host_handle: str, reason: str) -> WorkerOutcome: ...

    def list_capabilities(self) -> Sequence["HostCapability"]: ...

    def invoke_capability(self, request: "HostCapabilityRequest") -> WorkerOutcome: ...


@dataclass(frozen=True)
class HostCapability:
    identifier: str
    display_name: str
    kind: str
    operations: Tuple[str, ...]
    availability: str
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.identifier or not self.display_name:
            raise ValueError("host capability identity must be non-empty")
        if self.kind not in _KIND_MAP:
            raise ValueError("unknown Codex host capability kind")
        if self.availability not in _AVAILABILITY_MAP:
            raise ValueError("unknown Codex host availability")
        if not self.operations or any(not item for item in self.operations):
            raise ValueError("host capability operations must be non-empty")
        _credential_free(self.metadata, "capability metadata")


@dataclass(frozen=True)
class HostCapabilityRequest:
    capability_id: str
    operation: str
    payload: Mapping[str, object]

    def __post_init__(self) -> None:
        if not self.capability_id or not self.operation:
            raise ValueError("capability request identity must be non-empty")
        _credential_free(self.payload)


@dataclass
class _WorkerRun:
    assignment_id: str
    host_handle: str
    terminal: bool = False


class CodexWorkerExecutor:
    """Maps provider-neutral worker execution to Codex host callbacks."""

    def __init__(self, host: CodexHost, *, max_workers: int = 6) -> None:
        if isinstance(max_workers, bool) or not isinstance(max_workers, int) or not 1 <= max_workers <= 6:
            raise ValueError("max_workers must be between one and six")
        self._host = host
        self._max_workers = max_workers
        self._runs: Dict[str, _WorkerRun] = {}
        self._sequence = 0

    def dispatch(self, request: WorkerDispatch, *, now: datetime) -> WorkerHandle:
        _validate_dispatch(request, now)
        if sum(not run.terminal for run in self._runs.values()) >= self._max_workers:
            raise WorkerError("Codex worker capacity is exhausted")
        host_request = HostWorkerRequest(
            assignment=request.assignment.to_dict(),
            lease_token=request.lease.lease_token,
            coordinator_fencing_token=request.lease.coordinator_fencing_token,
            deadline=request.deadline.isoformat(),
        )
        try:
            host_handle = self._host.spawn_worker(host_request)
        except Exception as exc:
            raise WorkerError("Codex host rejected worker dispatch") from exc
        if not host_handle:
            raise WorkerError("Codex host returned an empty worker handle")
        self._sequence += 1
        handle = WorkerHandle(f"codex-{self._sequence}", request.assignment.assignment_id)
        self._runs[handle.handle_id] = _WorkerRun(handle.assignment_id, host_handle)
        return handle

    def poll(self, handle: WorkerHandle, *, now: datetime) -> WorkerStatus:
        run = self._run(handle)
        state = self._host_state(self._host.poll_worker(run.host_handle))
        run.terminal = state == "finished"
        return WorkerStatus(state, now)

    def heartbeat(self, handle: WorkerHandle, *, now: datetime) -> WorkerStatus:
        run = self._run(handle)
        state = self._host_state(self._host.heartbeat_worker(run.host_handle))
        run.terminal = state == "finished"
        return WorkerStatus(state, now)

    def collect(self, handle: WorkerHandle, *, now: datetime) -> WorkerOutcome:
        run = self._run(handle)
        outcome = self._host.collect_worker(run.host_handle)
        run.terminal = True
        return outcome

    def cancel(self, handle: WorkerHandle, *, now: datetime, reason: str) -> WorkerOutcome:
        if not reason:
            raise ValueError("cancellation reason must be non-empty")
        run = self._run(handle)
        outcome = self._host.cancel_worker(run.host_handle, reason)
        run.terminal = True
        return outcome

    def _run(self, handle: WorkerHandle) -> _WorkerRun:
        run = self._runs.get(handle.handle_id)
        if run is None or run.assignment_id != handle.assignment_id:
            raise WorkerError("unknown Codex worker handle")
        return run

    @staticmethod
    def _host_state(state: str) -> str:
        aliases = {"pending": "awaiting_acknowledgement", "active": "running", "complete": "finished"}
        normalized = aliases.get(state, state)
        if normalized not in {"awaiting_acknowledgement", "running", "finished"}:
            raise WorkerError("Codex host returned an invalid worker state")
        return normalized


class CodexCapabilityAdapter:
    """Discovers and invokes Codex tools without persisting request payloads."""

    def __init__(self, host: CodexHost) -> None:
        self._host = host

    def discover(self) -> Tuple[CapabilityDescriptor, ...]:
        descriptors = [self._descriptor(item) for item in self._host.list_capabilities()]
        identifiers = [item.identifier for item in descriptors]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("duplicate Codex host capability identifier")
        return tuple(sorted(descriptors, key=lambda item: item.identifier))

    def invoke(self, request: HostCapabilityRequest, *, supervised: bool = False) -> WorkerOutcome:
        capabilities = {item.identifier: item for item in self._host.list_capabilities()}
        capability = capabilities.get(request.capability_id)
        if capability is None:
            return WorkerOutcome("blocked", "Codex host capability does not exist", None, {
                "availability": "does_not_exist", "future_opportunity": True
            })
        if request.operation not in capability.operations:
            return WorkerOutcome("malformed", "capability does not support the requested operation", None)
        availability = _AVAILABILITY_MAP[capability.availability]
        if availability is Availability.EXPECTED_UNAVAILABLE:
            return WorkerOutcome("unavailable", "expected Codex host capability is unavailable", None, {
                "availability": availability.value
            })
        if availability is not Availability.AVAILABLE:
            return WorkerOutcome("blocked", "Codex host capability cannot be invoked", None, {
                "availability": availability.value,
                "future_opportunity": availability is Availability.DOES_NOT_EXIST,
            })
        if self._requires_supervision(capability, request) and not supervised:
            return WorkerOutcome("supervision", "computer use requires user supervision", None, {
                "capability_id": capability.identifier
            })
        try:
            return self._host.invoke_capability(request)
        except Exception:
            return WorkerOutcome("retryable", "Codex host capability callback failed", None)

    @staticmethod
    def _requires_supervision(capability: HostCapability, request: HostCapabilityRequest) -> bool:
        return capability.kind == "browser" and request.operation == "computer_use"

    @staticmethod
    def _descriptor(capability: HostCapability) -> CapabilityDescriptor:
        requires_supervision = capability.kind == "browser" and "computer_use" in capability.operations
        return CapabilityDescriptor(
            identifier=capability.identifier,
            display_name=capability.display_name,
            kind=_KIND_MAP[capability.kind],
            source="codex-host",
            operations=capability.operations,
            availability=_AVAILABILITY_MAP[capability.availability],
            mutation_scope=MutationScope.EXTERNAL_WRITE if requires_supervision else MutationScope.READ_ONLY,
            auth=AuthRequirement.OPTIONAL,
            network=NetworkRequirement.OPTIONAL,
            cost=CostClass.METERED,
            privacy=PrivacyClass.NON_SENSITIVE_ONLY,
            replay=ReplaySafety.IDEMPOTENCY_REQUIRED,
            supervision=(
                SupervisionRequirement.REQUIRED if requires_supervision else SupervisionRequirement.CONDITIONAL
            ),
            metadata=dict(capability.metadata),
        )


FakeResult = Union[WorkerOutcome, BaseException]


class DeterministicCodexHost:
    """In-memory Codex callback surface for contract tests and smoke tests."""

    def __init__(
        self,
        *,
        capabilities: Sequence[HostCapability] = (),
        worker_results: Optional[Mapping[str, FakeResult]] = None,
        capability_results: Optional[Mapping[str, FakeResult]] = None,
    ) -> None:
        self._capabilities = tuple(capabilities)
        self._worker_results = dict(worker_results or {})
        self._capability_results = dict(capability_results or {})
        self.worker_requests: list[HostWorkerRequest] = []
        self.capability_invocations: list[HostCapabilityRequest] = []
        self._assignments: Dict[str, str] = {}
        self._cancelled: Dict[str, WorkerOutcome] = {}

    def spawn_worker(self, request: HostWorkerRequest) -> str:
        assignment_id = str(request.assignment["assignment_id"])
        handle = f"host-{len(self.worker_requests) + 1}"
        self.worker_requests.append(request)
        self._assignments[handle] = assignment_id
        return handle

    def poll_worker(self, host_handle: str) -> str:
        assignment_id = self._assignment(host_handle)
        return "finished" if host_handle in self._cancelled or assignment_id in self._worker_results else "running"

    def heartbeat_worker(self, host_handle: str) -> str:
        return self.poll_worker(host_handle)

    def collect_worker(self, host_handle: str) -> WorkerOutcome:
        if host_handle in self._cancelled:
            return self._cancelled[host_handle]
        result = self._worker_results.get(self._assignment(host_handle))
        if result is None:
            raise WorkerError("Codex host worker has not completed")
        if isinstance(result, BaseException):
            raise result
        return result

    def cancel_worker(self, host_handle: str, reason: str) -> WorkerOutcome:
        self._assignment(host_handle)
        outcome = WorkerOutcome("retryable", reason, None, {"cancelled": True})
        self._cancelled[host_handle] = outcome
        return outcome

    def list_capabilities(self) -> Sequence[HostCapability]:
        return self._capabilities

    def invoke_capability(self, request: HostCapabilityRequest) -> WorkerOutcome:
        self.capability_invocations.append(request)
        result = self._capability_results.get(
            f"{request.capability_id}:{request.operation}", WorkerOutcome.success({"status": "ok"})
        )
        if isinstance(result, BaseException):
            raise result
        return result

    def _assignment(self, host_handle: str) -> str:
        try:
            return self._assignments[host_handle]
        except KeyError as exc:
            raise WorkerError("unknown Codex host worker handle") from exc
