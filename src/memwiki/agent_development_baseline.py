from __future__ import annotations

import json
import os
import shutil
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

from memwiki.claims import build_claim
from memwiki.html import escape, render_page
from memwiki.ids import sha256_bytes, slugify, stable_id, utc_now
from memwiki.manifest import read_jsonl, write_jsonl
from memwiki.policy import OperationContext, append_event, require_operation_context
from memwiki.sources import register_source
from memwiki.workspace import Workspace

BASELINE_SCHEMA_VERSION = 1
BASELINE_KIND = "agent_development_project_baseline"
BASELINE_SOURCE_CATEGORY = "agent_development_baseline"
PHI_ANSWERS = {"yes", "no", "unknown"}

BASELINE_SURFACES: Tuple[Tuple[str, str, str], ...] = (
    ("project", "Project Foundation", "Project identity, objective, product specification, and architecture."),
    ("decisions", "Decisions and Questions", "Accepted decisions, open questions, variances, and supersession."),
    ("work", "Work Coordination", "Features, slices, dependencies, assignments, and completion state."),
    ("agents", "Agent Activity", "Agent activity, ownership, context packages, and worker results."),
    ("validation", "Validation and Evidence", "Tests, evaluations, completion evidence, and release proofs."),
    ("blockers", "Blockers and Supervision", "Blocked work, independent continuation, and user supervision requests."),
    ("capabilities", "External Capabilities", "External tools, plugins, APIs, MCP servers, and integrations."),
    ("design", "Design Memory", "Design principles, references, selected targets, screenshots, and variances."),
    ("privacy", "Privacy Posture", "PHI answer, synthetic-data policy, credentials boundary, and security posture."),
    ("memory", "Memory Governance", "Memory proposals, provenance, review status, relationships, and promotion."),
    ("runtime", "Runtime Observations", "Goals, workers, heartbeats, freshness, and source revisions."),
    ("resources", "Resource Measurements", "Token, time, cost, model, and measurement-quality observations."),
)


@dataclass(frozen=True)
class AgentDevelopmentBaselineResult:
    workspace: str
    status: str
    baseline_version: int
    project_id: str
    project_name: str
    objective: str
    handles_phi: str
    synthetic_data_required: bool
    workspace_initialized: bool
    source_id: str
    draft_id: str
    page_count: int
    created_paths: Tuple[str, ...]
    preserved_paths: Tuple[str, ...]

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["created_paths"] = list(self.created_paths)
        payload["preserved_paths"] = list(self.preserved_paths)
        return payload


def normalize_phi_answer(value: str) -> str:
    normalized = value.strip().lower()
    if normalized not in PHI_ANSWERS:
        raise ValueError("handles_phi must be yes, no, or unknown")
    return normalized


def require_existing_writable_project_root(root: Path) -> None:
    if not root.exists():
        raise ValueError(f"Project root must already exist: {root}")
    if not root.is_dir():
        raise ValueError(f"Project root must be a directory: {root}")
    if not os.access(root, os.W_OK | os.X_OK):
        raise ValueError(f"Project root must be writable: {root}")


