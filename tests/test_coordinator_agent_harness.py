from __future__ import annotations

from dataclasses import replace

import pytest

from memwiki.coordinator_agent_harness import (
    AgentCompatibilityHarness,
    AgentScenario,
    DeterministicChatGPTHost,
    DeterministicCodexHost,
    FakeAgentReply,
    HarnessState,
    ScenarioAgent,
    ScenarioInvocation,
)


def scenario() -> AgentScenario:
    return AgentScenario(
        name="public-private-conformance",
        agents=(
            ScenarioAgent("codex.public", "codex", "public", "callable"),
            ScenarioAgent("chatgpt.private", "chatgpt", "personal_private", "callable"),
            ScenarioAgent("chatgpt.handoff", "chatgpt", "public", "handoff_only"),
        ),
        invocations=(
            ScenarioInvocation("i-1", "codex.public", "analyze", {"q": "one"}),
            ScenarioInvocation("i-2", "chatgpt.private", "research", {"q": "two"}),
            ScenarioInvocation("i-3", "chatgpt.handoff", "review", {"q": "three"}),
        ),
    )


def test_scenario_dsl_round_trip_and_validation() -> None:
    value = scenario()
    assert AgentScenario.from_dict(value.to_dict()) == value
    with pytest.raises(ValueError, match="duplicate agent"):
        replace(value, agents=(value.agents[0], value.agents[0]))
    with pytest.raises(ValueError, match="unknown agent"):
        replace(
            value,
            invocations=(ScenarioInvocation("bad", "missing", "run", {}),),
        )


def test_public_private_callable_and_handoff_conformance() -> None:
    harness = AgentCompatibilityHarness(
        {
            "codex": DeterministicCodexHost(),
            "chatgpt": DeterministicChatGPTHost(),
        }
    )
    result = harness.run(scenario())

    assert [item.status for item in result.records] == [
        "succeeded",
        "succeeded",
        "handoff_required",
    ]
    assert result.records[0].visibility == "public"
    assert result.records[1].visibility == "personal_private"
    assert result.records[2].call_count == 0
    assert result.conformant


@pytest.mark.parametrize(
    ("outcome", "status"),
    [
        ("outage", "retryable_failure"),
        ("auth_expired", "authorization_required"),
        ("schema_drift", "schema_rejected"),
        ("uncertain_outcome", "outcome_unknown"),
    ],
)
def test_failure_modes_are_typed_and_slice_local(outcome: str, status: str) -> None:
    host = DeterministicCodexHost({"i-1": (FakeAgentReply(outcome),)})
    value = AgentScenario(
        "failure",
        (ScenarioAgent("codex.public", "codex", "public", "callable"),),
        (ScenarioInvocation("i-1", "codex.public", "run", {}),),
    )
    result = AgentCompatibilityHarness({"codex": host}).run(value)
    assert result.records[0].status == status
    assert result.records[0].invocation_id == "i-1"


def test_duplicate_reply_is_detected_without_duplicate_effect() -> None:
    reply = FakeAgentReply("success", {"answer": 1}, reply_id="same")
    host = DeterministicCodexHost({"i-1": (reply,), "i-2": (reply,)})
    value = AgentScenario(
        "duplicate",
        (ScenarioAgent("a", "codex", "public", "callable"),),
        (
            ScenarioInvocation("i-1", "a", "run", {}),
            ScenarioInvocation("i-2", "a", "run", {}),
        ),
    )
    result = AgentCompatibilityHarness({"codex": host}).run(value)
    assert result.records[0].status == "succeeded"
    assert result.records[1].status == "duplicate_ignored"
    assert result.applied_effects == ("same",)


def test_restart_is_byte_equivalent_and_does_not_repeat_calls() -> None:
    value = scenario()
    adapters = {
        "codex": DeterministicCodexHost(),
        "chatgpt": DeterministicChatGPTHost(),
    }
    uninterrupted = AgentCompatibilityHarness(adapters).run(value)

    first = AgentCompatibilityHarness(
        {"codex": DeterministicCodexHost(), "chatgpt": DeterministicChatGPTHost()}
    )
    partial = first.run(value, max_invocations=1)
    restored = AgentCompatibilityHarness(
        {"codex": DeterministicCodexHost(), "chatgpt": DeterministicChatGPTHost()},
        state=HarnessState.from_dict(partial.state.to_dict()),
    ).run(value)

    assert restored.to_json() == uninterrupted.to_json()
    assert restored.records[-1].call_count == 0


def test_new_agent_product_uses_adapter_without_core_changes() -> None:
    class CustomAdapter:
        adapter_id = "custom"

        def invoke(self, invocation: ScenarioInvocation, attempt: int) -> FakeAgentReply:
            return FakeAgentReply(
                "success", {"invocation": invocation.invocation_id, "attempt": attempt}
            )

    value = AgentScenario(
        "extension",
        (ScenarioAgent("private.custom", "custom", "personal_private", "callable"),),
        (ScenarioInvocation("x", "private.custom", "extend", {}),),
    )
    result = AgentCompatibilityHarness({"custom": CustomAdapter()}).run(value)
    assert result.records[0].status == "succeeded"
    assert result.records[0].adapter_id == "custom"


def test_missing_adapter_is_reported_not_invoked() -> None:
    value = AgentScenario(
        "missing",
        (ScenarioAgent("future.agent", "future", "public", "callable"),),
        (ScenarioInvocation("x", "future.agent", "run", {}),),
    )
    result = AgentCompatibilityHarness({}).run(value)
    assert result.records[0].status == "adapter_unavailable"
    assert result.records[0].call_count == 0
