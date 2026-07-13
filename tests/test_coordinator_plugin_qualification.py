from __future__ import annotations

import json
from pathlib import Path

import pytest

from memwiki.coordinator_plugin_qualification import (
    REQUIRED_ARCHETYPES,
    PluginQualificationHarness,
    QualificationStatus,
)


def _plugin(root: Path) -> Path:
    (root / ".codex-plugin").mkdir(parents=True)
    (root / "scripts").mkdir()
    (root / "assets").mkdir()
    for skill in (
        "agent-dev-continuous-coordinator",
        "agent-dev-eval-routing",
        "agent-dev-intake-preflight",
        "agent-dev-memory-steward",
        "agent-dev-start-project",
        "agent-dev-status-handoff",
    ):
        path = root / "skills" / skill
        path.mkdir(parents=True)
        body = f"---\nname: {skill}\ndescription: Qualification fixture skill.\n---\n# {skill}\n"
        if skill == "agent-dev-start-project":
            body += (
                "Start building new software from scratch, from an existing PRD, or from partial resources. "
                "Ask one focused question at a time. Ask whether the application handles PHI. "
                "Transition automatically to implementation readiness and continuous coordination. "
                "Use scripts/start_project.py for initialize prefill worksheet apply-worksheet "
                "apply inspect resume readiness. "
                "The universal software project worksheet treats unknown answers as interview candidates.\n"
                "When the initial description is significant, prefill every explicitly stated fact into its matching "
                "worksheet field before presenting the form; unsupported inferences remain questions.\n"
                "First Gate: require an explicit project root and run scripts/initialize_project.py "
                "with --handles-phi before memory writes. Keep synthetic data in a provenance draft "
                "before canonical promotion and use .memwiki/coordinator/runs.json.\n"
            )
        (path / "SKILL.md").write_text(
            body,
            encoding="utf-8",
        )
    (root / ".codex-plugin" / "plugin.json").write_text(
        json.dumps(
            {
                "name": "agent-development-coordinator",
                "version": "test-version",
                "description": "Qualification fixture",
                "author": {"name": "Test"},
                "skills": "./skills/",
            }
        ),
        encoding="utf-8",
    )
    (root / "scripts" / "coordinator.py").write_text(
        "PLUGIN_IMMUTABLE = True\nCOMPUTER_USE_REQUIRES_SUPERVISION = True\n"
        "Host runtime observations require freshness.\n"
        "Measurements preserve exact estimated, unknown, and unavailable semantics; never estimate.\n"
        "Durable worker packet obligations are mandatory.\n"
        "The canonical queue is the single source of truth.\n"
        "Completion coherence is required.\n"
        "Recovery qualification references coordinator_recovery.\n",
        encoding="utf-8",
    )
    (root / "scripts" / "start_project.py").write_text(
        "OPERATIONS = 'initialize prefill worksheet apply-worksheet apply inspect resume readiness'\n",
        encoding="utf-8",
    )
    (root / "scripts" / "initialize_project.py").write_text(
        "PUBLIC_API = 'initialize_agent_development_project'\nOPTION = '--handles-phi'\n",
        encoding="utf-8",
    )
    (root / "scripts" / "route_model.py").write_text("ROUTING = True\n", encoding="utf-8")
    (root / "assets" / "adaptive-model-routing-contract.json").write_text(
        json.dumps({"tiers": {"narrow": {}, "implementation": {}, "frontier": {}}}),
        encoding="utf-8",
    )
    (root / "assets" / "project-wiki-scaffold-contract.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "canonical_coordinator_layout": {
                    "run_registry": ".memwiki/coordinator/runs.json"
                },
            }
        ),
        encoding="utf-8",
    )
    (root / "assets" / "software-project-intake-template.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "questions": [
                    {"id": "product-purpose", "required": True},
                    {"id": "target-users", "required": True},
                    {"id": "observable-outcomes", "required": True},
                    {"id": "in-scope", "required": True},
                    {"id": "primary-workflow", "required": True},
                    {"id": "phi-answer", "required": True},
                    {"id": "acceptance-criteria", "required": True},
                    {"id": "definition-of-done", "required": True},
                ],
            }
        ),
        encoding="utf-8",
    )
    (root / "assets" / "initial-description-prefill-contract.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "required": [
                    "project_id",
                    "source_revision",
                    "description_summary",
                    "field_provenance",
                    "intake_update",
                ],
                "allowed_provenance": ["explicit", "user-confirmed"],
            }
        ),
        encoding="utf-8",
    )
    return root


