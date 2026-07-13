from __future__ import annotations

import importlib.util
import json
from argparse import Namespace
from pathlib import Path
from types import ModuleType

PLUGIN_ROOT = Path("/Users/tyler-lcsw/plugins/agent-development-coordinator")
SCRIPT = PLUGIN_ROOT / "scripts" / "start_project.py"


def _module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("agent_dev_start_project", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_plugin_start_project_executes_initialize_apply_and_readiness(tmp_path: Path) -> None:
    helper = _module()
    initialized = helper.execute(
        Namespace(
            project_root=tmp_path,
            operation="initialize",
            project_id="plugin-pilot",
            entry_path="from-scratch",
            project_type="web-application",
            phi_answer="no",
            recorded_at="2026-07-12T15:00:00-04:00",
        )
    )
    assert initialized["revision"] == 1

    update = tmp_path / "update.json"
    update.write_text(
        json.dumps(
            {
                "product": {
                    "purpose": "Build a synthetic scheduling tool",
                    "users": ["synthetic scheduler"],
                    "outcomes": ["shifts are assigned"],
                    "scope": ["schedule"],
                },
                "acceptance_criteria": [{"id": "AC-1", "statement": "Shift is assigned"}],
                "open_questions": [],
            }
        ),
        encoding="utf-8",
    )
    applied = helper.execute(
        Namespace(
            project_root=tmp_path,
            operation="apply",
            input=update,
            expected_revision=1,
            recorded_at="2026-07-12T15:01:00-04:00",
        )
    )
    assert applied["revision"] == 2
    readiness = helper.execute(Namespace(project_root=tmp_path, operation="readiness"))
    assert readiness["ready"] is True


def test_project_start_operations_do_not_mutate_plugin_source(tmp_path: Path) -> None:
    coordinator = PLUGIN_ROOT / "scripts" / "coordinator.py"
    spec = importlib.util.spec_from_file_location("agent_dev_coordinator", coordinator)
    assert spec is not None and spec.loader is not None
    guard = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(guard)
    before = guard.plugin_snapshot()

    helper = _module()
    helper.execute(
        Namespace(
            project_root=tmp_path,
            operation="initialize",
            project_id="immutable",
            entry_path="from-scratch",
            project_type="cli",
            phi_answer="no",
            recorded_at="2026-07-12T15:00:00-04:00",
        )
    )

    guard.assert_plugin_unchanged(PLUGIN_ROOT, before)


def test_plugin_start_project_generates_universal_intake_worksheet(tmp_path: Path) -> None:
    helper = _module()
    helper.execute(
        Namespace(
            project_root=tmp_path,
            operation="initialize",
            project_id="worksheet-pilot",
            entry_path="from-scratch",
            project_type="web-application",
            phi_answer="no",
            recorded_at="2026-07-13T09:00:00-04:00",
        )
    )

    result = helper.execute(Namespace(project_root=tmp_path, operation="worksheet"))

    html_path = Path(result["html_path"])
    schema_path = Path(result["schema_path"])
    assert result["project_id"] == "worksheet-pilot"
    assert result["source_revision"] == 1
    assert result["required_question_count"] >= 8
    assert html_path.is_file()
    assert schema_path.is_file()
    assert html_path.is_relative_to(tmp_path)
    assert schema_path.is_relative_to(tmp_path)

    worksheet = html_path.read_text(encoding="utf-8")
    assert "Software Project Intake" in worksheet
    assert "Will the application being developed create, receive, maintain" in worksheet
    assert "I am not sure yet" in worksheet
    assert "Use the recommended default" in worksheet
    assert "Download answers as JSON" in worksheet
    assert "localStorage" in worksheet
    assert "application/ld+json" in worksheet
    assert r"value.split(/\r?\n/)" in worksheet
    assert "needs_confirmation" in worksheet
    assert "Confirm availability for" in worksheet

    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    assert schema["schema_version"] == 1
    assert schema["project_id"] == "worksheet-pilot"
    assert schema["source_revision"] == 1
    assert schema["answers"]["phi_answer"] == "no"
    assert schema["answers"]["project_type"] == "web-application"
    assert schema["questions"][0]["id"] == "product-purpose"
    assert any(question["id"] == "phi-answer" for question in schema["questions"])


def test_universal_intake_worksheet_rejects_uninitialized_project(tmp_path: Path) -> None:
    helper = _module()

    try:
        helper.execute(Namespace(project_root=tmp_path, operation="worksheet"))
    except (FileNotFoundError, ValueError) as exc:
        assert "intake" in str(exc).casefold() or "project-intake" in str(exc).casefold()
    else:
        raise AssertionError("worksheet generation must require initialized intake state")


def test_plugin_start_project_applies_exported_worksheet_packet(tmp_path: Path) -> None:
    helper = _module()
    helper.execute(
        Namespace(
            project_root=tmp_path,
            operation="initialize",
            project_id="worksheet-apply",
            entry_path="from-scratch",
            project_type="cli-or-library",
            phi_answer="unknown",
            recorded_at="2026-07-13T09:00:00-04:00",
        )
    )
    packet = tmp_path / "answers.json"
    packet.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "project_id": "worksheet-apply",
                "source_revision": 1,
                "answers": {"product_purpose": "Build a local analysis tool"},
                "intake_update": {
                    "phi_answer": "no",
                    "product": {
                        "purpose": "Build a local analysis tool",
                        "users": ["analyst"],
                        "outcomes": ["report is generated"],
                        "scope": ["analyze local input"],
                    },
                    "acceptance_criteria": [{"id": "AC-1", "statement": "Report is generated"}],
                    "open_questions": [],
                },
            }
        ),
        encoding="utf-8",
    )

    applied = helper.execute(
        Namespace(
            project_root=tmp_path,
            operation="apply-worksheet",
            input=packet,
            recorded_at="2026-07-13T09:05:00-04:00",
        )
    )

    assert applied["revision"] == 2
    assert helper.execute(Namespace(project_root=tmp_path, operation="readiness"))["ready"] is True


