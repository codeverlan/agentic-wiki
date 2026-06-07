from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Tuple

from memwiki.claims import build_claim, build_clinical_guidance_claim
from memwiki.html import escape, render_client_record_page, render_source_page
from memwiki.ids import slugify, stable_id, utc_now
from memwiki.manifest import read_jsonl, write_jsonl
from memwiki.policy import OperationContext, append_event, is_clinical_phi, require_operation_context
from memwiki.workspace import Workspace


def _source_by_id(workspace: Workspace, source_id: str) -> Dict[str, Any]:
    for record in read_jsonl(workspace.path("manifests/sources.jsonl")):
        if record.get("source_id") == source_id:
            return record
    raise ValueError(f"Unknown source_id: {source_id}")


def _excerpt(text: str, limit: int = 900) -> str:
    normalized = " ".join(text.split())
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 1].rstrip() + "..."


def _source_json_payload(workspace: Workspace, source: Dict[str, Any]) -> Dict[str, Any] | None:
    raw_path = source.get("raw_path")
    if not isinstance(raw_path, str) or not raw_path.endswith(".json"):
        return None
    path = workspace.path(raw_path)
    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        return None
    return payload


def _list_html(items: List[str]) -> str:
    if not items:
        return "<p>None recorded.</p>"
    return "<ul>" + "".join(f"<li>{escape(item)}</li>" for item in items) + "</ul>"


def _pairs_html(pairs: List[Tuple[str, str]]) -> str:
    rows = "".join(f"<dt>{escape(label)}</dt><dd>{escape(value)}</dd>" for label, value in pairs if value)
    if not rows:
        return "<p>No structured details available.</p>"
    return f"<dl>{rows}</dl>"


def _problem_summary(problem: Dict[str, Any]) -> str:
    parts = [str(problem.get("summary", "")).strip()]
    severity = str(problem.get("severity", "")).strip()
    frequency = str(problem.get("frequency", "")).strip()
    if severity:
        parts.append(f"severity: {severity}")
    if frequency:
        parts.append(f"frequency: {frequency}")
    return "; ".join(part for part in parts if part)


def _goal_summary(goal: Dict[str, Any]) -> str:
    statement = str(goal.get("goal_statement", "")).strip()
    target_date = str(goal.get("target_date", "")).strip()
    status = str(goal.get("progress_status", "")).strip()
    details = []
    if target_date:
        details.append(f"target {target_date}")
    if status:
        details.append(status.replace("_", " "))
    return f"{statement} ({'; '.join(details)})" if details else statement


def _session_summary(note: Dict[str, Any]) -> str:
    date = str(note.get("session_date", "")).strip()
    session_type = str(note.get("session_type", "")).replace("_", " ").strip()
    response = str(note.get("client_response", "")).strip()
    return f"{date}: {session_type}. {response}".strip()