def test_qualification_runs_all_archetypes_and_adversarial_checks(tmp_path: Path) -> None:
    source = _plugin(tmp_path / "source")
    (source / ".git").mkdir()
    (source / ".git" / "config").write_text("source-control metadata", encoding="utf-8")
    installed = tmp_path / "installed"
    installed.mkdir()
    for path in source.rglob("*"):
        target = installed / path.relative_to(source)
        if path.is_dir():
            target.mkdir(exist_ok=True)
        else:
            target.write_bytes(path.read_bytes())

    result = PluginQualificationHarness(source, installed).run()

    assert result.status is QualificationStatus.PASSED
    assert set(result.archetypes) == set(REQUIRED_ARCHETYPES)
    assert all(case.passed for case in result.cases)
    assert any(case.case_id == "natural-language-start-routing" for case in result.cases)
    assert any(case.case_id == "executable-intake-recovery" for case in result.cases)
    intake_case = next(case for case in result.cases if case.case_id == "executable-intake-recovery")
    assert "worksheet" in intake_case.details
    assert "prefill" in intake_case.details
    assert any(case.case_id == "adaptive-model-reasoning-routing" for case in result.cases)
    assert any(case.case_id == "adc-refinement-contracts" for case in result.cases)
    assert any(case.case_id == "project-wiki-baseline-first-gate" for case in result.cases)
    assert result.qualification_id.startswith("sha256:")
    assert type(result).from_dict(result.to_dict()) == result


def test_source_cache_drift_fails_qualification_without_exposing_content(tmp_path: Path) -> None:
    source = _plugin(tmp_path / "source")
    installed = _plugin(tmp_path / "installed")
    (installed / "scripts" / "coordinator.py").write_text("changed = True\n", encoding="utf-8")

    result = PluginQualificationHarness(source, installed).run()

    assert result.status is QualificationStatus.FAILED
    parity = next(case for case in result.cases if case.case_id == "installed-cache-parity")
    assert parity.passed is False
    assert "changed = True" not in json.dumps(result.to_dict())


def test_missing_required_skill_fails_discovery(tmp_path: Path) -> None:
    source = _plugin(tmp_path / "source")
    installed = _plugin(tmp_path / "installed")
    skill = installed / "skills" / "agent-dev-eval-routing" / "SKILL.md"
    skill.unlink()

    result = PluginQualificationHarness(source, installed).run()

    discovery = next(case for case in result.cases if case.case_id == "skill-discovery")
    assert discovery.passed is False
    assert "agent-dev-eval-routing" in discovery.details


def test_ambiguous_start_project_front_door_fails_routing_readiness(tmp_path: Path) -> None:
    source = _plugin(tmp_path / "source")
    installed = _plugin(tmp_path / "installed")
    skill = installed / "skills" / "agent-dev-start-project" / "SKILL.md"
    skill.write_text("---\nname: agent-dev-start-project\ndescription: Start.\n---\n", encoding="utf-8")

    result = PluginQualificationHarness(source, installed).run()

    routing = next(case for case in result.cases if case.case_id == "natural-language-start-routing")
    assert routing.passed is False
    assert "missing concepts" in routing.details


@pytest.mark.parametrize(
    "obligation",
    [
        "Host runtime observations",
        "freshness",
        "never estimate",
        "Durable worker packet",
        "canonical queue",
        "Completion coherence",
        "Recovery qualification",
    ],
)
def test_missing_adc_refinement_obligation_fails_qualification(tmp_path: Path, obligation: str) -> None:
    source = _plugin(tmp_path / "source")
    installed = _plugin(tmp_path / "installed")
    coordinator = installed / "scripts" / "coordinator.py"
    coordinator.write_text(coordinator.read_text(encoding="utf-8").replace(obligation, "removed"), encoding="utf-8")

    result = PluginQualificationHarness(source, installed).run()

    adc = next(case for case in result.cases if case.case_id == "adc-refinement-contracts")
    assert adc.passed is False
    assert "missing contracts" in adc.details


def test_report_writer_confines_outputs_and_emits_json_and_semantic_html(tmp_path: Path) -> None:
    source = _plugin(tmp_path / "source")
    installed = _plugin(tmp_path / "installed")
    result = PluginQualificationHarness(source, installed).run()
    output = tmp_path / "evidence"

    files = result.write(output)

    assert json.loads(files.json_path.read_text())["qualification_id"] == result.qualification_id
    html = files.html_path.read_text()
    assert "<!doctype html>" in html.lower()
    assert 'type="application/ld+json"' in html
    assert "Plugin Qualification" in html
    with pytest.raises(ValueError, match="output directory"):
        result.write(output, json_name="../outside.json")