def test_plugin_start_project_rejects_stale_or_foreign_worksheet_packet(tmp_path: Path) -> None:
    helper = _module()
    helper.execute(
        Namespace(
            project_root=tmp_path,
            operation="initialize",
            project_id="worksheet-owner",
            entry_path="from-scratch",
            project_type="web-application",
            phi_answer="no",
            recorded_at="2026-07-13T09:00:00-04:00",
        )
    )
    packet = tmp_path / "foreign.json"
    packet.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "project_id": "different-project",
                "source_revision": 1,
                "answers": {},
                "intake_update": {},
            }
        ),
        encoding="utf-8",
    )

    try:
        helper.execute(
            Namespace(
                project_root=tmp_path,
                operation="apply-worksheet",
                input=packet,
                recorded_at="2026-07-13T09:05:00-04:00",
            )
        )
    except ValueError as exc:
        assert "project" in str(exc).casefold()
    else:
        raise AssertionError("foreign worksheet packet must be rejected")


def test_significant_initial_description_prefills_matching_worksheet_fields(tmp_path: Path) -> None:
    helper = _module()
    helper.execute(
        Namespace(
            project_root=tmp_path,
            operation="initialize",
            project_id="description-prefill",
            entry_path="from-scratch",
            project_type="web-application",
            phi_answer="no",
            recorded_at="2026-07-13T11:00:00-04:00",
        )
    )
    packet = tmp_path / "initial-description.json"
    packet.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "project_id": "description-prefill",
                "source_revision": 1,
                "description_summary": (
                    "Build a browser scheduling application for clinic coordinators. "
                    "They create appointments and verify that conflicts are prevented."
                ),
                "field_provenance": {
                    "product.purpose": "explicit",
                    "product.users": "explicit",
                    "product.outcomes": "explicit",
                    "product.scope": "explicit",
                    "decisions.PRIMARY-WORKFLOW": "explicit",
                    "design.material": "explicit",
                    "external_dependencies": "explicit",
                    "acceptance_criteria": "explicit",
                },
                "intake_update": {
                    "product": {
                        "purpose": "Build a browser scheduling application",
                        "users": ["clinic coordinators"],
                        "outcomes": ["appointment conflicts are prevented"],
                        "scope": ["create appointments", "detect scheduling conflicts"],
                    },
                    "decisions": [
                        {
                            "id": "PRIMARY-WORKFLOW",
                            "summary": "Primary end-to-end workflow",
                            "value": "A coordinator creates an appointment and receives conflict validation.",
                            "status": "proposed",
                            "source": "initial-user-description",
                        }
                    ],
                    "design": {"material": True, "authority": "Browser application described by user"},
                    "external_dependencies": [
                        {
                            "id": "calendar service",
                            "availability": "unavailable",
                            "needs_confirmation": True,
                            "source": "initial-user-description",
                        }
                    ],
                    "acceptance_criteria": [
                        {"id": "AC-1", "statement": "Conflicting appointments are rejected"}
                    ],
                    "open_questions": [
                        {
                            "id": "DEPENDENCY-1",
                            "question": "Confirm the calendar service and its availability",
                            "source": "initial-user-description",
                        }
                    ],
                },
            }
        ),
        encoding="utf-8",
    )

    applied = helper.execute(
        Namespace(
            project_root=tmp_path,
            operation="prefill",
            input=packet,
            recorded_at="2026-07-13T11:01:00-04:00",
        )
    )
    worksheet_result = helper.execute(Namespace(project_root=tmp_path, operation="worksheet"))
    worksheet = json.loads(Path(worksheet_result["schema_path"]).read_text(encoding="utf-8"))
    state = json.loads(Path(applied["state_path"]).read_text(encoding="utf-8"))

    assert applied["revision"] == 2
    assert worksheet["answers"]["product_purpose"] == "Build a browser scheduling application"
    assert worksheet["answers"]["target_users"] == ["clinic coordinators"]
    assert worksheet["answers"]["observable_outcomes"] == ["appointment conflicts are prevented"]
    assert worksheet["answers"]["in_scope"] == ["create appointments", "detect scheduling conflicts"]
    assert worksheet["answers"]["primary_workflow"].startswith("A coordinator creates")
    assert worksheet["answers"]["user_interface"] == "yes"
    assert worksheet["answers"]["integrations"] == ["calendar service"]
    assert worksheet["answers"]["acceptance_criteria"] == ["Conflicting appointments are rejected"]
    assert state["sources"][0]["authority"] == "user-initial-description"
    assert state["sources"][0]["field_provenance"]["product.purpose"] == "explicit"


