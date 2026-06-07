import json
import os
import subprocess
from pathlib import Path

import pytest

from agentic_wiki import (
    AgenticWikiWorkspace,
    OperationContext,
)
from agentic_wiki import (
    MemwikiWorkspace as AgenticWikiMemwikiWorkspace,
)
from memwiki import AgenticWikiWorkspace as LegacyAgenticWikiWorkspace
from memwiki import MemwikiWorkspace
from memwiki.html import render_page
from memwiki.manifest import read_jsonl, write_jsonl


def clinical_context() -> OperationContext:
    return OperationContext(
        actor_id="clinician-001",
        actor_role="clinician",
        purpose_of_use="treatment",
        session_id="session-001",
        reason="synthetic test workflow",
    )


def synthetic_client_record_payload(record_id: str = "client-synthetic-001") -> dict[str, object]:
    case_number = record_id.rsplit("-", 1)[-1]
    case_label = f"Synthetic Case {case_number}"
    overview = {
        "problem_id": "problem-001",
        "summary": "anxiety symptoms affecting school and sleep",
        "onset": "symptoms increased over the last 3 months",
        "frequency": "most school days",
        "severity": "moderate",
        "functional_impairment": ["school", "sleep"],
        "client_reported_priority": "high",
    }
    return {
        "template_id": "synthetic-phi-client-record-template-v1",
        "record_policy": {"synthetic": True, "sensitivity": "synthetic", "no_actual_phi": True},
        "dataset_metadata": {"record_count": 1},
        "client_record": {
            "record_id": record_id,
            "synthetic": True,
            "sensitivity": "synthetic",
            "client_record_id": record_id,
            "pseudonymous_case_label": case_label,
            "presenting_problems": [overview],
            "diagnostic_profile": {
                "current_diagnostic_impressions": [
                    {
                        "diagnosis_id": "diagnosis-001",
                        "diagnostic_system": "DSM-5-style synthetic label",
                        "code": "SYN-F41.1",
                        "label": "Generalized anxiety disorder",
                        "status": "provisional",
                        "clinical_rationale_summary": (
                            "Synthetic anxiety symptoms are described across school and sleep domains."
                        ),
                    }
                ]
            },
            "initial_evaluation": {
                "evaluation_date": "2026-01-15",
                "history_of_present_illness": "Synthetic Case 001 reports anxiety and sleep disruption.",
                "mental_status_exam": {"summary": "alert, cooperative, and anxious in a synthetic evaluation"},
                "risk_assessment_summary": "No current safety concerns in this synthetic record.",
                "provenance": {"claim_scope": "initial_evaluation"},
            },
            "treatment_plan": {
                "plan_date": "2026-01-20",
                "level_of_care": "outpatient_therapy",
                "frequency": "weekly",
                "goals": [
                    {
                        "goal_id": "goal-001",
                        "summary": "Reduce anxiety impact on school attendance.",
                        "target_date": "2026-04-20",
                    }
                ],
                "smart_goals": ["Use two coping skills before school on 4 of 5 school days."],
                "provenance": {"claim_scope": "treatment_plan"},
            },
            "session_notes": [
                {
                    "session_id": "session-001",
                    "date": "2026-01-27",
                    "summary": "Reviewed coping practice and school transition plan.",
                    "interventions": ["skills practice", "supportive therapy"],
                    "client_response": "engaged with practice",
                    "plan": "continue weekly coping practice",
                    "provenance": {"claim_scope": "session_note"},
                }
            ],
            "validation_expectations": {"required_top_level_fields": ["record_id", "presenting_problems"]},
        },
    }


def test_agentic_wiki_api_and_cli_aliases_are_available(tmp_path: Path) -> None:
    assert MemwikiWorkspace is AgenticWikiWorkspace
    assert AgenticWikiMemwikiWorkspace is AgenticWikiWorkspace
    assert LegacyAgenticWikiWorkspace is AgenticWikiWorkspace

    wiki = AgenticWikiWorkspace(tmp_path)
    result = wiki.init()

    assert result.status == "initialized"
    capabilities = wiki.capabilities()
    assert capabilities["name"] == "agentic-wiki"
    assert capabilities["compatibility"]["legacy_cli"] == "memwiki"

    repo_root = Path(__file__).resolve().parents[1]
    env = {**os.environ, "UV_NO_SYNC": "1"}
    for command in ["agentic-wiki", "memwiki"]:
        completed = subprocess.run(
            ["uv", "run", "--no-sync", command, "--help"],
            cwd=repo_root,
            env=env,
            check=False,
            capture_output=True,
            text=True,
            timeout=20,
        )
        assert completed.returncode == 0, completed.stderr
        assert "Usage:" in completed.stdout

        docs_check = subprocess.run(
            ["uv", "run", "--no-sync", command, "docs", "check"],
            cwd=repo_root,
            env=env,
            check=False,
            capture_output=True,
            text=True,
            timeout=20,
        )
        assert docs_check.returncode == 0, docs_check.stderr
        assert "OK" in docs_check.stdout


