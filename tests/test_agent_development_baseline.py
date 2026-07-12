import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agentic_wiki import AgenticWikiWorkspace
from memwiki.cli import app
from memwiki.manifest import read_jsonl

runner = CliRunner()


def test_baseline_requires_an_existing_project_directory(tmp_path: Path) -> None:
    missing = tmp_path / "missing-project"

    with pytest.raises(ValueError, match="must already exist"):
        AgenticWikiWorkspace(missing).initialize_agent_development_project(
            handles_phi="no",
        )

    assert not missing.exists()


def test_baseline_creates_provenance_backed_drafts_without_canonical_knowledge(tmp_path: Path) -> None:
    project = tmp_path / "sample-project"
    project.mkdir()

    result = AgenticWikiWorkspace(project).initialize_agent_development_project(
        handles_phi="no",
        objective="Build a local scheduling application.",
    )

    assert result.status == "initialized"
    assert result.handles_phi == "no"
    assert result.synthetic_data_required is False
    assert result.page_count >= 10
    assert read_jsonl(project / "manifests/pages.jsonl") == []
    assert read_jsonl(project / "manifests/claims.jsonl") == []
    assert sorted(path.name for path in (project / "wiki").glob("*.html")) == [
        "contradictions.html",
        "index.html",
    ]

    draft_root = project / "drafts" / result.draft_id
    pages = read_jsonl(draft_root / "manifests/pages.jsonl")
    claims = read_jsonl(draft_root / "manifests/claims.jsonl")
    assert len(pages) == result.page_count
    assert len(claims) == result.page_count
    assert all(claim["source_id"] == result.source_id for claim in claims)
    assert all(claim["provenance"]["source_locator"]["type"] == "json" for claim in claims)
    assert all(page["review_status"] == "draft" for page in pages)
    check = AgenticWikiWorkspace(project).promote(result.draft_id, check_only=True)
    assert check.check_only is True
    assert check.promoted_pages == 0
    run_registry = json.loads(
        (project / ".memwiki/coordinator/runs.json").read_text(encoding="utf-8")
    )
    run_layout = json.loads(
        (project / ".memwiki/coordinator/layout.json").read_text(encoding="utf-8")
    )
    assert run_registry["runs"] == []
    assert run_layout["run_directory_template"] == ".memwiki/coordinator/runs/{run-key}"
    assert run_layout["one_run_per_directory"] is True
    assert (project / "schemas/agent_development_baseline.schema.json").is_file()
    assert (project / "schemas/coordinator_run_registry.schema.json").is_file()


@pytest.mark.parametrize("handles_phi", ["yes", "unknown"])
def test_phi_or_unknown_baseline_requires_synthetic_development_data(
    tmp_path: Path,
    handles_phi: str,
) -> None:
    project = tmp_path / f"project-{handles_phi}"
    project.mkdir()

    result = AgenticWikiWorkspace(project).initialize_agent_development_project(
        handles_phi=handles_phi,
    )

    privacy = json.loads((project / ".agent-development/privacy-profile.json").read_text(encoding="utf-8"))
    assert result.synthetic_data_required is True
    assert privacy["handles_phi"] == handles_phi
    assert privacy["development_data_policy"] == "synthetic_only"
    assert privacy["actual_phi_allowed"] is False


def test_baseline_is_idempotent_and_preserves_existing_project_state(tmp_path: Path) -> None:
    project = tmp_path / "existing-project"
    project.mkdir()
    user_file = project / "README.md"
    user_file.write_text("User-authored content.\n", encoding="utf-8")
    wiki = AgenticWikiWorkspace(project)
    wiki.init()
    config_before = (project / ".memwiki/config.toml").read_bytes()

    first = wiki.initialize_agent_development_project(
        handles_phi="no",
        project_name="Existing Project",
        objective="Preserve established state.",
    )
    snapshot = {
        path.relative_to(project): path.read_bytes()
        for path in project.rglob("*")
        if path.is_file()
    }
    second = wiki.initialize_agent_development_project(
        handles_phi="no",
        project_name="Existing Project",
        objective="Preserve established state.",
    )

    assert second.status == "already_initialized"
    assert second.draft_id == first.draft_id
    assert second.source_id == first.source_id
    assert user_file.read_text(encoding="utf-8") == "User-authored content.\n"
    assert (project / ".memwiki/config.toml").read_bytes() == config_before
    assert snapshot == {
        path.relative_to(project): path.read_bytes()
        for path in project.rglob("*")
        if path.is_file()
    }
    assert len(read_jsonl(project / "manifests/sources.jsonl")) == 1


def test_baseline_refuses_to_reinterpret_an_existing_privacy_answer(tmp_path: Path) -> None:
    project = tmp_path / "fixed-privacy-project"
    project.mkdir()
    wiki = AgenticWikiWorkspace(project)
    wiki.initialize_agent_development_project(handles_phi="no")

    with pytest.raises(ValueError, match="already initialized with different inputs"):
        wiki.initialize_agent_development_project(handles_phi="yes")

    privacy = json.loads((project / ".agent-development/privacy-profile.json").read_text(encoding="utf-8"))
    assert privacy["handles_phi"] == "no"


def test_baseline_preserves_existing_nonempty_run_registry(tmp_path: Path) -> None:
    project = tmp_path / "running-project"
    project.mkdir()
    wiki = AgenticWikiWorkspace(project)
    wiki.init()
    registry = project / ".memwiki/coordinator/runs.json"
    registry.parent.mkdir(parents=True, exist_ok=True)
    original = {
        "schema_version": 1,
        "runs": [{"run_id": "existing", "path": "runs/existing", "status": "active"}],
    }
    registry.write_text(json.dumps(original), encoding="utf-8")

    result = wiki.initialize_agent_development_project(handles_phi="no")

    assert result.status == "initialized"
    assert json.loads(registry.read_text(encoding="utf-8")) == original


def test_scaffold_collision_fails_before_any_baseline_file_is_written(tmp_path: Path) -> None:
    project = tmp_path / "collision-project"
    project.mkdir()
    wiki = AgenticWikiWorkspace(project)
    wiki.init()
    collision = project / ".agent-development/privacy-profile.json"
    collision.parent.mkdir(parents=True, exist_ok=True)
    collision.write_text('{"user":"owned"}\n', encoding="utf-8")

    with pytest.raises(ValueError, match="will not be overwritten"):
        wiki.initialize_agent_development_project(handles_phi="no")

    assert not (project / ".agent-development/project-profile.json").exists()
    assert not (project / ".agent-development/baseline-source.json").exists()


def test_agent_development_init_cli_emits_sorted_machine_readable_json(tmp_path: Path) -> None:
    project = tmp_path / "cli-project"
    project.mkdir()

    result = runner.invoke(
        app,
        [
            "--workspace",
            str(project),
            "agent-development",
            "init",
            "--handles-phi",
            "unknown",
            "--objective",
            "Build with synthetic data until privacy is resolved.",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert result.output == json.dumps(payload, sort_keys=True) + "\n"
    assert payload["status"] == "initialized"
    assert payload["handles_phi"] == "unknown"
    assert payload["synthetic_data_required"] is True