def test_initial_description_prefill_rejects_inferred_or_foreign_facts(tmp_path: Path) -> None:
    helper = _module()
    helper.execute(
        Namespace(
            project_root=tmp_path,
            operation="initialize",
            project_id="description-owner",
            entry_path="from-scratch",
            project_type="web-application",
            phi_answer="unknown",
            recorded_at="2026-07-13T11:00:00-04:00",
        )
    )
    packet = tmp_path / "invalid-prefill.json"
    packet.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "project_id": "description-owner",
                "source_revision": 1,
                "description_summary": "Build a scheduling application.",
                "field_provenance": {"product.users": "inferred"},
                "intake_update": {"product": {"users": ["clinic coordinators"]}},
            }
        ),
        encoding="utf-8",
    )

    try:
        helper.execute(
            Namespace(
                project_root=tmp_path,
                operation="prefill",
                input=packet,
                recorded_at="2026-07-13T11:01:00-04:00",
            )
        )
    except ValueError as exc:
        assert "provenance" in str(exc).casefold()
    else:
        raise AssertionError("inferred facts must not be admitted as initial-description prefill")


def test_planning_depth_recommends_bmad_and_blocks_readiness_until_selected(
    tmp_path: Path,
) -> None:
    helper = _module()
    helper.execute(
        Namespace(
            project_root=tmp_path,
            operation="initialize",
            project_id="complex-planning",
            entry_path="from-scratch",
            project_type="clinical-web-application",
            phi_answer="yes",
            recorded_at="2026-07-13T12:00:00-04:00",
        )
    )
    update = tmp_path / "complex-update.json"
    update.write_text(
        json.dumps(
            {
                "product": {
                    "purpose": "Coordinate a synthetic clinical workflow",
                    "users": ["scheduler", "clinician", "billing specialist"],
                    "outcomes": ["workflow state is visible"],
                    "scope": ["scheduling", "documentation", "claims", "portal", "reporting"],
                },
                "design": {"material": True, "authority": "user-selected design baseline"},
                "external_dependencies": [
                    {"id": "claims API", "availability": "available"},
                    {"id": "telehealth API", "availability": "available"},
                ],
                "acceptance_criteria": [{"id": "AC-1", "statement": "Synthetic workflow passes"}],
                "open_questions": [],
            }
        ),
        encoding="utf-8",
    )
    helper.execute(
        Namespace(
            project_root=tmp_path,
            operation="apply",
            input=update,
            expected_revision=1,
            recorded_at="2026-07-13T12:01:00-04:00",
        )
    )

    assessment = helper.execute(Namespace(project_root=tmp_path, operation="planning-depth"))
    readiness = helper.execute(Namespace(project_root=tmp_path, operation="readiness"))

    assert assessment["status"] == "bmad-recommended"
    assert assessment["requires_user_selection"] is True
    assert assessment["complexity_score"] >= assessment["recommendation_threshold"]
    assert Path(assessment["html_path"]).is_file()
    assert Path(assessment["json_path"]).is_file()
    assert readiness["ready"] is False
    assert "planning-depth-selection-required" in readiness["blocking_issues"]


