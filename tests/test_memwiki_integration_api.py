import json
from pathlib import Path

from typer.testing import CliRunner

from memwiki import MemwikiWorkspace
from memwiki.cli import app
from memwiki.manifest import read_jsonl

runner = CliRunner()


def invoke(workspace: Path, *args: str):
    return runner.invoke(app, ["--workspace", str(workspace), *args])


def test_public_api_supports_full_agent_workflow(tmp_path: Path) -> None:
    wiki = MemwikiWorkspace(tmp_path)
    init_result = wiki.init()
    assert init_result.status == "initialized"
    assert (tmp_path / ".memwiki/agent-capabilities.json").exists()

    source = tmp_path / "source.txt"
    source.write_text("Agents need stable source-backed references.", encoding="utf-8")

    preview = wiki.ingest(source, dry_run=True)
    assert preview.dry_run is True
    assert preview.source_id.startswith("src_")
    assert read_jsonl(tmp_path / "manifests/sources.jsonl") == []

    ingest = wiki.ingest(source)
    draft = wiki.compile(ingest.source_id)
    check = wiki.promote(draft.draft_id, check_only=True)
    assert check.check_only is True
    assert check.promoted_pages == 0
    assert not read_jsonl(tmp_path / "manifests/pages.jsonl")

    promoted = wiki.promote(draft.draft_id)
    assert promoted.promoted_pages == 1

    page_id = read_jsonl(tmp_path / "manifests/pages.jsonl")[0]["page_id"]
    claim_id = read_jsonl(tmp_path / "manifests/claims.jsonl")[0]["claim_id"]

    page = wiki.resolve(page_id)
    claim = wiki.resolve(claim_id)
    assert page["kind"] == "page"
    assert claim["kind"] == "claim"
    assert wiki.backlinks(claim_id)[0]["from_id"] == page_id

    query = wiki.query("stable source-backed references")
    assert query.matches
    assert ingest.source_id in query.citations


def test_cli_agent_options_emit_machine_readable_json(tmp_path: Path) -> None:
    assert invoke(tmp_path, "init").exit_code == 0
    source = tmp_path / "source.txt"
    source.write_text("JSON responses help coding agents compose tools.", encoding="utf-8")

    dry_run = invoke(tmp_path, "ingest", str(source), "--dry-run")
    assert dry_run.exit_code == 0, dry_run.output
    dry_run_data = json.loads(dry_run.output)
    assert dry_run_data["dry_run"] is True
    assert read_jsonl(tmp_path / "manifests/sources.jsonl") == []

    ingest = invoke(tmp_path, "ingest", str(source))
    source_id = json.loads(ingest.output)["source_id"]
    draft_id = json.loads(invoke(tmp_path, "compile", source_id).output)["draft_id"]

    check = invoke(tmp_path, "promote", draft_id, "--check-only")
    assert check.exit_code == 0, check.output
    assert json.loads(check.output)["check_only"] is True
    assert not read_jsonl(tmp_path / "manifests/pages.jsonl")

    promote = invoke(tmp_path, "promote", draft_id)
    assert promote.exit_code == 0, promote.output

    claim_id = read_jsonl(tmp_path / "manifests/claims.jsonl")[0]["claim_id"]
    resolved = invoke(tmp_path, "resolve", claim_id)
    assert resolved.exit_code == 0, resolved.output
    assert json.loads(resolved.output)["kind"] == "claim"

    backlinks = invoke(tmp_path, "backlinks", claim_id)
    assert backlinks.exit_code == 0, backlinks.output
    assert json.loads(backlinks.output)["backlinks"]

    query = invoke(tmp_path, "query", "coding agents compose tools", "--json")
    assert query.exit_code == 0, query.output
    query_data = json.loads(query.output)
    assert query_data["matches"]
    assert source_id in query_data["citations"]

    capabilities = invoke(tmp_path, "capabilities")
    assert capabilities.exit_code == 0, capabilities.output
    capability_data = json.loads(capabilities.output)
    assert "memwiki.ingest_source" in capability_data["tools"]
    assert capability_data["defaults"]["storage"] == "plain-files"
