import json
from pathlib import Path
from typing import Any

import pytest

from agentic_wiki import AgenticWikiWorkspace
from memwiki.linter import lint_workspace
from memwiki.manifest import read_jsonl


def write_delta(path: Path, **record_overrides: Any) -> Path:
    record = {
        "record_id": "decision-semantic-html-v2",
        "kind": "decision",
        "summary": "Keep project memory in semantic HTML drafts.",
        "related_record_ids": ["constraint-immutable-sources"],
        "supersedes": ["decision-semantic-html-v1"],
    }
    record.update(record_overrides)
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "proposal_id": "proposal-semantic-html-v2",
                "proposed_at": "2026-07-11T14:00:00Z",
                "records": [record],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return path


def test_propose_agent_memory_requires_initialized_workspace(tmp_path: Path) -> None:
    delta_path = write_delta(tmp_path / "memory-delta.json")
    workspace = AgenticWikiWorkspace(tmp_path / "wiki")

    with pytest.raises(RuntimeError, match="Not an Agentic Wiki workspace"):
        workspace.propose_agent_memory(delta_path)

    assert not workspace.root.exists()


def test_proposal_registers_delta_json_as_immutable_source(tmp_path: Path) -> None:
    workspace = AgenticWikiWorkspace(tmp_path / "wiki")
    workspace.init()
    delta_path = write_delta(tmp_path / "memory-delta.json")
    original_bytes = delta_path.read_bytes()

    result = workspace.propose_agent_memory(delta_path)

    sources = read_jsonl(workspace.root / "manifests/sources.jsonl")
    assert len(sources) == 1
    source = sources[0]
    assert source["source_id"] == result.source_id
    assert source["kind"] == "json"
    assert source["source_category"] == "agent_memory_proposal"
    raw_path = workspace.root / source["raw_path"]
    assert raw_path.read_bytes() == original_bytes

    delta_path.write_text("{}\n", encoding="utf-8")
    assert raw_path.read_bytes() == original_bytes


def test_proposal_writes_semantic_html_claims_and_links_only_to_deterministic_draft(
    tmp_path: Path,
) -> None:
    workspace = AgenticWikiWorkspace(tmp_path / "wiki")
    workspace.init()
    delta_path = write_delta(tmp_path / "memory-delta.json")

    result = workspace.propose_agent_memory(delta_path)
    draft_root = workspace.root / "drafts" / result.draft_id

    assert result.draft_id
    assert result.page_id
    assert not (workspace.root / "wiki" / f"{result.page_id}.html").exists()
    assert sorted(path.name for path in (draft_root / "wiki").iterdir()) == [f"{result.page_id}.html"]
    html = (draft_root / "wiki" / f"{result.page_id}.html").read_text(encoding="utf-8")
    assert "<!doctype html>" in html.lower()
    assert "<main" in html
    assert "<article" in html
    assert "application/ld+json" in html

    pages = read_jsonl(draft_root / "manifests/pages.jsonl")
    claims = read_jsonl(draft_root / "manifests/claims.jsonl")
    links = read_jsonl(draft_root / "manifests/links.jsonl")
    assert [page["page_id"] for page in pages] == [result.page_id]
    assert claims and all(claim["source_id"] == result.source_id for claim in claims)
    assert links
    support_links = [link for link in links if link["relationship"] == "supported_by"]
    assert {link["from_id"] for link in support_links} == {result.page_id}
    assert {link["to_id"] for link in support_links}.issuperset({claim["claim_id"] for claim in claims})


def test_duplicate_proposal_replay_is_idempotent(tmp_path: Path) -> None:
    workspace = AgenticWikiWorkspace(tmp_path / "wiki")
    workspace.init()
    delta_path = write_delta(tmp_path / "memory-delta.json")

    first = workspace.propose_agent_memory(delta_path)
    first_draft_files = {
        path.relative_to(workspace.root / "drafts" / first.draft_id): path.read_bytes()
        for path in (workspace.root / "drafts" / first.draft_id).rglob("*")
        if path.is_file()
    }
    second = workspace.propose_agent_memory(delta_path)

    assert second.source_id == first.source_id
    assert second.draft_id == first.draft_id
    assert second.page_id == first.page_id
    assert first.duplicate is False
    assert second.duplicate is True
    assert len(read_jsonl(workspace.root / "manifests/sources.jsonl")) == 1
    assert [path.name for path in (workspace.root / "drafts").iterdir()] == [first.draft_id]
    assert {
        path.relative_to(workspace.root / "drafts" / first.draft_id): path.read_bytes()
        for path in (workspace.root / "drafts" / first.draft_id).rglob("*")
        if path.is_file()
    } == first_draft_files