def _build_client_record_sections(client_record: Dict[str, Any]) -> Dict[str, str]:
    demographics = client_record.get("demographics", {}) if isinstance(client_record.get("demographics"), dict) else {}
    admin = (
        client_record.get("administrative_context", {})
        if isinstance(client_record.get("administrative_context"), dict)
        else {}
    )
    social = (
        client_record.get("clinical_history", {}).get("social_history", {})
        if isinstance(client_record.get("clinical_history"), dict)
        and isinstance(client_record.get("clinical_history", {}).get("social_history"), dict)
        else {}
    )
    diagnoses = (
        client_record.get("diagnostic_profile", {}).get("current_diagnostic_impressions", [])
        if isinstance(client_record.get("diagnostic_profile"), dict)
        else []
    )
    problems = (
        client_record.get("presenting_problems", [])
        if isinstance(client_record.get("presenting_problems"), list)
        else []
    )
    risk = (
        client_record.get("risk_assessment", {})
        if isinstance(client_record.get("risk_assessment"), dict)
        else {}
    )
    evaluation = (
        client_record.get("initial_evaluation", {}) if isinstance(client_record.get("initial_evaluation"), dict) else {}
    )
    plan = (
        client_record.get("treatment_plan", {})
        if isinstance(client_record.get("treatment_plan"), dict)
        else {}
    )
    session_notes = (
        client_record.get("session_notes", [])
        if isinstance(client_record.get("session_notes"), list)
        else []
    )
    care_team = admin.get("care_team", []) if isinstance(admin.get("care_team"), list) else []
    goals = plan.get("smart_goals", []) if isinstance(plan.get("smart_goals"), list) else []

    care_team_items = []
    for member in care_team:
        if not isinstance(member, dict):
            continue
        care_team_items.append(
            (
                f"{member.get('role', 'team member').replace('_', ' ')}: "
                f"{member.get('synthetic_name', 'unknown')} "
                f"({member.get('organization', 'unknown organization')})"
            )
        )
    diagnosis_items = [
        f"{item.get('label', 'Unlabeled diagnosis')} ({item.get('code', 'no code')})"
        for item in diagnoses
        if isinstance(item, dict)
    ]
    risk_items = [
        f"Risk level: {risk.get('risk_level', 'unknown')}",
        f"Suicidal ideation: {risk.get('suicidal_ideation', 'unknown')}",
        f"Self-harm: {risk.get('self_harm', 'unknown')}",
        f"Homicidal ideation: {risk.get('homicidal_ideation', 'unknown')}",
        f"Safety plan status: {risk.get('safety_plan_status', 'unknown')}",
    ]

    overview_pairs = [
        ("Case label", str(client_record.get("pseudonymous_case_label", ""))),
        ("Client record", str(client_record.get("client_record_id", ""))),
        ("Preferred name", str(demographics.get("preferred_name", ""))),
        ("Age at intake", str(demographics.get("age_at_intake", ""))),
        ("Pronouns", str(demographics.get("pronouns", ""))),
        ("Intake date", str(admin.get("intake_date", ""))),
        ("Referral source", str(admin.get("referral_source", ""))),
        ("Living situation", str(social.get("living_situation", ""))),
    ]
    overview_html = f"""
    <section id="overview-snapshot">
      <h2>Client Snapshot</h2>
      {_pairs_html(overview_pairs)}
    </section>
    <section id="overview-presenting-problems">
      <h2>Presenting Problems</h2>
      {_list_html([_problem_summary(problem) for problem in problems if isinstance(problem, dict)])}
    </section>
    <section id="overview-diagnoses">
      <h2>Current Diagnoses</h2>
      {_list_html(diagnosis_items)}
    </section>
    <section id="overview-risk">
      <h2>Risk Status</h2>
      {_list_html(risk_items)}
    </section>
    <section id="overview-care-team">
      <h2>Care Team</h2>
      {_list_html(care_team_items)}
    </section>
"""

    biopsychosocial = (
        evaluation.get("biopsychosocial_summary", {})
        if isinstance(evaluation.get("biopsychosocial_summary"), dict)
        else {}
    )
    mental_status = (
        evaluation.get("mental_status_exam", {}) if isinstance(evaluation.get("mental_status_exam"), dict) else {}
    )
    assessment_html = f"""
    <section id="assessment-referral">
      <h2>Referral and Intake Summary</h2>
      {_pairs_html([
          ("Reason for referral", str(evaluation.get("reason_for_referral", ""))),
          ("Chief concern", str(evaluation.get("chief_concern", ""))),
          ("History of present illness", str(evaluation.get("history_of_present_illness", ""))),
      ])}
    </section>
    <section id="assessment-biopsychosocial">
      <h2>Biopsychosocial Summary</h2>
      {_pairs_html([
          ("Biological", str(biopsychosocial.get("biological", ""))),
          ("Psychological", str(biopsychosocial.get("psychological", ""))),
          ("Social", str(biopsychosocial.get("social", ""))),
      ])}
      <h3>Strengths</h3>
      {_list_html([str(item) for item in biopsychosocial.get("strengths", []) if isinstance(item, str)])}
      <h3>Barriers</h3>
      {_list_html([str(item) for item in biopsychosocial.get("barriers", []) if isinstance(item, str)])}
    </section>
    <section id="assessment-mental-status">
      <h2>Mental Status</h2>
      {_pairs_html([(key.replace('_', ' ').title(), str(value)) for key, value in mental_status.items()])}
    </section>
"""

    goal_items = []
    for goal in goals:
        if not isinstance(goal, dict):
            continue
        objectives = [
            str(item.get("objective_statement", ""))
            for item in goal.get("objectives", [])
            if isinstance(item, dict)
        ]
        interventions = [str(item) for item in goal.get("planned_interventions", []) if isinstance(item, str)]
        detail = _goal_summary(goal)
        if objectives:
            detail += " Objectives: " + "; ".join(item for item in objectives if item)
        if interventions:
            detail += " Interventions: " + "; ".join(item for item in interventions if item)
        goal_items.append(detail.strip())
    plan_html = f"""
    <section id="plan-structure">
      <h2>Plan Structure</h2>
      {_pairs_html([
          ("Level of care", str(plan.get("level_of_care", ""))),
          ("Service frequency", str(plan.get("service_frequency", ""))),
          ("Estimated duration", str(plan.get("estimated_duration", ""))),
          ("Review due date", str(plan.get("review_due_date", ""))),
      ])}
    </section>
    <section id="plan-strengths-barriers">
      <h2>Strengths and Barriers</h2>
      <h3>Client Strengths</h3>
      {_list_html([str(item) for item in plan.get("client_strengths", []) if isinstance(item, str)])}
      <h3>Barriers to Progress</h3>
      {_list_html([str(item) for item in plan.get("barriers_to_progress", []) if isinstance(item, str)])}
    </section>
    <section id="plan-goals">
      <h2>SMART Goals</h2>
      {_list_html(goal_items)}
    </section>
    <section id="plan-coordination">
      <h2>Care Coordination and Discharge</h2>
      <h3>Care Coordination</h3>
      {_list_html([str(item) for item in plan.get("care_coordination_plan", []) if isinstance(item, str)])}
      <h3>Minimal Discharge Criteria</h3>
      {_list_html([str(item) for item in plan.get("minimal_discharge_criteria", []) if isinstance(item, str)])}
    </section>
"""

    note_items = []
    for note in session_notes:
        if not isinstance(note, dict):
            continue
        interventions = [str(item) for item in note.get("intervention_provided", []) if isinstance(item, str)]
        plan_items = [str(item) for item in note.get("plan", []) if isinstance(item, str)]
        note_body = (
            f"<strong>{escape(str(note.get('session_date', '')))}:</strong> "
            f"{escape(str(note.get('session_type', '')).replace('_', ' '))}"
        )
        response = str(note.get("client_response", "")).strip()
        if response:
            note_body += f"<br>{escape(response)}"
        if interventions:
            note_body += f"<br><em>Interventions:</em> {escape('; '.join(interventions))}"
        if plan_items:
            note_body += f"<br><em>Plan:</em> {escape('; '.join(plan_items))}"
        note_items.append(f"<li>{note_body}</li>")
    sessions_html = """
    <section id="session-timeline">
      <h2>Session Timeline</h2>
      <ul>""" + "".join(note_items) + """</ul>
    </section>
"""

    overview_summary = " ".join(
        [
            str(client_record.get("pseudonymous_case_label", "")).strip(),
            str(evaluation.get("chief_concern", "")).strip(),
            f"Diagnoses: {', '.join(diagnosis_items)}." if diagnosis_items else "",
            f"Current risk level is {risk.get('risk_level', 'unknown')}." if risk else "",
        ]
    ).strip()
    assessment_summary = " ".join(
        [
            str(evaluation.get("history_of_present_illness", "")).strip(),
            str(biopsychosocial.get("psychological", "")).strip(),
            str(biopsychosocial.get("social", "")).strip(),
        ]
    ).strip()
    plan_summary = " ".join(
        [
            f"Level of care: {plan.get('level_of_care', '')}.",
            f"Service frequency: {plan.get('service_frequency', '')}.",
            "Goals: " + "; ".join(_goal_summary(goal) for goal in goals if isinstance(goal, dict)),
        ]
    ).strip()
    sessions_summary = " ".join(_session_summary(note) for note in session_notes if isinstance(note, dict)).strip()

    return {
        "overview_html": overview_html,
        "assessment_html": assessment_html,
        "plan_html": plan_html,
        "sessions_html": sessions_html,
        "overview_summary": overview_summary,
        "assessment_summary": assessment_summary,
        "plan_summary": plan_summary,
        "sessions_summary": sessions_summary,
    }