def _json_bytes(payload: Mapping[str, Any]) -> bytes:
    return (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _write_preserving(path: Path, payload: Mapping[str, Any], created: List[str], preserved: List[str]) -> None:
    expected = _json_bytes(payload)
    relative = str(path)
    if path.exists():
        if not path.is_file() or path.read_bytes() != expected:
            raise ValueError(f"Existing agent-development scaffold differs and will not be overwritten: {path}")
        preserved.append(relative)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_bytes(expected)
    temporary.replace(path)
    created.append(relative)


def _valid_run_registry(path: Path) -> bool:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return (
        isinstance(value, dict)
        and value.get("schema_version") == BASELINE_SCHEMA_VERSION
        and isinstance(value.get("runs"), list)
    )


def _preflight_scaffold(workspace: Workspace, payloads: Mapping[str, Mapping[str, Any]]) -> None:
    for relative, payload in payloads.items():
        path = workspace.path(relative)
        if not path.exists():
            continue
        if relative == ".memwiki/coordinator/runs.json" and _valid_run_registry(path):
            continue
        if not path.is_file() or path.read_bytes() != _json_bytes(payload):
            raise ValueError(
                f"Existing agent-development scaffold differs and will not be overwritten: {path}"
            )


def _baseline_payload(
    root: Path,
    project_name: str,
    objective: str,
    handles_phi: str,
) -> Dict[str, Any]:
    project_id = stable_id("project", str(root))
    synthetic_required = handles_phi in {"yes", "unknown"}
    return {
        "schema_version": BASELINE_SCHEMA_VERSION,
        "baseline_kind": BASELINE_KIND,
        "project": {
            "project_id": project_id,
            "name": project_name,
            "objective": objective,
            "root": str(root),
        },
        "privacy": {
            "handles_phi": handles_phi,
            "development_data_policy": "synthetic_only" if synthetic_required else "project_defined_non_phi",
            "actual_phi_allowed": False,
            "credentials_in_memory_allowed": False,
            "privacy_review_required": handles_phi == "unknown",
        },
        "run_coordination": {
            "registry": ".memwiki/coordinator/runs.json",
            "run_directory_template": ".memwiki/coordinator/runs/{run-key}",
            "one_run_per_directory": True,
            "legacy_workspace_journal_policy": "read_only_compatibility",
        },
        "surfaces": [
            {
                "surface_id": surface_id,
                "title": title,
                "description": description,
                "records": [],
            }
            for surface_id, title, description in BASELINE_SURFACES
        ],
    }


def _scaffold_payloads(source_payload: Mapping[str, Any]) -> Dict[str, Mapping[str, Any]]:
    project = source_payload["project"]
    privacy = source_payload["privacy"]
    surfaces = source_payload["surfaces"]
    run_coordination = source_payload["run_coordination"]
    assert isinstance(project, dict)
    assert isinstance(privacy, dict)
    assert isinstance(surfaces, list)
    assert isinstance(run_coordination, dict)
    return {
        ".agent-development/project-profile.json": {
            "schema_version": BASELINE_SCHEMA_VERSION,
            **project,
        },
        ".agent-development/privacy-profile.json": {
            "schema_version": BASELINE_SCHEMA_VERSION,
            **privacy,
        },
        ".agent-development/memory-surfaces.json": {
            "schema_version": BASELINE_SCHEMA_VERSION,
            "surfaces": surfaces,
        },
        ".agent-development/baseline-source.json": source_payload,
        ".memwiki/coordinator/runs.json": {
            "schema_version": BASELINE_SCHEMA_VERSION,
            "runs": [],
        },
        ".memwiki/coordinator/layout.json": {
            "schema_version": BASELINE_SCHEMA_VERSION,
            "project_id": project["project_id"],
            **run_coordination,
        },
    }


def _registered_source(workspace: Workspace, digest: str) -> Optional[Dict[str, Any]]:
    for record in read_jsonl(workspace.path("manifests/sources.jsonl")):
        if record.get("sha256") == digest and record.get("source_category") == BASELINE_SOURCE_CATEGORY:
            return record
    return None


def _render_surface_page(
    *,
    title: str,
    description: str,
    surface_id: str,
    page_id: str,
    claim: Mapping[str, Any],
    source_id: str,
    generated_at: str,
) -> str:
    claim_id = str(claim["claim_id"])
    body = f"""
    <section id="purpose">
      <h2>Baseline Purpose</h2>
      <p>{escape(description)}</p>
      <p>This surface is intentionally empty until project evidence is proposed and reviewed.</p>
    </section>
    <section id="{escape(claim_id)}" class="claim" data-claim-id="{escape(claim_id)}">
      <h2>Source-backed Baseline Claim</h2>
      <p>{escape(str(claim['text']))}</p>
      <aside class="provenance">
        <strong>Provenance:</strong>
        <code>{escape(source_id)}#surfaces.{escape(surface_id)}</code>
      </aside>
    </section>
"""
    return render_page(
        title=title,
        page_id=page_id,
        page_type="concept",
        body=body,
        metadata={
            "memwiki:baselineVersion": BASELINE_SCHEMA_VERSION,
            "memwiki:surfaceId": surface_id,
            "memwiki:sourceId": source_id,
            "memwiki:claims": [claim_id],
        },
        generated_at=generated_at,
    )


def _create_baseline_draft(
    workspace: Workspace,
    source: Mapping[str, Any],
    source_payload: Mapping[str, Any],
) -> Tuple[str, int, bool]:
    source_id = str(source["source_id"])
    draft_id = stable_id("draft", BASELINE_KIND, str(BASELINE_SCHEMA_VERSION), source_id)
    draft_root = workspace.path(f"drafts/{draft_id}")
    if draft_root.exists():
        metadata_path = draft_root / "draft.json"
        if not metadata_path.is_file():
            raise ValueError(f"Existing baseline draft is incomplete and will not be overwritten: {draft_root}")
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        if metadata.get("kind") != BASELINE_KIND or metadata.get("source_id") != source_id:
            raise ValueError(f"Existing baseline draft identity does not match: {draft_root}")
        return draft_id, len(read_jsonl(draft_root / "manifests/pages.jsonl")), False

    staging = workspace.path(f"drafts/.{draft_id}.tmp")
    if staging.exists():
        shutil.rmtree(staging)
    wiki_root = staging / "wiki"
    manifests_root = staging / "manifests"
    wiki_root.mkdir(parents=True)
    manifests_root.mkdir(parents=True)
    pages: List[Dict[str, Any]] = []
    claims: List[Dict[str, Any]] = []
    links: List[Dict[str, Any]] = []
    generated_at = str(source.get("ingested_at", "1970-01-01T00:00:00+00:00"))
    sensitivity = "synthetic" if source_payload["privacy"]["handles_phi"] in {"yes", "unknown"} else "general"

    for index, surface in enumerate(source_payload["surfaces"]):
        surface_id = str(surface["surface_id"])
        title = str(surface["title"])
        description = str(surface["description"])
        page_id = stable_id("page", source_id, surface_id)
        html_path = f"baseline-{slugify(surface_id)}.html"
        claim = build_claim(
            source_id,
            description,
            "json",
            locator_value=f"surfaces.{index}.description",
            sensitivity=sensitivity,
            source_category=BASELINE_SOURCE_CATEGORY,
        )
        claim["provenance"]["generated_at"] = generated_at
        pages.append(
            {
                "page_id": page_id,
                "title": title,
                "slug": slugify(f"baseline-{surface_id}"),
                "page_type": "concept",
                "review_status": "draft",
                "html_path": html_path,
                "sensitivity": sensitivity,
                "baseline_surface": surface_id,
            }
        )
        claims.append(claim)
        links.append({"from_id": page_id, "to_id": claim["claim_id"], "relationship": "supported_by"})
        (wiki_root / html_path).write_text(
            _render_surface_page(
                title=title,
                description=description,
                surface_id=surface_id,
                page_id=page_id,
                claim=claim,
                source_id=source_id,
                generated_at=generated_at,
            ),
            encoding="utf-8",
        )

    write_jsonl(manifests_root / "pages.jsonl", pages)
    write_jsonl(manifests_root / "claims.jsonl", claims)
    write_jsonl(manifests_root / "links.jsonl", links)
    (staging / "draft.json").write_bytes(
        _json_bytes(
            {
                "draft_id": draft_id,
                "kind": BASELINE_KIND,
                "schema_version": BASELINE_SCHEMA_VERSION,
                "source_id": source_id,
                "created_at": generated_at,
                "page_count": len(pages),
            }
        )
    )
    staging.replace(draft_root)
    return draft_id, len(pages), True


def initialize_agent_development_baseline(
    workspace: Workspace,
    *,
    handles_phi: str,
    project_name: Optional[str] = None,
    objective: str = "",
    context: Optional[OperationContext] = None,
    workspace_initialized: bool = False,
) -> AgentDevelopmentBaselineResult:
    workspace.require()
    require_operation_context(workspace.config_path, "initialize_agent_development_project", context)
    normalized_phi = normalize_phi_answer(handles_phi)
    resolved_name = (project_name or workspace.root.name).strip()
    if not resolved_name:
        raise ValueError("project_name must not be empty")
    resolved_objective = objective.strip()
    source_payload = _baseline_payload(workspace.root, resolved_name, resolved_objective, normalized_phi)
    project = source_payload["project"]
    assert isinstance(project, dict)
    project_id = str(project["project_id"])
    state_path = workspace.path(".agent-development/baseline.json")
    expected_identity = {
        "baseline_version": BASELINE_SCHEMA_VERSION,
        "project_id": project_id,
        "project_name": resolved_name,
        "objective": resolved_objective,
        "handles_phi": normalized_phi,
    }

    if state_path.exists():
        state = json.loads(state_path.read_text(encoding="utf-8"))
        if any(state.get(key) != value for key, value in expected_identity.items()):
            raise ValueError("Agent-development baseline is already initialized with different inputs")
        draft_id = str(state["draft_id"])
        source_id = str(state["source_id"])
        draft_root = workspace.path(f"drafts/{draft_id}")
        if not draft_root.is_dir():
            raise ValueError(f"Agent-development baseline references a missing draft: {draft_id}")
        existing_paths = tuple(
            str(workspace.path(path))
            for path in sorted(_scaffold_payloads(source_payload))
        ) + (str(state_path), str(draft_root))
        return AgentDevelopmentBaselineResult(
            workspace=str(workspace.root),
            status="already_initialized",
            baseline_version=BASELINE_SCHEMA_VERSION,
            project_id=project_id,
            project_name=resolved_name,
            objective=resolved_objective,
            handles_phi=normalized_phi,
            synthetic_data_required=normalized_phi in {"yes", "unknown"},
            workspace_initialized=False,
            source_id=source_id,
            draft_id=draft_id,
            page_count=int(state["page_count"]),
            created_paths=(),
            preserved_paths=existing_paths,
        )

    scaffold_payloads = _scaffold_payloads(source_payload)
    _preflight_scaffold(workspace, scaffold_payloads)
    created: List[str] = []
    preserved: List[str] = []
    for relative, payload in scaffold_payloads.items():
        path = workspace.path(relative)
        if relative == ".memwiki/coordinator/runs.json" and path.exists():
            preserved.append(str(path))
            continue
        _write_preserving(workspace.path(relative), payload, created, preserved)

    source_path = workspace.path(".agent-development/baseline-source.json")
    digest = sha256_bytes(source_path.read_bytes())
    source = _registered_source(workspace, digest)
    if source is None:
        source = register_source(
            workspace,
            source_path,
            alias="agent-development-baseline",
            source_category=BASELINE_SOURCE_CATEGORY,
            context=context,
        )
        created.extend([str(workspace.path(str(source["raw_path"]))), "manifests/sources.jsonl"])
    else:
        preserved.append(str(workspace.path(str(source["raw_path"]))))

    draft_id, page_count, draft_created = _create_baseline_draft(workspace, source, source_payload)
    draft_path = workspace.path(f"drafts/{draft_id}")
    (created if draft_created else preserved).append(str(draft_path))
    state = {
        "schema_version": BASELINE_SCHEMA_VERSION,
        **expected_identity,
        "source_id": str(source["source_id"]),
        "draft_id": draft_id,
        "page_count": page_count,
        "initialized_at": utc_now(),
    }
    _write_preserving(state_path, state, created, preserved)
    append_event(
        workspace.root,
        "agent_development_baseline_initialized",
        {
            "baseline_version": BASELINE_SCHEMA_VERSION,
            "project_id": project_id,
            "handles_phi": normalized_phi,
            "synthetic_data_required": normalized_phi in {"yes", "unknown"},
            "source_id": source["source_id"],
            "draft_id": draft_id,
            "page_count": page_count,
        },
        context,
    )
    return AgentDevelopmentBaselineResult(
        workspace=str(workspace.root),
        status="initialized",
        baseline_version=BASELINE_SCHEMA_VERSION,
        project_id=project_id,
        project_name=resolved_name,
        objective=resolved_objective,
        handles_phi=normalized_phi,
        synthetic_data_required=normalized_phi in {"yes", "unknown"},
        workspace_initialized=workspace_initialized,
        source_id=str(source["source_id"]),
        draft_id=draft_id,
        page_count=page_count,
        created_paths=tuple(sorted(set(created))),
        preserved_paths=tuple(sorted(set(preserved))),
    )