def test_proposal_preserves_supersedes_as_additive_relationship(tmp_path: Path) -> None:
    workspace = AgenticWikiWorkspace(tmp_path / "wiki")
    workspace.init()
    delta_path = write_delta(tmp_path / "memory-delta.json")

    result = workspace.propose_agent_memory(delta_path)

    draft_root = workspace.root / "drafts" / result.draft_id
    claims = read_jsonl(draft_root / "manifests/claims.jsonl")
    links = read_jsonl(draft_root / "manifests/links.jsonl")
    assert any(claim.get("record_id") == "decision-semantic-html-v2" for claim in claims)
    assert {
        (link["from_id"], link["to_id"], link["relationship"])
        for link in links
    } >= {
        ("decision-semantic-html-v2", "decision-semantic-html-v1", "supersedes")
    }
    assert not (workspace.root / "wiki" / "decision-semantic-html-v1.html").exists()


def test_proposal_lint_resolves_record_ids_as_claim_aliases(tmp_path: Path) -> None:
    workspace = AgenticWikiWorkspace(tmp_path / "wiki")
    workspace.init()
    delta_path = tmp_path / "memory-delta.json"
    delta_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "proposal_id": "proposal-supersession-aliases",
                "proposed_at": "2026-07-11T14:00:00Z",
                "records": [
                    {"record_id": "design-v1", "kind": "design", "summary": "Synthetic first design."},
                    {
                        "record_id": "design-v2",
                        "kind": "design",
                        "summary": "Synthetic selected design.",
                        "supersedes": ["design-v1"],
                    },
                ],
            }
        )
        + "\n",
        encoding="utf-8",
    )

    result = workspace.propose_agent_memory(delta_path)

    lint = lint_workspace(workspace.workspace, base=workspace.root / "drafts" / result.draft_id)
    assert lint.ok, lint.errors


@pytest.mark.parametrize("prohibited_field", ["phi_value", "raw_phi", "secret_value", "token_value"])
def test_proposal_rejects_phi_and_secret_fields_before_writes(
    tmp_path: Path,
    prohibited_field: str,
) -> None:
    workspace = AgenticWikiWorkspace(tmp_path / "wiki")
    workspace.init()
    delta_path = write_delta(tmp_path / "memory-delta.json", **{prohibited_field: "must-not-persist"})
    before = {path.relative_to(workspace.root) for path in workspace.root.rglob("*")}

    with pytest.raises(ValueError, match="prohibited value field"):
        workspace.propose_agent_memory(delta_path)

    after = {path.relative_to(workspace.root) for path in workspace.root.rglob("*")}
    assert after == before
    assert read_jsonl(workspace.root / "manifests/sources.jsonl") == []
    assert not any((workspace.root / "raw").iterdir())
    assert not any((workspace.root / "drafts").iterdir())


@pytest.mark.parametrize(
    "records",
    [
        [{"record_id": "a", "kind": "decision", "summary": "Synthetic.", "supersedes": ["a"]}],
        [
            {"record_id": "a", "kind": "decision", "summary": "Synthetic A.", "supersedes": ["b"]},
            {"record_id": "b", "kind": "decision", "summary": "Synthetic B.", "supersedes": ["a"]},
        ],
    ],
)
def test_proposal_rejects_supersession_cycles_before_writes(tmp_path: Path, records: list[dict[str, object]]) -> None:
    workspace = AgenticWikiWorkspace(tmp_path / "wiki")
    workspace.init()
    delta_path = tmp_path / "memory-delta.json"
    delta_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "proposal_id": "proposal-cycle",
                "proposed_at": "2026-07-11T14:00:00Z",
                "records": records,
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="supersession cycle"):
        workspace.propose_agent_memory(delta_path)

    assert read_jsonl(workspace.root / "manifests/sources.jsonl") == []
