import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

import memwiki.cli as cli

runner = CliRunner()


def write_json(path: Path, payload: dict[str, Any]) -> Path:
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path


def invoke(workspace: Path, *args: str, prog_name: str = "agentic-wiki"):
    return runner.invoke(
        cli.app,
        ["--workspace", str(workspace), *args],
        prog_name=prog_name,
    )


def assert_sorted_json(output: str) -> dict[str, Any]:
    payload = json.loads(output)
    assert isinstance(payload, dict)
    assert output == json.dumps(payload, sort_keys=True) + "\n"
    return payload


@pytest.mark.parametrize("prog_name", ["agentic-wiki", "memwiki"])
def test_agent_memory_observe_emits_sorted_json_through_shared_cli_app(
    tmp_path: Path,
    prog_name: str,
) -> None:
    event_path = write_json(
        tmp_path / "event.json",
        {
            "schema_version": 1,
            "event_id": "evt-001",
            "event_type": "decision_observed",
            "occurred_at": "2026-07-11T12:00:00Z",
            "record": {
                "record_id": "decision-001",
                "kind": "decision",
                "summary": "Keep canonical memory pages in semantic HTML.",
            },
        },
    )

    result = invoke(
        tmp_path,
        "agent",
        "memory",
        "observe",
        str(event_path),
        prog_name=prog_name,
    )

    assert result.exit_code == 0, result.output
    payload = assert_sorted_json(result.output)
    assert payload == {
        "draft_id": "",
        "duplicate": False,
        "event_id": "evt-001",
        "event_path": str(event_path),
        "event_type": "decision_observed",
        "page_id": "",
        "record_id": "decision-001",
        "source_id": "",
    }


def test_agent_memory_context_emits_sorted_structured_result(tmp_path: Path) -> None:
    memory_state_path = write_json(
        tmp_path / "memory-state.json",
        {
            "schema_version": 1,
            "records": [
                {
                    "record_id": "decision-001",
                    "kind": "decision",
                    "summary": "Keep canonical memory pages in semantic HTML.",
                    "tags": ["html", "storage"],
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
            "tags": ["storage"],
        },
    )

    result = invoke(
        tmp_path,
        "agent",
        "memory",
        "context",
        str(memory_state_path),
        str(request_path),
    )

    assert result.exit_code == 0, result.output
    payload = assert_sorted_json(result.output)
    assert payload["memory_state_path"] == str(memory_state_path)
    assert payload["request_path"] == str(request_path)
    assert payload["request_id"] == "request-001"
    assert payload["selected_record_ids"] == ["decision-001"]
    assert [record["record_id"] for record in payload["records"]] == ["decision-001"]


def test_agent_memory_impact_emits_sorted_ids_and_sorted_json(tmp_path: Path) -> None:
    memory_state_path = write_json(
        tmp_path / "memory-state.json",
        {
            "schema_version": 1,
            "records": [
                {
                    "record_id": "decision-001",
                    "kind": "decision",
                    "summary": "Keep canonical memory pages in semantic HTML.",
                    "related_record_ids": ["workflow-z", "workflow-a"],
                },
                {
                    "record_id": "consumer-b",
                    "kind": "component",
                    "summary": "Builds canonical pages.",
                    "related_record_ids": ["decision-001"],
                },
                {
                    "record_id": "unrelated-001",
                    "kind": "constraint",
                    "summary": "Does not depend on the changed decision.",
                    "related_record_ids": [],
                },
            ],
        },
    )

    result = invoke(
        tmp_path,
        "agent",
        "memory",
        "impact",
        str(memory_state_path),
        "decision-001",
    )

    assert result.exit_code == 0, result.output
    payload = assert_sorted_json(result.output)
    assert payload == {
        "changed_record_id": "decision-001",
        "impacted_record_ids": ["consumer-b", "workflow-a", "workflow-z"],
        "memory_state_path": str(memory_state_path),
    }


def test_agent_memory_propose_delegates_and_emits_sorted_json(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    event_path = write_json(
        tmp_path / "event.json",
        {
            "schema_version": 1,
            "event_id": "evt-001",
            "event_type": "decision_observed",
            "occurred_at": "2026-07-11T12:00:00Z",
            "record": {
                "record_id": "decision-001",
                "kind": "decision",
                "summary": "Keep canonical memory pages in semantic HTML.",
            },
        },
    )

    class FakeWorkspace:
        def __init__(self, workspace: Path) -> None:
            assert workspace == tmp_path

        def propose_agent_memory(self, event: Path, context: object = None) -> dict[str, str]:
            assert event == event_path
            assert context is None
            return {
                "status": "proposed",
                "proposal_path": str(tmp_path / "drafts" / "proposal-001.json"),
                "proposal_id": "proposal-001",
                "event_id": "evt-001",
            }

    monkeypatch.setattr(cli, "AgenticWikiWorkspace", FakeWorkspace)

    result = invoke(
        tmp_path,
        "agent",
        "memory",
        "propose",
        str(event_path),
    )

    assert result.exit_code == 0, result.output
    assert assert_sorted_json(result.output) == {
        "event_id": "evt-001",
        "proposal_id": "proposal-001",
        "proposal_path": str(tmp_path / "drafts" / "proposal-001.json"),
        "status": "proposed",
    }