def _compile_client_record_source(
    workspace: Workspace,
    source: Dict[str, Any],
    payload: Dict[str, Any],
    context: OperationContext | None,
) -> Dict[str, Any]:
    client_record = payload.get("client_record")
    if not isinstance(client_record, dict):
        raise ValueError("client_record source is missing payload.client_record")
    source_id = str(source["source_id"])
    client_record_id = str(source.get("client_record_id") or client_record.get("client_record_id") or "")
    case_label = str(client_record.get("pseudonymous_case_label") or client_record_id or source_id)
    slug_root = slugify(client_record_id or source_id)
    page_specs = [
        ("overview", f"{case_label} Overview", "client", "overview_html", "overview_summary", "client_record.overview"),
        (
            "assessment",
            f"{case_label} Assessment",
            "assessment",
            "assessment_html",
            "assessment_summary",
            "client_record.initial_evaluation",
        ),
        (
            "treatment-plan",
            f"{case_label} Treatment Plan",
            "treatment_plan",
            "plan_html",
            "plan_summary",
            "client_record.treatment_plan",
        ),
        (
            "sessions",
            f"{case_label} Session Timeline",
            "session_notes",
            "sessions_html",
            "sessions_summary",
            "client_record.session_notes",
        ),
    ]
    sections = _build_client_record_sections(client_record)
    page_records: List[Dict[str, Any]] = []
    claims: List[Dict[str, Any]] = []
    links: List[Dict[str, Any]] = []
    page_paths_by_name: Dict[str, str] = {}
    page_ids_by_name: Dict[str, str] = {}
    fact_claims_by_page: Dict[str, Dict[str, Any]] = {}
    guidance_claims_by_page: Dict[str, Dict[str, Any]] = {}
    draft_id = stable_id("draft", source_id, utc_now())
    draft_root = workspace.path(f"drafts/{draft_id}")
    (draft_root / "wiki").mkdir(parents=True, exist_ok=True)
    (draft_root / "manifests").mkdir(parents=True, exist_ok=True)

    for name, title, page_type, _html_key, summary_key, locator_value in page_specs:
        slug = slugify(f"{slug_root}-{name}")
        page_id = stable_id("page", source_id, slug)
        claim = build_claim(
            source_id,
            sections[summary_key] or f"{case_label} {name.replace('-', ' ')} imported from client record JSON.",
            "json",
            locator_value=locator_value,
            sensitivity=str(source.get("sensitivity", "general")),
            client_record_id=client_record_id or None,
            source_category=str(source.get("source_category", "client_record")),
            clinical_claim_type="source_fact",
        )
        guidance_claim = build_clinical_guidance_claim(
            source_id,
            claim,
            client_record_id=client_record_id,
            source_category=str(source.get("source_category", "client_record")),
        )
        html_path = f"{slug}.html"
        page_records.append(
            {
                "page_id": page_id,
                "title": title,
                "slug": slug,
                "page_type": page_type,
                "review_status": "draft",
                "html_path": html_path,
                "sensitivity": str(source.get("sensitivity", "general")),
                "client_record_id": client_record_id,
            }
        )
        claims.extend([claim, guidance_claim])
        links.append({"from_id": page_id, "to_id": claim["claim_id"], "relationship": "contains_claim"})
        links.append({"from_id": page_id, "to_id": guidance_claim["claim_id"], "relationship": "contains_guidance"})
        page_paths_by_name[name] = html_path
        page_ids_by_name[name] = page_id
        fact_claims_by_page[name] = claim
        guidance_claims_by_page[name] = guidance_claim

    for name, title, page_type, html_key, _, _ in page_specs:
        related_pages = [
            {"title": other_title, "href": page_paths_by_name[other_name]}
            for other_name, other_title, _, _, _, _ in page_specs
            if other_name != name
        ]
        page_html = render_client_record_page(
            title=title,
            page_id=page_ids_by_name[name],
            page_type=page_type,
            source_id=source_id,
            source_record=source,
            client_record_id=client_record_id,
            sections_html=sections[html_key],
            claim=fact_claims_by_page[name],
            guidance_claim=guidance_claims_by_page[name],
            related_pages=related_pages,
        )
        (draft_root / "wiki" / page_paths_by_name[name]).write_text(page_html, encoding="utf-8")

    for name, _, _, _, _, _ in page_specs:
        from_id = page_ids_by_name[name]
        for other_name, _, _, _, _, _ in page_specs:
            if other_name == name:
                continue
            links.append(
                {
                    "from_id": from_id,
                    "to_id": page_ids_by_name[other_name],
                    "relationship": "related_page",
                }
            )

    write_jsonl(draft_root / "manifests/pages.jsonl", page_records)
    write_jsonl(draft_root / "manifests/claims.jsonl", claims)
    write_jsonl(draft_root / "manifests/links.jsonl", links)
    (draft_root / "draft.json").write_text(
        json.dumps(
            {
                "draft_id": draft_id,
                "source_id": source_id,
                "client_record_id": client_record_id,
                "created_at": utc_now(),
                "kind": "client-record-import",
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    append_event(
        workspace.root,
        "compile",
        {"source_id": source_id, "draft_id": draft_id, "claims": len(claims), "pages": len(page_records)},
        context,
    )
    return {"draft_id": draft_id, "source_id": source_id}


def compile_source(
    workspace: Workspace,
    source_id: str,
    context: OperationContext | None = None,
) -> Dict[str, Any]:
    workspace.require()
    require_operation_context(workspace.config_path, "compile", context)
    source = _source_by_id(workspace, source_id)
    source_category = str(source.get("source_category", ""))
    if source.get("kind") == "json" and source_category == "client_record":
        payload = _source_json_payload(workspace, source)
        if payload is not None and isinstance(payload.get("client_record"), dict):
            return _compile_client_record_source(workspace, source, payload, context)
    text_path = workspace.path(f".memwiki/extracted/{source_id}/text.txt")
    if not text_path.exists():
        raise ValueError(f"Missing extraction artifact for {source_id}")
    text = text_path.read_text(encoding="utf-8")
    clinical = is_clinical_phi(workspace.config_path)
    title = f"Clinical Source: {source_id}" if clinical else f"Source: {Path(str(source['origin'])).name}"
    slug = slugify(f"{source_id}-source-summary" if clinical else f"{source_id}-{Path(str(source['origin'])).stem}")
    page_id = stable_id("page", source_id, slug)
    claim = build_claim(
        source_id,
        _excerpt(text, 500) or "No extractable text.",
        source["kind"],
        sensitivity=str(source.get("sensitivity", "general")),
        client_record_id=source.get("client_record_id") if isinstance(source.get("client_record_id"), str) else None,
        source_category=source.get("source_category") if isinstance(source.get("source_category"), str) else None,
        clinical_claim_type="source_fact" if clinical else None,
    )
    guidance_claim = (
        build_clinical_guidance_claim(
            source_id,
            claim,
            client_record_id=str(source["client_record_id"]),
            source_category=str(source.get("source_category", "clinical_note")),
        )
        if clinical
        else None
    )
    html_path = f"{slug}.html"
    page = {
        "page_id": page_id,
        "title": title,
        "slug": slug,
        "page_type": "source",
        "review_status": "draft",
        "html_path": html_path,
    }
    if clinical:
        page["sensitivity"] = "phi"
        page["client_record_id"] = str(source["client_record_id"])
    link = {"from_id": page_id, "to_id": claim["claim_id"], "relationship": "contains_claim"}
    links = [link]
    if guidance_claim is not None:
        links.append(
            {
                "from_id": page_id,
                "to_id": guidance_claim["claim_id"],
                "relationship": "contains_guidance",
            }
        )
    draft_id = stable_id("draft", source_id, utc_now())
    draft_root = workspace.path(f"drafts/{draft_id}")
    (draft_root / "wiki").mkdir(parents=True, exist_ok=True)
    (draft_root / "manifests").mkdir(parents=True, exist_ok=True)
    page_html = render_source_page(
        title=title,
        page_id=page_id,
        source_id=source_id,
        claim_id=str(claim["claim_id"]),
        claim_text=str(claim["text"]),
        excerpt=_excerpt(text) or "No extractable text was available for this source.",
        source_record=source,
        guidance_claim=guidance_claim,
    )
    (draft_root / "wiki" / html_path).write_text(page_html, encoding="utf-8")
    write_jsonl(draft_root / "manifests/pages.jsonl", [page])
    claims = [claim]
    if guidance_claim is not None:
        claims.append(guidance_claim)
    write_jsonl(draft_root / "manifests/claims.jsonl", claims)
    write_jsonl(draft_root / "manifests/links.jsonl", links)
    (draft_root / "draft.json").write_text(
        json.dumps(
            {
                "draft_id": draft_id,
                "source_id": source_id,
                "created_at": utc_now(),
                "kind": "source-compile",
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    append_event(
        workspace.root,
        "compile",
        {"source_id": source_id, "draft_id": draft_id, "claims": len(claims)},
        context,
    )
    return {"draft_id": draft_id, "source_id": source_id}
