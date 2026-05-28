import json
from pathlib import Path

from PIL import Image
from typer.testing import CliRunner

from memwiki.cli import app
from memwiki.docs import docs_status
from memwiki.manifest import read_jsonl
from memwiki.models import DryRunAdapter, ModelRequest
from memwiki.workspace import Workspace

runner = CliRunner()


def invoke(workspace: Path, *args: str):
    return runner.invoke(app, ["--workspace", str(workspace), *args])


def test_init_creates_workspace_contract(tmp_path: Path) -> None:
    result = invoke(tmp_path, "init")

    assert result.exit_code == 0, result.output
    expected_dirs = [
        "raw",
        "wiki",
        "drafts",
        "assets",
        "manifests",
        "schemas",
        "docs",
        ".memwiki",
    ]
    for name in expected_dirs:
        assert (tmp_path / name).is_dir()
    assert (tmp_path / ".memwiki/config.toml").exists()
    assert (tmp_path / "AGENTS.md").exists()
    assert (tmp_path / "schemas/source.schema.json").exists()
    assert "memwiki docs check" in (tmp_path / "AGENTS.md").read_text()


def test_ingest_registers_text_html_pdf_and_image_sources(tmp_path: Path) -> None:
    invoke(tmp_path, "init")
    text = tmp_path / "note.txt"
    text.write_text("Memory systems should preserve source provenance.", encoding="utf-8")
    html = tmp_path / "page.html"
    html.write_text("<html><body><h1>HTML source</h1><p>Structured content.</p></body></html>")
    pdf = tmp_path / "empty.pdf"
    pdf.write_bytes(
        b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
        b"2 0 obj<</Type/Pages/Count 0/Kids[]>>endobj\n"
        b"xref\n0 3\n0000000000 65535 f \n0000000009 00000 n \n"
        b"0000000058 00000 n \ntrailer<</Root 1 0 R/Size 3>>\nstartxref\n"
        b"108\n%%EOF\n"
    )
    image = tmp_path / "image.png"
    Image.new("RGB", (8, 4), "white").save(image)

    for path in [text, html, pdf, image]:
        result = invoke(tmp_path, "ingest", str(path))
        assert result.exit_code == 0, result.output

    records = read_jsonl(tmp_path / "manifests/sources.jsonl")
    assert [record["kind"] for record in records] == ["text", "html", "pdf", "image"]
    for record in records:
        extracted = tmp_path / ".memwiki/extracted" / record["source_id"] / "text.txt"
        assert extracted.exists()
    image_record = records[-1]
    assert image_record["metadata"]["width"] == 8
    assert image_record["metadata"]["height"] == 4


def test_ingest_rejects_duplicate_hash_without_alias(tmp_path: Path) -> None:
    invoke(tmp_path, "init")
    source = tmp_path / "source.txt"
    source.write_text("duplicate content", encoding="utf-8")

    first = invoke(tmp_path, "ingest", str(source))
    second = invoke(tmp_path, "ingest", str(source))
    third = invoke(tmp_path, "ingest", str(source), "--alias", "copy")

    assert first.exit_code == 0, first.output
    assert second.exit_code != 0
    assert "Duplicate source hash" in second.output
    assert third.exit_code == 0, third.output
    assert len(read_jsonl(tmp_path / "manifests/sources.jsonl")) == 2


def test_compile_promote_lint_query_and_export_static(tmp_path: Path) -> None:
    invoke(tmp_path, "init")
    source = tmp_path / "source.txt"
    source.write_text("HTML memory uses source-backed claims and draft promotion.", encoding="utf-8")
    ingest = invoke(tmp_path, "ingest", str(source))
    assert ingest.exit_code == 0, ingest.output
    source_id = json.loads(ingest.output.strip().splitlines()[-1])["source_id"]

    compile_result = invoke(tmp_path, "compile", source_id)
    assert compile_result.exit_code == 0, compile_result.output
    draft_id = json.loads(compile_result.output.strip().splitlines()[-1])["draft_id"]
    assert sorted(path.name for path in (tmp_path / "wiki").glob("*.html")) == [
        "contradictions.html",
        "index.html",
    ]
    assert (tmp_path / "drafts" / draft_id / "wiki").is_dir()

    promote = invoke(tmp_path, "promote", draft_id)
    assert promote.exit_code == 0, promote.output
    lint = invoke(tmp_path, "lint")
    assert lint.exit_code == 0, lint.output
    assert "OK" in lint.output

    pages = read_jsonl(tmp_path / "manifests/pages.jsonl")
    claims = read_jsonl(tmp_path / "manifests/claims.jsonl")
    assert pages[0]["review_status"] == "accepted"
    assert claims[0]["source_id"] == source_id
    assert claims[0]["provenance"]["source_locator"]["type"] == "text"

    query = invoke(tmp_path, "query", "source-backed claims")
    assert query.exit_code == 0, query.output
    assert "source-backed claims" in query.output.lower()
    assert source_id in query.output

    export = invoke(tmp_path, "export", "static", "--output", str(tmp_path / "site"))
    assert export.exit_code == 0, export.output
    assert (tmp_path / "site/index.html").exists()
    assert (tmp_path / "site/search.json").exists()


def test_invalid_draft_promotion_fails_validation(tmp_path: Path) -> None:
    invoke(tmp_path, "init")
    bad_draft = tmp_path / "drafts/bad/wiki"
    bad_draft.mkdir(parents=True)
    (bad_draft / "bad.html").write_text("<article><p>No metadata</p></article>", encoding="utf-8")

    result = invoke(tmp_path, "promote", "bad")

    assert result.exit_code != 0
    assert "No JSON-LD metadata" in result.output


def test_docs_check_and_draft_detect_schema_drift(tmp_path: Path) -> None:
    invoke(tmp_path, "init")
    clean = invoke(tmp_path, "docs", "check")
    assert clean.exit_code == 0, clean.output

    schema_path = tmp_path / "schemas/source.schema.json"
    data = json.loads(schema_path.read_text())
    data["properties"]["new_field"] = {"type": "string"}
    schema_path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    dirty_status = docs_status(Workspace(tmp_path))
    assert not dirty_status.clean
    assert "docs/schema.html" in dirty_status.stale_paths

    draft = invoke(tmp_path, "docs", "draft")
    assert draft.exit_code == 0, draft.output
    draft_id = json.loads(draft.output.strip().splitlines()[-1])["draft_id"]
    assert (tmp_path / "drafts" / draft_id / "docs/schema.html").exists()

    promote = invoke(tmp_path, "promote", draft_id)
    assert promote.exit_code == 0, promote.output
    clean_again = invoke(tmp_path, "docs", "check")
    assert clean_again.exit_code == 0, clean_again.output


def test_dry_run_model_adapter_is_local_and_deterministic() -> None:
    adapter = DryRunAdapter()
    result = adapter.complete(
        ModelRequest(prompt="Summarize", files={"source.txt": "A source-backed fact."}, schema=None)
    )

    assert result.adapter == "dry-run"
    assert result.remote is False
    assert "source-backed fact" in result.text.lower()
