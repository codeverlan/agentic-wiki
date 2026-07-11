import json
from pathlib import Path
from typing import Any

import pytest

from agentic_wiki import AgenticWikiWorkspace
from memwiki import MemwikiWorkspace

WORKSPACE_TYPES = [AgenticWikiWorkspace, MemwikiWorkspace]


def write_json(path: Path, payload: dict[str, Any]) -> Path:
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path


def assert_structured_result(result: object) -> dict[str, Any]:
    to_dict = result.to_dict
    payload = to_dict()
    assert isinstance(payload, dict)
    assert json.loads(json.dumps(payload)) == payload
    return payload


@pytest.mark.parametrize("workspace_type", WORKSPACE_TYPES)
def test_workspace_observes_agent_memory_event(
    tmp_path: Path,
    workspace_type: type[AgenticWikiWorkspace],
) -> None:
    workspace = workspace_type(tmp_path)
    event_path = write_json(
        tmp_path / "agent-memory-event.json",
        {
            "schema_version": 1,
            "event_id": "evt-001",
            "event_type": "record_observed",
            "occurred_at": "2026-07-11T12:00:00Z",
            "record": {
                "record_id": "decision-001",
                "kind": "decision",
                "summary": "Keep canonical memory pages in semantic HTML.",
            },
        },
    )

    result = workspace.observe_agent_memory(event_path)

    payload = assert_structured_result(result)
    assert payload["event_path"] == str(event_path)
    assert payload["event_id"] == "evt-001"


@pytest.mark.parametrize("workspace_type", WORKSPACE_TYPES)
def test_workspace_builds_agent_context_from_memory_state_and_request(
    tmp_path: Path,
    workspace_type: type[AgenticWikiWorkspace],
) -> None:
    workspace = workspace_type(tmp_path)
    memory_state_path = write_json(
        tmp_path / "memory-state.json",
        {
            "schema_version": 1,
            "records": [
                {
                    "record_id": "decision-001",
                    "kind": "decision",
                    "summary": "Keep canonical memory pages in semantic HTML.",
                    "tags": ["storage", "html"],
                },
                {
                    "record_id": "constraint-001",
                    "kind": "constraint",
                    "summary": "Raw source files remain immutable after ingest.",
                    "tags": ["ingest"],
                },
            ],
        },
    )
    request_path = write_json(
        tmp_path / "context-request.json",
        {
            "schema_version": 1,
            "request_id": "request-001",
            "objective": "Plan semantic HTML memory storage.",
            "tags": ["storage", "html"],
        },
    )

    result = workspace.build_agent_context(memory_state_path, request_path)

    payload = assert_structured_result(result)
    assert payload["memory_state_path"] == str(memory_state_path)
    assert payload["request_path"] == str(request_path)
    assert payload["request_id"] == "request-001"
    assert "decision-001" in payload["selected_record_ids"]


@pytest.mark.parametrize("workspace_type", WORKSPACE_TYPES)
def test_workspace_assesses_agent_memory_impact_for_changed_record(
    tmp_path: Path,
    workspace_type: type[AgenticWikiWorkspace],
) -> None:
    workspace = workspace_type(tmp_path)
    memory_state_path = write_json(
        tmp_path / "memory-state.json",
        {
            "schema_version": 1,
            "records": [
                {
                    "record_id": "decision-001",
                    "kind": "decision",
                    "summary": "Keep canonical memory pages in semantic HTML.",
                    "related_record_ids": ["workflow-001"],
                },
                {
                    "record_id": "workflow-001",
                    "kind": "workflow",
                    "summary": "Validate semantic HTML before promotion.",
                    "related_record_ids": ["decision-001"],
                },
            ],
        },
    )

    result = workspace.assess_agent_memory_impact(memory_state_path, "decision-001")

    payload = assert_structured_result(result)
    assert payload["memory_state_path"] == str(memory_state_path)
    assert payload["changed_record_id"] == "decision-001"
    assert "workflow-001" in payload["impacted_record_ids"]
