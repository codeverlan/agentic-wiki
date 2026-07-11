from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Any, Dict, Mapping, Optional, Protocol, Sequence, Tuple

from memwiki.coordinator_agents import AgentVisibility, ExecutionMode

_OUTCOMES = {
    "success",
    "outage",
    "auth_expired",
    "schema_drift",
    "uncertain_outcome",
}
_STATUS_BY_OUTCOME = {
    "success": "succeeded",
    "outage": "retryable_failure",
    "auth_expired": "authorization_required",
    "schema_drift": "schema_rejected",
    "uncertain_outcome": "outcome_unknown",
}


def _canonical(value: object) -> str:
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )
    except (TypeError, ValueError) as error:
        raise ValueError("harness data must be JSON serializable") from error


def _hash(value: object) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value).encode()).hexdigest()


@dataclass(frozen=True)
class ScenarioAgent:
    agent_id: str
    adapter: str
    visibility: str
    execution_mode: str

    def __post_init__(self) -> None:
        if not self.agent_id.strip() or not self.adapter.strip():
            raise ValueError("agent and adapter identifiers must be non-empty")
        try:
            AgentVisibility(self.visibility)
            ExecutionMode(self.execution_mode)
        except ValueError as error:
            raise ValueError("agent visibility or execution mode is invalid") from error
        if self.execution_mode == ExecutionMode.METADATA_ONLY.value:
            raise ValueError("conformance agents must be callable or handoff-only")

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "ScenarioAgent":
        if set(value) != {"agent_id", "adapter", "visibility", "execution_mode"}:
            raise ValueError("scenario agent fields do not match schema")
        return cls(*(str(value[key]) for key in ("agent_id", "adapter", "visibility", "execution_mode")))


@dataclass(frozen=True)
class ScenarioInvocation:
    invocation_id: str
    agent_id: str
    operation: str
    payload: Mapping[str, Any]

    def __post_init__(self) -> None:
        if any(not item.strip() for item in (self.invocation_id, self.agent_id, self.operation)):
            raise ValueError("invocation identifiers must be non-empty")
        _canonical(self.payload)

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "ScenarioInvocation":
        if set(value) != {"invocation_id", "agent_id", "operation", "payload"}:
            raise ValueError("scenario invocation fields do not match schema")
        if not isinstance(value["payload"], Mapping):
            raise ValueError("scenario invocation payload must be an object")
        return cls(
            str(value["invocation_id"]),
            str(value["agent_id"]),
            str(value["operation"]),
            value["payload"],
        )