def test_planning_depth_selection_records_lightweight_variance_and_allows_readiness(
    tmp_path: Path,
) -> None:
    helper = _module()
    helper.execute(
        Namespace(
            project_root=tmp_path,
            operation="initialize",
            project_id="planning-selection",
            entry_path="from-scratch",
            project_type="clinical-web-application",
            phi_answer="yes",
            recorded_at="2026-07-13T12:00:00-04:00",
        )
    )
    update = tmp_path / "selection-update.json"
    update.write_text(
        json.dumps(
            {
                "product": {
                    "purpose": "Coordinate a synthetic clinical workflow",
                    "users": ["scheduler", "clinician"],
                    "outcomes": ["workflow is verified"],
                    "scope": ["scheduling", "documentation", "claims", "portal"],
                },
                "design": {"material": True, "authority": "user-selected baseline"},
                "external_dependencies": [
                    {"id": "claims API", "availability": "available"},
                    {"id": "portal API", "availability": "available"},
                ],
                "acceptance_criteria": [{"id": "AC-1", "statement": "Synthetic workflow passes"}],
                "open_questions": [],
            }
        ),
        encoding="utf-8",
    )
    helper.execute(
        Namespace(
            project_root=tmp_path,
            operation="apply",
            input=update,
            expected_revision=1,
            recorded_at="2026-07-13T12:01:00-04:00",
        )
    )

    selected = helper.execute(
        Namespace(
            project_root=tmp_path,
            operation="planning-depth-select",
            mode="lightweight",
            expected_revision=2,
            recorded_at="2026-07-13T12:02:00-04:00",
        )
    )
    assessment = helper.execute(Namespace(project_root=tmp_path, operation="planning-depth"))
    readiness = helper.execute(Namespace(project_root=tmp_path, operation="readiness"))

    assert selected["revision"] == 3
    assert assessment["status"] == "lightweight-selected"
    assert assessment["accepted_variance"] == "bmad-recommended-but-lightweight-selected"
    assert readiness["ready"] is True


def test_planning_depth_allows_low_complexity_project_without_supervision(tmp_path: Path) -> None:
    helper = _module()
    helper.execute(
        Namespace(
            project_root=tmp_path,
            operation="initialize",
            project_id="small-planning",
            entry_path="from-scratch",
            project_type="cli",
            phi_answer="no",
            recorded_at="2026-07-13T12:00:00-04:00",
        )
    )
    update = tmp_path / "small-update.json"
    update.write_text(
        json.dumps(
            {
                "product": {
                    "purpose": "Format one local file",
                    "users": ["developer"],
                    "outcomes": ["file is formatted"],
                    "scope": ["format command"],
                },
                "acceptance_criteria": [{"id": "AC-1", "statement": "Fixture is formatted"}],
                "open_questions": [],
            }
        ),
        encoding="utf-8",
    )
    helper.execute(
        Namespace(
            project_root=tmp_path,
            operation="apply",
            input=update,
            expected_revision=1,
            recorded_at="2026-07-13T12:01:00-04:00",
        )
    )

    assessment = helper.execute(Namespace(project_root=tmp_path, operation="planning-depth"))
    readiness = helper.execute(Namespace(project_root=tmp_path, operation="readiness"))

    assert assessment["status"] == "lightweight-sufficient"
    assert assessment["requires_user_selection"] is False
    assert readiness["ready"] is True
