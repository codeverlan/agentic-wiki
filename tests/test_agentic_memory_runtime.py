import json
from pathlib import Path

import pytest

from agentic_wiki import AgenticWikiWorkspace, OperationContext
from memwiki.manifest import read_jsonl


def test_observed_agent_memory_event_becomes_source_backed_draft(tmp_path: Path) -> None:
    wiki = AgenticWikiWorkspace(tmp_path)
    wiki.init()
    event_path = tmp_path / "event.json"
    event_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "event_id": "evt-001",
                "event_type": "decision_observed",
                "occurred_at": "2026-07-11T01:00:00-04:00",
                "record": {
                    "record_id": "decision-001",
                    "kind": "decision",
                    "summary": "Use semantic HTML for canonical project memory.",
                },
            }
        ),
        encoding="utf-8",
    )

    first = wiki.observe_agent_memory(event_path)
    second = wiki.observe_agent_memory(event_path)

    assert first.source_id
    assert first.draft_id
    assert first.page_id
    assert first.duplicate is False
    assert second.source_id == first.source_id
    assert second.draft_id == first.draft_id
    assert second.duplicate is True
    source = read_jsonl(tmp_path / "manifests/sources.jsonl")[0]
    assert source["source_category"] == "agent_memory_event"
    claims = read_jsonl(tmp_path / "drafts" / first.draft_id / "manifests/claims.jsonl")
    assert claims[0]["source_id"] == first.source_id
    assert claims[0]["provenance"]["source_locator"]["type"] == "json"
    assert (tmp_path / "drafts" / first.draft_id / "wiki" / f"{first.page_id}.html").exists()
    assert not (tmp_path / "wiki" / f"{first.page_id}.html").exists()


def test_observed_memory_escapes_event_controlled_html(tmp_path: Path) -> None:
    wiki = AgenticWikiWorkspace(tmp_path)
    wiki.init()
    event_path = tmp_path / "event.json"
    event_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "event_id": "evt-<img>",
                "event_type": "observation",
                "occurred_at": "2026-07-11T01:00:00-04:00",
                "record": {
                    "record_id": "record-<script>",
                    "kind": "decision",
                    "summary": "<script>alert('unsafe')</script>",
                },
            }
        ),
        encoding="utf-8",
    )

    result = wiki.observe_agent_memory(event_path)
    html = (tmp_path / "drafts" / result.draft_id / "wiki" / f"{result.page_id}.html").read_text()

    assert "<script>alert" not in html
    assert "&lt;script&gt;alert" in html
    assert "record-&lt;script&gt;" in html


def test_clinical_duplicate_observation_still_requires_operation_context(tmp_path: Path) -> None:
    wiki = AgenticWikiWorkspace(tmp_path)
    wiki.init(
        profile="clinical-phi",
        client_record_id="synthetic-client-001",
        local_encrypted_storage_attested=True,
    )
    event_path = tmp_path / "event.json"
    event_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "event_id": "evt-001",
                "event_type": "observation",
                "occurred_at": "2026-07-11T01:00:00-04:00",
                "record": {"record_id": "record-001", "kind": "decision", "summary": "Synthetic."},
            }
        ),
        encoding="utf-8",
    )
    context = OperationContext(
        actor_id="coordinator",
        actor_role="coordinator",
        purpose_of_use="development",
        session_id="session-001",
    )
    wiki.observe_agent_memory(event_path, context=context)

    with pytest.raises(PermissionError, match="Operation context is required"):
        wiki.observe_agent_memory(event_path)


def test_agent_context_enforces_explicit_record_bound(tmp_path: Path) -> None:
    wiki = AgenticWikiWorkspace(tmp_path)
    state = tmp_path / "state.json"
    request = tmp_path / "request.json"
    state.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "records": [
                    {"record_id": f"record-{index:03d}", "kind": "decision", "summary": "Active"}
                    for index in range(20)
                ],
            }
        ),
        encoding="utf-8",
    )
    request.write_text(
        json.dumps({"schema_version": 1, "request_id": "request-001", "objective": "Bounded", "max_records": 3}),
        encoding="utf-8",
    )

    result = wiki.build_agent_context(state, request)

    assert result.selected_record_ids == ["record-000", "record-001", "record-002"]


def test_event_runtime_rejects_schema_type_drift(tmp_path: Path) -> None:
    wiki = AgenticWikiWorkspace(tmp_path)
    event_path = tmp_path / "event.json"
    event_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "event_id": ["not", "a", "string"],
                "event_type": "observation",
                "occurred_at": "2026-07-11T01:00:00-04:00",
                "record": {"record_id": "record-001", "kind": "decision", "summary": "Synthetic."},
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="event_id must be a non-empty string"):
        wiki.observe_agent_memory(event_path)


def test_observation_rejects_hash_reuse_from_another_source_category(tmp_path: Path) -> None:
    wiki = AgenticWikiWorkspace(tmp_path)
    wiki.init()
    event_path = tmp_path / "event.json"
    event_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "event_id": "event-001",
                "event_type": "observation",
                "occurred_at": "2026-07-11T01:00:00-04:00",
                "record": {"record_id": "record-001", "kind": "decision", "summary": "Synthetic."},
            }
        ),
        encoding="utf-8",
    )
    wiki.ingest(event_path, source_category="generic-json")

    with pytest.raises(ValueError, match="source category conflict"):
        wiki.observe_agent_memory(event_path)


def test_context_request_requires_published_contract_fields(tmp_path: Path) -> None:
    wiki = AgenticWikiWorkspace(tmp_path)
    state = tmp_path / "state.json"
    request = tmp_path / "request.json"
    state.write_text(json.dumps({"schema_version": 1, "records": []}), encoding="utf-8")
    request.write_text(json.dumps({"schema_version": 1, "request_id": "request-001"}), encoding="utf-8")

    with pytest.raises(ValueError, match="missing required field: objective"):
        wiki.build_agent_context(state, request)


def test_agent_context_reports_byte_budget_truncation(tmp_path: Path) -> None:
    wiki = AgenticWikiWorkspace(tmp_path)
    state = tmp_path / "state.json"
    request = tmp_path / "request.json"
    state.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "records": [
                    {"record_id": f"record-{index}", "kind": "decision", "summary": "x" * 180}
                    for index in range(4)
                ],
            }
        ),
        encoding="utf-8",
    )
    request.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "request_id": "request-001",
                "objective": "Bound bytes",
                "max_records": 10,
                "max_bytes": 320,
            }
        ),
        encoding="utf-8",
    )

    result = wiki.build_agent_context(state, request)

    assert result.truncated is True
    assert result.omitted_record_count == 3
    assert len(result.records) == 1