@dataclass(frozen=True)
class AgentScenario:
    name: str
    agents: Tuple[ScenarioAgent, ...]
    invocations: Tuple[ScenarioInvocation, ...]

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("scenario name must be non-empty")
        agent_ids = [item.agent_id for item in self.agents]
        if len(agent_ids) != len(set(agent_ids)):
            raise ValueError("scenario has a duplicate agent")
        invocation_ids = [item.invocation_id for item in self.invocations]
        if len(invocation_ids) != len(set(invocation_ids)):
            raise ValueError("scenario has a duplicate invocation")
        if any(item.agent_id not in set(agent_ids) for item in self.invocations):
            raise ValueError("scenario invocation references an unknown agent")

    def to_dict(self) -> Dict[str, object]:
        return {
            "name": self.name,
            "agents": [asdict(item) for item in self.agents],
            "invocations": [asdict(item) for item in self.invocations],
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "AgentScenario":
        if set(value) != {"name", "agents", "invocations"}:
            raise ValueError("agent scenario fields do not match schema")
        agents = value["agents"]
        invocations = value["invocations"]
        if not isinstance(agents, list) or not isinstance(invocations, list):
            raise ValueError("scenario agents and invocations must be lists")
        if not all(isinstance(item, Mapping) for item in agents + invocations):
            raise ValueError("scenario entries must be objects")
        return cls(
            str(value["name"]),
            tuple(ScenarioAgent.from_dict(item) for item in agents),
            tuple(ScenarioInvocation.from_dict(item) for item in invocations),
        )


@dataclass(frozen=True)
class FakeAgentReply:
    outcome: str
    output: object = None
    reply_id: Optional[str] = None

    def __post_init__(self) -> None:
        if self.outcome not in _OUTCOMES:
            raise ValueError("fake reply outcome is invalid")
        _canonical(self.output)


class AgentHarnessAdapter(Protocol):
    """Adapter seam for adding agent products without changing harness logic."""

    adapter_id: str

    def invoke(self, invocation: ScenarioInvocation, attempt: int) -> FakeAgentReply: ...


class _DeterministicHost:
    adapter_id: str

    def __init__(self, replies: Optional[Mapping[str, Sequence[FakeAgentReply]]] = None) -> None:
        self._replies = {key: tuple(value) for key, value in (replies or {}).items()}

    def invoke(self, invocation: ScenarioInvocation, attempt: int) -> FakeAgentReply:
        replies = self._replies.get(invocation.invocation_id, ())
        if replies:
            reply = replies[min(attempt - 1, len(replies) - 1)]
        else:
            reply = FakeAgentReply(
                "success",
                {"agent_result": invocation.operation, "input_hash": _hash(invocation.payload)},
            )
        if reply.reply_id is not None:
            return reply
        return FakeAgentReply(
            reply.outcome,
            reply.output,
            _hash({"adapter": self.adapter_id, "invocation": invocation.invocation_id, "attempt": attempt}),
        )


class DeterministicCodexHost(_DeterministicHost):
    adapter_id = "codex"


class DeterministicChatGPTHost(_DeterministicHost):
    adapter_id = "chatgpt"


@dataclass(frozen=True)
class ConformanceRecord:
    invocation_id: str
    agent_id: str
    adapter_id: str
    visibility: str
    execution_mode: str
    status: str
    call_count: int
    reply_id: Optional[str]
    output_hash: Optional[str]


@dataclass(frozen=True)
class HarnessState:
    scenario_hash: Optional[str] = None
    records: Tuple[ConformanceRecord, ...] = ()
    attempts: Tuple[Tuple[str, int], ...] = ()
    applied_reply_ids: Tuple[str, ...] = ()

    def to_dict(self) -> Dict[str, object]:
        return {
            "scenario_hash": self.scenario_hash,
            "records": [asdict(item) for item in self.records],
            "attempts": [[key, value] for key, value in self.attempts],
            "applied_reply_ids": list(self.applied_reply_ids),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "HarnessState":
        if set(value) != {"scenario_hash", "records", "attempts", "applied_reply_ids"}:
            raise ValueError("harness state fields do not match schema")
        records = value["records"]
        attempts = value["attempts"]
        replies = value["applied_reply_ids"]
        if not isinstance(records, list) or not isinstance(attempts, list) or not isinstance(replies, list):
            raise ValueError("harness state collections must be lists")
        parsed_records = []
        for item in records:
            if not isinstance(item, Mapping):
                raise ValueError("harness record must be an object")
            parsed_records.append(ConformanceRecord(**item))
        parsed_attempts = []
        for item in attempts:
            if not isinstance(item, list) or len(item) != 2:
                raise ValueError("harness attempt must be a pair")
            parsed_attempts.append((str(item[0]), int(item[1])))
        scenario_hash = value["scenario_hash"]
        return cls(
            None if scenario_hash is None else str(scenario_hash),
            tuple(parsed_records),
            tuple(parsed_attempts),
            tuple(str(item) for item in replies),
        )


@dataclass(frozen=True)
class HarnessResult:
    scenario_hash: str
    records: Tuple[ConformanceRecord, ...]
    applied_effects: Tuple[str, ...]
    state: HarnessState

    @property
    def conformant(self) -> bool:
        return all(
            item.status in {"succeeded", "handoff_required", "duplicate_ignored"}
            for item in self.records
        )

    def to_dict(self) -> Dict[str, object]:
        return {
            "scenario_hash": self.scenario_hash,
            "records": [asdict(item) for item in self.records],
            "applied_effects": list(self.applied_effects),
        }

    def to_json(self) -> str:
        return _canonical(self.to_dict())


class AgentCompatibilityHarness:
    """Runs hermetic compatibility scenarios through pluggable agent adapters."""

    def __init__(
        self,
        adapters: Mapping[str, AgentHarnessAdapter],
        *,
        state: Optional[HarnessState] = None,
    ) -> None:
        self._adapters = dict(adapters)
        if any(key != adapter.adapter_id for key, adapter in self._adapters.items()):
            raise ValueError("adapter registry key must match adapter identity")
        self._state = state or HarnessState()

    def run(
        self,
        scenario: AgentScenario,
        *,
        max_invocations: Optional[int] = None,
    ) -> HarnessResult:
        scenario_hash = _hash(scenario.to_dict())
        if self._state.scenario_hash not in {None, scenario_hash}:
            raise ValueError("persisted harness state belongs to another scenario")
        if max_invocations is not None and max_invocations < 0:
            raise ValueError("max_invocations must be non-negative")
        records = list(self._state.records)
        attempts = dict(self._state.attempts)
        applied = list(self._state.applied_reply_ids)
        completed = {item.invocation_id for item in records}
        agents = {item.agent_id: item for item in scenario.agents}
        remaining = [item for item in scenario.invocations if item.invocation_id not in completed]
        if max_invocations is not None:
            remaining = remaining[:max_invocations]

        for invocation in remaining:
            agent = agents[invocation.agent_id]
            adapter = self._adapters.get(agent.adapter)
            if agent.execution_mode == ExecutionMode.HANDOFF_ONLY.value:
                records.append(self._record(invocation, agent, agent.adapter, "handoff_required", 0))
                continue
            if adapter is None:
                records.append(self._record(invocation, agent, agent.adapter, "adapter_unavailable", 0))
                continue
            attempt = attempts.get(invocation.invocation_id, 0) + 1
            attempts[invocation.invocation_id] = attempt
            reply = adapter.invoke(invocation, attempt)
            reply_id = reply.reply_id or _hash({"invocation": invocation.invocation_id, "attempt": attempt})
            if reply_id in applied:
                status = "duplicate_ignored"
            else:
                status = _STATUS_BY_OUTCOME[reply.outcome]
                if reply.outcome == "success":
                    applied.append(reply_id)
            output_hash = _hash(reply.output) if reply.outcome == "success" else None
            records.append(
                self._record(invocation, agent, adapter.adapter_id, status, attempt, reply_id, output_hash)
            )

        state = HarnessState(
            scenario_hash,
            tuple(records),
            tuple(sorted(attempts.items())),
            tuple(applied),
        )
        self._state = state
        return HarnessResult(scenario_hash, state.records, state.applied_reply_ids, state)

    @staticmethod
    def _record(
        invocation: ScenarioInvocation,
        agent: ScenarioAgent,
        adapter_id: str,
        status: str,
        call_count: int,
        reply_id: Optional[str] = None,
        output_hash: Optional[str] = None,
    ) -> ConformanceRecord:
        return ConformanceRecord(
            invocation.invocation_id,
            agent.agent_id,
            adapter_id,
            agent.visibility,
            agent.execution_mode,
            status,
            call_count,
            reply_id,
            output_hash,
        )