def test_legacy_memwiki_capabilities_mirror_phi_policy_contract(tmp_path: Path) -> None:
    wiki = AgenticWikiWorkspace(tmp_path)
    wiki.init()
    tools = wiki.capabilities()["tools"]
    policy_fields = {"clinical_phi_requires_context", "clinical_phi_policy"}

    for canonical_name in [name for name in tools if name.startswith("agentic_wiki.")]:
        legacy_name = canonical_name.replace("agentic_wiki.", "memwiki.", 1)
        assert legacy_name in tools
        canonical_policy = {
            key: value for key, value in tools[canonical_name].items() if key in policy_fields
        }
        legacy_policy = {key: value for key, value in tools[legacy_name].items() if key in policy_fields}
        assert legacy_policy == canonical_policy

    assert tools["agentic_wiki.export_static"]["clinical_phi_requires_context"] is True
    assert tools["memwiki.export_static"]["clinical_phi_requires_context"] is True
    assert tools["agentic_wiki.export_static"]["clinical_phi_policy"] == "blocked unless deidentified"
    assert tools["memwiki.export_static"]["clinical_phi_policy"] == "blocked unless deidentified"


def test_clinical_phi_workspace_requires_context_and_sanitizes_audit_records(tmp_path: Path) -> None:
    wiki = AgenticWikiWorkspace(tmp_path)
    wiki.init(
        profile="clinical_phi",
        client_record_id="client-synthetic-001",
        local_encrypted_storage_attested=True,
    )
    source = tmp_path / "Jane Doe 1970-01-01 progress note.txt"
    source.write_text(
        "Synthetic client reports sleep disruption and medication adherence concerns.",
        encoding="utf-8",
    )

    with pytest.raises(PermissionError, match="Operation context is required"):
        wiki.ingest(source)

    context = clinical_context()
    ingest = wiki.ingest(
        source,
        alias="Jane Doe DOB 1970-01-01",
        source_category="progress_note",
        context=context,
    )

    assert "jane" not in ingest.source_id.lower()
    assert "1970" not in ingest.source_id
    source_record = read_jsonl(tmp_path / "manifests/sources.jsonl")[0]
    assert source_record["origin"] == "redacted-local-origin"
    assert source_record["client_record_id"] == "client-synthetic-001"
    assert source_record["sensitivity"] == "phi"
    assert source_record["source_category"] == "progress_note"

    ingest_event = read_jsonl(tmp_path / "manifests/events.jsonl")[-1]
    dumped_event = json.dumps(ingest_event).lower()
    assert ingest_event["details"]["actor_id"] == "clinician-001"
    assert ingest_event["details"]["purpose_of_use"] == "treatment"
    assert "sleep disruption" not in dumped_event
    assert "jane" not in dumped_event

    with pytest.raises(PermissionError, match="Static export is blocked"):
        wiki.export_static(tmp_path / "site", context=context)

    config = tmp_path / ".memwiki/config.toml"
    config.write_text(
        config.read_text(encoding="utf-8").replace("allow_remote = false", "allow_remote = true"),
        encoding="utf-8",
    )
    with pytest.raises(PermissionError, match="Remote model adapters are blocked"):
        wiki.query("sleep disruption", context=context)


