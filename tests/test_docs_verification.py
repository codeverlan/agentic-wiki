from pathlib import Path

from typer.testing import CliRunner

from memwiki.cli import app
from memwiki.docs import render_all_docs
from memwiki.schemas import write_schemas
from memwiki.workspace import Workspace

runner = CliRunner()


def _write_source_checkout_markers(path: Path) -> None:
    (path / "src/memwiki").mkdir(parents=True)
    (path / "src/agentic_wiki").mkdir(parents=True)
    (path / "pyproject.toml").write_text('[project]\nname = "agentic-wiki"\n', encoding="utf-8")


def test_repo_docs_check_uses_generated_docs_from_clean_temp_workspace(tmp_path: Path) -> None:
    repo_root = tmp_path / "agentic-wiki"
    _write_source_checkout_markers(repo_root)

    schema_workspace = Workspace(tmp_path / "schema-workspace")
    schema_workspace.ensure_dirs()
    write_schemas(schema_workspace.path("schemas"))
    render_all_docs(schema_workspace, repo_root)

    clean = runner.invoke(app, ["--workspace", str(repo_root), "docs", "check"])
    assert clean.exit_code == 0, clean.output
    assert "OK" in clean.output

    stale_doc = repo_root / "docs/operations.html"
    stale_doc.write_text(stale_doc.read_text(encoding="utf-8") + "\n<!-- stale -->\n", encoding="utf-8")

    dirty = runner.invoke(app, ["--workspace", str(repo_root), "docs", "check"])
    assert dirty.exit_code == 1
    assert "Stale documentation: docs/operations.html" in dirty.output