def test_clinical_workflow_keeps_guidance_reviewable_and_source_cited(tmp_path: Path) -> None:
    wiki = AgenticWikiWorkspace(tmp_path)
    wiki.init(
        profile="clinical_phi",
        client_record_id="client-synthetic-002",
        local_encrypted_storage_attested=True,
    )
    context = clinical_context()
    source = tmp_path / "synthetic-progress-note.txt"
    source.write_text(
        "Synthetic client reports persistent insomnia and medication adherence concerns.",
        encoding="utf-8",
    )
    ingest = wiki.ingest(source, source_category="progress_note", context=context)

    bad_draft = tmp_path / "drafts/bad-guidance"
    (bad_draft / "wiki").mkdir(parents=True)
    (bad_draft / "manifests").mkdir()
    (bad_draft / "wiki/bad.html").write_text(
        render_page(
            title="Bad Guidance",
            page_id="page_bad_guidance",
            page_type="concept",
            body="<section><h2>Bad</h2><p>Uncited accepted guidance.</p></section>",
            metadata={"memwiki:pageType": "concept"},
        ),
        encoding="utf-8",
    )
    write_jsonl(
        bad_draft / "manifests/pages.jsonl",
        [
            {
                "page_id": "page_bad_guidance",
                "title": "Bad Guidance",
                "slug": "bad-guidance",
                "page_type": "concept",
                "review_status": "accepted",
                "html_path": "bad.html",
                "sensitivity": "phi",
                "client_record_id": "client-synthetic-002",
            }
        ],
    )
    write_jsonl(
        bad_draft / "manifests/claims.jsonl",
        [
            {
                "claim_id": "claim_bad_guidance",
                "text": "Accepted clinical guidance without cited source claims.",
                "source_id": ingest.source_id,
                "confidence": 0.5,
                "review_status": "accepted",
                "clinical_claim_type": "clinical_guidance",
                "guidance_type": "decision_support",
                "sensitivity": "phi",
                "client_record_id": "client-synthetic-002",
                "provenance": {
                    "generated_at": "2026-05-29T00:00:00+00:00",
                    "model_adapter": "dry-run",
                    "source_locator": {"type": "text", "value": "extracted/text.txt"},
                },
            }
        ],
    )
    write_jsonl(bad_draft / "manifests/links.jsonl", [])

    with pytest.raises(ValueError, match="accepted clinical guidance"):
        wiki.promote("bad-guidance", context=context)

    draft = wiki.compile(ingest.source_id, context=context)
    draft_claims = read_jsonl(tmp_path / "drafts" / draft.draft_id / "manifests/claims.jsonl")
    claim_types = {claim["clinical_claim_type"] for claim in draft_claims}
    assert claim_types == {"source_fact", "clinical_guidance"}
    guidance = next(claim for claim in draft_claims if claim["clinical_claim_type"] == "clinical_guidance")
    fact = next(claim for claim in draft_claims if claim["clinical_claim_type"] == "source_fact")
    assert guidance["review_status"] == "needs_review"
    assert guidance["cited_claim_ids"] == [fact["claim_id"]]
    assert guidance["provenance"]["derived_from_claim_ids"] == [fact["claim_id"]]

    check = wiki.promote(draft.draft_id, check_only=True, context=context)
    assert check.check_only is True
    promoted = wiki.promote(draft.draft_id, context=context)
    assert promoted.promoted_pages == 1

    accepted_claims = read_jsonl(tmp_path / "manifests/claims.jsonl")
    assert any(claim["review_status"] == "accepted" for claim in accepted_claims)
    assert any(claim["review_status"] == "needs_review" for claim in accepted_claims)

    query = wiki.query("insomnia adherence", context=context)
    assert "Evidence summary" in query.answer
    assert "Decision support" in query.answer
    assert ingest.source_id in query.citations


def test_client_record_json_import_creates_structured_client_pages(tmp_path: Path) -> None:
    wiki = AgenticWikiWorkspace(tmp_path)
    wiki.init(
        profile="clinical_phi",
        client_record_id="client-synthetic-001",
        local_encrypted_storage_attested=True,
    )
    context = clinical_context()
    source = tmp_path / "client-record.json"
    source.write_text(json.dumps(synthetic_client_record_payload()), encoding="utf-8")

    ingest = wiki.ingest(source, source_category="client_record", context=context)
    draft = wiki.compile(ingest.source_id, context=context)
    promoted = wiki.promote(draft.draft_id, context=context)

    assert promoted.promoted_pages == 4

    pages = read_jsonl(tmp_path / "manifests/pages.jsonl")
    assert {page["page_type"] for page in pages} == {
        "client",
        "assessment",
        "treatment_plan",
        "session_notes",
    }
    titles = {page["title"] for page in pages}
    assert "Synthetic Case 001 Overview" in titles
    assert "Synthetic Case 001 Assessment" in titles
    assert "Synthetic Case 001 Treatment Plan" in titles
    assert "Synthetic Case 001 Session Timeline" in titles

    claims = read_jsonl(tmp_path / "manifests/claims.jsonl")
    fact_claims = [claim for claim in claims if claim.get("clinical_claim_type") == "source_fact"]
    guidance_claims = [claim for claim in claims if claim.get("clinical_claim_type") == "clinical_guidance"]
    assert len(fact_claims) == 4
    assert len(guidance_claims) == 4
    assert {claim["client_record_id"] for claim in claims} == {"client-synthetic-001"}
    assert {
        claim["provenance"]["source_locator"]["value"] for claim in fact_claims
    } == {
        "client_record.overview",
        "client_record.initial_evaluation",
        "client_record.treatment_plan",
        "client_record.session_notes",
    }

    overview_page = tmp_path / "wiki/client-synthetic-001-overview.html"
    assert overview_page.exists()
    overview_html = overview_page.read_text(encoding="utf-8")
    assert "Presenting Problems" in overview_html
    assert "Current Diagnoses" in overview_html
    assert "client-synthetic-001-assessment.html" in overview_html

    query = wiki.query("Synthetic Case 001 anxiety treatment plan", context=context)
    assert any(match.title == "Synthetic Case 001 Overview" for match in query.matches)
    assert ingest.source_id in query.citations
