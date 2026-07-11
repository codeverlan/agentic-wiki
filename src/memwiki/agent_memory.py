from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from memwiki.claims import build_claim
from memwiki.content_safety import validate_safe_memory_content
from memwiki.html import escape, render_page
from memwiki.ids import sha256_bytes, stable_id, utc_now
from memwiki.manifest import read_jsonl, write_jsonl
from memwiki.policy import (
    OperationContext,
    append_event,
    client_record_id,
    require_operation_context,
    sensitivity,
)
from memwiki.sources import register_source
from memwiki.workspace import Workspace

PROHIBITED_VALUE_FIELDS = {
    "credential_value",
    "ephi_value",
    "password_value",
    "phi_value",
    "raw_ephi",
    "raw_phi",
    "raw_secret",
    "raw_token",
    "secret_value",
    "token_value",
}

SCREEN_REFERENCE_FIELDS = [
    "screen_id",
    "route_or_surface",
    "feature",
    "user_role",
    "viewport",
    "application_state",
    "source_path",
    "sha256",
    "captured_at",
    "capture_method",
    "reference_status",
    "related_components",
    "applicable_design_principles",
    "commit_or_revision",
    "supersedes",
    "contains_phi",
    "synthetic_or_deidentified_status",
]


def _load_object(path: Path | str, label: str) -> Dict[str, Any]:
    source = Path(path)
    if not source.exists():
        raise ValueError(f"{label} does not exist: {source}")
    value = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return value


def _require_schema_version_one(value: Dict[str, Any], label: str) -> None:
    if value.get("schema_version") != 1:
        raise ValueError(f"{label} schema_version must be 1")


def _require_non_empty_string(value: Dict[str, Any], field: str, label: str) -> str:
    item = value.get(field)
    if not isinstance(item, str) or not item.strip():
        raise ValueError(f"{label} {field} must be a non-empty string")
    return item


def _reject_prohibited_fields(value: object, path: str) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            field = str(key)
            if field in PROHIBITED_VALUE_FIELDS:
                raise ValueError(f"Agent memory must not store prohibited value field: {path}.{field}")
            _reject_prohibited_fields(item, f"{path}.{field}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _reject_prohibited_fields(item, f"{path}[{index}]")


def validate_memory_record(value: object, path: str = "memory") -> None:
    _reject_prohibited_fields(value, path)
    validate_safe_memory_content(value)


@dataclass(frozen=True)
class PhiDevelopmentPolicy:
    answer: str
    development_data_mode: str
    synthetic_only: bool
    requires_supervision: bool

    def allows_evidence(self, evidence_status: str) -> bool:
        if not self.synthetic_only:
            return True
        return evidence_status in {"synthetic", "verified-deidentified"}

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)


def phi_policy_for(answer: str) -> PhiDevelopmentPolicy:
    normalized = answer.strip().lower()
    policies = {
        "yes": PhiDevelopmentPolicy(
            answer="yes",
            development_data_mode="synthetic-or-verified-deidentified-only",
            synthetic_only=True,
            requires_supervision=False,
        ),
        "no": PhiDevelopmentPolicy(
            answer="no",
            development_data_mode="general-project-policy",
            synthetic_only=False,
            requires_supervision=False,
        ),
        "unknown": PhiDevelopmentPolicy(
            answer="unknown",
            development_data_mode="precautionary-synthetic-only-until-answered",
            synthetic_only=True,
            requires_supervision=True,
        ),
    }
    if normalized not in policies:
        raise ValueError("PHI answer must be yes, no, or unknown")
    return policies[normalized]


@dataclass(frozen=True)
class ScreenReference:
    screen_id: str
    route_or_surface: str
    feature: str
    user_role: str
    viewport: str
    application_state: str
    source_path: str
    sha256: str
    captured_at: str
    capture_method: str
    reference_status: str
    related_components: List[str]
    applicable_design_principles: List[str]
    commit_or_revision: str
    supersedes: List[str]
    contains_phi: bool
    synthetic_or_deidentified_status: str
    approved_by: Optional[str] = None
    approved_at: Optional[str] = None
    approval_authority: Optional[str] = None

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)


def create_screen_reference(
    metadata: Dict[str, Any],
    *,
    screenshot_root: Path | str | None = None,
) -> ScreenReference:
    validate_memory_record(metadata, "screen_reference")
    missing = [field for field in SCREEN_REFERENCE_FIELDS if field not in metadata]
    if missing:
        raise ValueError(f"Screen reference missing required fields: {', '.join(missing)}")
    if not isinstance(metadata.get("contains_phi"), bool):
        raise ValueError("Screen reference contains_phi must be a boolean")
    if metadata.get("contains_phi") is True:
        raise ValueError("Screen references must not contain PHI")
    status = metadata.get("reference_status")
    if status not in {"proposed", "approved", "rejected", "superseded"}:
        raise ValueError("Screen reference has invalid reference_status")
    source_path = Path(str(metadata.get("source_path", "")))
    if source_path.is_absolute() or ".." in source_path.parts:
        raise ValueError("Screen reference source_path must be a confined relative path")
    digest = metadata.get("sha256")
    if not isinstance(digest, str) or len(digest) != 64:
        raise ValueError("Screen reference sha256 must be a lowercase SHA-256 digest")
    if any(character not in "0123456789abcdef" for character in digest):
        raise ValueError("Screen reference sha256 must be a lowercase SHA-256 digest")
    if screenshot_root is not None:
        root = Path(screenshot_root).resolve()
        screenshot = root / source_path
        current = root
        for part in source_path.parts:
            current = current / part
            if current.is_symlink():
                raise ValueError("Screen reference source_path must not contain symlinks")
        try:
            screenshot.resolve().relative_to(root)
        except ValueError:
            raise ValueError("Screen reference source_path must remain inside screenshot storage") from None
        if not screenshot.is_file():
            raise ValueError("Screen reference screenshot file does not exist")
        if sha256_bytes(screenshot.read_bytes()) != digest:
            raise ValueError("Screen reference sha256 does not match screenshot bytes")
    for field in ["related_components", "applicable_design_principles", "supersedes"]:
        if not isinstance(metadata.get(field), list) or not all(isinstance(item, str) for item in metadata[field]):
            raise ValueError(f"Screen reference {field} must be a list of strings")
    if str(metadata.get("screen_id")) in metadata.get("supersedes", []):
        raise ValueError("Screen reference must not supersede itself")
    approval_fields = ["approved_by", "approved_at", "approval_authority"]
    if status == "approved" and any(not str(metadata.get(field, "")).strip() for field in approval_fields):
        raise ValueError("Approved screen reference requires supervised approval metadata")
    evidence_status = str(metadata.get("synthetic_or_deidentified_status", ""))
    if evidence_status not in {"synthetic", "verified-deidentified"}:
        raise ValueError("Screen references must be synthetic or verified-deidentified")
    values = {field: metadata[field] for field in SCREEN_REFERENCE_FIELDS}
    values.update({field: metadata.get(field) for field in approval_fields})
    return ScreenReference(**values)


@dataclass(frozen=True)
class DesignVariance:
    rationale: str
    affected_components: List[str]


@dataclass(frozen=True)
class VisualValidationResult:
    status: str
    baseline_reference_id: str
    observed_sha256: str
    replaces_baseline: bool
    variance: Optional[DesignVariance] = None

    def to_dict(self) -> Dict[str, object]:
        data = asdict(self)
        return data


def validate_visual_reference(
    *,
    baseline: ScreenReference,
    observed_sha256: str,
    material_mismatch: bool,
    rationale: str = "",
    affected_components: Optional[List[str]] = None,
) -> VisualValidationResult:
    if baseline.reference_status != "approved" or not baseline.approval_authority:
        raise ValueError("Visual validation requires an approved screen reference baseline")
    if len(observed_sha256) != 64 or any(character not in "0123456789abcdef" for character in observed_sha256):
        raise ValueError("Observed visual hash must be a lowercase SHA-256 digest")
    variance = None
    status = "validated"
    if material_mismatch:
        if observed_sha256 == baseline.sha256:
            raise ValueError("Material visual mismatch cannot use the baseline hash")
        if not rationale.strip() or not affected_components:
            raise ValueError("Material visual variance requires rationale and affected components")
        status = "variance"
        variance = DesignVariance(
            rationale=rationale,
            affected_components=list(affected_components or []),
        )
    return VisualValidationResult(
        status=status,
        baseline_reference_id=baseline.screen_id,
        observed_sha256=observed_sha256,
        replaces_baseline=False,
        variance=variance,
    )


@dataclass(frozen=True)
class DesignContext:
    screen_references: List[Dict[str, Any]]
    excluded_superseded_ids: List[str]

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)


def compile_design_context(*, references: List[Dict[str, Any]]) -> DesignContext:
    approved = [reference for reference in references if reference.get("reference_status") == "approved"]
    superseded_ids = {
        str(target)
        for reference in approved
        for target in reference.get("supersedes", [])
        if isinstance(target, str)
    }
    active: List[Dict[str, Any]] = []
    excluded: List[str] = []
    for reference in references:
        validate_memory_record(reference, "design_context")
        screen_id = str(reference.get("screen_id", ""))
        if reference.get("reference_status") == "superseded" or screen_id in superseded_ids:
            excluded.append(str(reference.get("screen_id", "")))
        elif reference.get("reference_status") == "approved":
            active.append(dict(reference))
    return DesignContext(screen_references=active, excluded_superseded_ids=sorted(set(excluded)))


@dataclass(frozen=True)
class AgentMemoryObservationResult:
    event_path: str
    event_id: str
    event_type: str
    record_id: str
    source_id: str = ""
    draft_id: str = ""
    page_id: str = ""
    duplicate: bool = False

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)


def observe_agent_memory(
    event_path: Path | str,
    *,
    workspace: Optional[Workspace] = None,
    context: Optional[OperationContext] = None,
) -> AgentMemoryObservationResult:
    event = _load_object(event_path, "Agent memory event")
    validate_memory_record(event, "agent_memory_event")
    for field in ["schema_version", "event_id", "event_type", "occurred_at", "record"]:
        if field not in event:
            raise ValueError(f"Agent memory event missing required field: {field}")
    _require_schema_version_one(event, "Agent memory event")
    _require_non_empty_string(event, "event_id", "Agent memory event")
    _require_non_empty_string(event, "event_type", "Agent memory event")
    _require_non_empty_string(event, "occurred_at", "Agent memory event")
    record = event["record"]
    if not isinstance(record, dict) or not str(record.get("record_id", "")):
        raise ValueError("Agent memory event record must include record_id")
    resolved_event_path = str(Path(event_path))
    event_id = str(event["event_id"])
    event_type = str(event["event_type"])
    record_id = str(record["record_id"])
    if workspace is None or not workspace.config_path.exists():
        return AgentMemoryObservationResult(
            event_path=resolved_event_path,
            event_id=event_id,
            event_type=event_type,
            record_id=record_id,
        )

    require_operation_context(workspace.config_path, "agent_memory_observe", context)

    source_path = Path(event_path)
    digest = sha256_bytes(source_path.read_bytes())
    existing = next(
        (
            item
            for item in read_jsonl(workspace.path("manifests/sources.jsonl"))
            if item.get("sha256") == digest
        ),
        None,
    )
    duplicate = existing is not None
    if existing is not None and existing.get("source_category") != "agent_memory_event":
        raise ValueError(
            "Agent memory event source category conflict: "
            f"existing source is {existing.get('source_category', '<unset>')}"
        )
    source_record = existing or register_source(
        workspace,
        source_path,
        source_category="agent_memory_event",
        context=context,
    )
    source_id = str(source_record["source_id"])
    page_id = stable_id("page", "agent-memory", source_id, str(event["event_id"]))
    draft_id = stable_id("draft", "agent-memory", source_id, str(event["event_id"]))
    draft_root = workspace.path(f"drafts/{draft_id}")
    wiki_root = draft_root / "wiki"
    manifests_root = draft_root / "manifests"
    if not duplicate or not draft_root.exists():
        wiki_root.mkdir(parents=True, exist_ok=True)
        manifests_root.mkdir(parents=True, exist_ok=True)
        summary = str(record.get("summary", f"Observed agent memory record {record['record_id']}"))
        claim = build_claim(
            source_id,
            summary,
            "json",
            locator_value="extracted/text.txt",
            sensitivity=sensitivity(workspace.config_path),
            client_record_id=client_record_id(workspace.config_path),
            source_category="agent_memory_event",
        )
        html = render_page(
            title=f"Agent Memory: {record['record_id']}",
            page_id=page_id,
            page_type="concept",
            body=(
                '<section id="observation"><h2>Observation</h2>'
                f"<p>{escape(summary)}</p><dl><dt>Event</dt><dd>{escape(str(event['event_id']))}</dd>"
                f"<dt>Record</dt><dd>{escape(str(record['record_id']))}</dd></dl></section>"
            ),
            metadata={
                "memwiki:agentMemoryType": str(record.get("kind", "observation")),
                "memwiki:eventId": str(event["event_id"]),
                "memwiki:sourceId": source_id,
            },
        )
        (wiki_root / f"{page_id}.html").write_text(html, encoding="utf-8")
        write_jsonl(
            manifests_root / "pages.jsonl",
            [{
                "page_id": page_id,
                "title": f"Agent Memory: {record['record_id']}",
                "slug": f"agent-memory-{record['record_id']}",
                "page_type": "concept",
                "review_status": "draft",
                "html_path": f"{page_id}.html",
                "sensitivity": sensitivity(workspace.config_path),
            }],
        )
        write_jsonl(manifests_root / "claims.jsonl", [claim])
        write_jsonl(
            manifests_root / "links.jsonl",
            [{"from_id": page_id, "to_id": claim["claim_id"], "relationship": "supported_by"}],
        )
        (draft_root / "draft.json").write_text(
            json.dumps(
                {
                    "draft_id": draft_id,
                    "kind": "agent_memory_observation",
                    "created_at": utc_now(),
                    "source_id": source_id,
                    "page_id": page_id,
                    "event_id": event["event_id"],
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        append_event(
            workspace.root,
            "agent_memory_observe",
            {"event_id": event["event_id"], "source_id": source_id, "draft_id": draft_id},
            context,
        )
    return AgentMemoryObservationResult(
        event_path=resolved_event_path,
        event_id=event_id,
        event_type=event_type,
        record_id=record_id,
        source_id=source_id,
        draft_id=draft_id,
        page_id=page_id,
        duplicate=duplicate,
    )


@dataclass(frozen=True)
class AgentContextResult:
    memory_state_path: str
    request_path: str
    request_id: str
    selected_record_ids: List[str]
    records: List[Dict[str, Any]]
    selected_bytes: int
    truncated: bool
    omitted_record_count: int

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)


def build_agent_context(memory_state_path: Path | str, request_path: Path | str) -> AgentContextResult:
    state = _load_object(memory_state_path, "Agent memory state")
    request = _load_object(request_path, "Agent context request")
    validate_memory_record(state, "agent_memory_state")
    validate_memory_record(request, "agent_context_request")
    _require_schema_version_one(state, "Agent memory state")
    _require_schema_version_one(request, "Agent context request")
    for field in ["request_id", "objective"]:
        if field not in request:
            raise ValueError(f"Agent context request missing required field: {field}")
        _require_non_empty_string(request, field, "Agent context request")
    records = state.get("records")
    if not isinstance(records, list):
        raise ValueError("Agent memory state records must be a list")
    requested_tags = {str(tag) for tag in request.get("tags", [])}
    limit = request.get("max_records", 50)
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1 or limit > 200:
        raise ValueError("Agent context max_records must be an integer from 1 through 200")
    max_bytes = request.get("max_bytes", 65_536)
    if not isinstance(max_bytes, int) or isinstance(max_bytes, bool) or max_bytes < 1 or max_bytes > 1_000_000:
        raise ValueError("Agent context max_bytes must be an integer from 1 through 1000000")
    eligible: List[Dict[str, Any]] = []
    for item in records:
        if not isinstance(item, dict) or item.get("status") == "superseded":
            continue
        tags = {str(tag) for tag in item.get("tags", [])}
        if not requested_tags or requested_tags.intersection(tags):
            eligible.append(dict(item))
    selected: List[Dict[str, Any]] = []
    selected_bytes = 0
    for item in eligible:
        item_bytes = len(json.dumps(item, sort_keys=True, separators=(",", ":")).encode("utf-8"))
        if len(selected) >= limit or selected_bytes + item_bytes > max_bytes:
            break
        selected.append(item)
        selected_bytes += item_bytes
    omitted_record_count = len(eligible) - len(selected)
    return AgentContextResult(
        memory_state_path=str(Path(memory_state_path)),
        request_path=str(Path(request_path)),
        request_id=str(request.get("request_id", "")),
        selected_record_ids=[str(item.get("record_id", "")) for item in selected],
        records=selected,
        selected_bytes=selected_bytes,
        truncated=omitted_record_count > 0,
        omitted_record_count=omitted_record_count,
    )


@dataclass(frozen=True)
class AgentMemoryImpactResult:
    memory_state_path: str
    changed_record_id: str
    impacted_record_ids: List[str]

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class AgentMemoryProposalResult:
    proposal_path: str
    proposal_id: str
    source_id: str
    draft_id: str
    page_id: str
    record_ids: List[str]
    duplicate: bool

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)


def propose_agent_memory(
    workspace: Workspace,
    delta_path: Path | str,
    *,
    context: Optional[OperationContext] = None,
) -> AgentMemoryProposalResult:
    workspace.require()
    require_operation_context(workspace.config_path, "agent_memory_propose", context)
    source_path = Path(delta_path)
    delta = _load_object(source_path, "Agent memory proposal")
    validate_memory_record(delta, "agent_memory_proposal")
    for field in ["schema_version", "proposal_id", "proposed_at", "records"]:
        if field not in delta:
            raise ValueError(f"Agent memory proposal missing required field: {field}")
    _require_schema_version_one(delta, "Agent memory proposal")
    _require_non_empty_string(delta, "proposal_id", "Agent memory proposal")
    _require_non_empty_string(delta, "proposed_at", "Agent memory proposal")
    records = delta["records"]
    if not isinstance(records, list) or not records:
        raise ValueError("Agent memory proposal records must be a non-empty list")
    for index, record in enumerate(records, start=1):
        if not isinstance(record, dict):
            raise ValueError(f"Agent memory proposal record {index} must be an object")
        for field in ["record_id", "kind", "summary"]:
            if not str(record.get(field, "")):
                raise ValueError(f"Agent memory proposal record {index} missing required field: {field}")
    record_ids_in_packet = {str(record["record_id"]) for record in records}
    supersession_graph: Dict[str, List[str]] = {}
    for record in records:
        record_id = str(record["record_id"])
        supersedes = record.get("supersedes", [])
        if not isinstance(supersedes, list) or not all(isinstance(item, str) and item for item in supersedes):
            raise ValueError(f"Agent memory proposal record {record_id} supersedes must be a list of record IDs")
        supersession_graph[record_id] = [item for item in supersedes if item in record_ids_in_packet]

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(record_id: str) -> None:
        if record_id in visiting:
            raise ValueError("Agent memory proposal contains a supersession cycle")
        if record_id in visited:
            return
        visiting.add(record_id)
        for target_id in supersession_graph.get(record_id, []):
            visit(target_id)
        visiting.remove(record_id)
        visited.add(record_id)

    for record_id in sorted(supersession_graph):
        visit(record_id)

    digest = sha256_bytes(source_path.read_bytes())
    existing = next(
        (
            item
            for item in read_jsonl(workspace.path("manifests/sources.jsonl"))
            if item.get("sha256") == digest
        ),
        None,
    )
    duplicate = existing is not None
    if existing is not None and existing.get("source_category") != "agent_memory_proposal":
        raise ValueError(
            "Agent memory proposal source category conflict: "
            f"existing source is {existing.get('source_category', '<unset>')}"
        )
    source_record = existing or register_source(
        workspace,
        source_path,
        source_category="agent_memory_proposal",
        context=context,
    )
    source_id = str(source_record["source_id"])
    proposal_id = str(delta["proposal_id"])
    draft_id = stable_id("draft", "agent-memory-proposal", source_id, proposal_id)
    page_id = stable_id("page", "agent-memory-proposal", source_id, proposal_id)
    draft_root = workspace.path(f"drafts/{draft_id}")
    record_ids = [str(record["record_id"]) for record in records]
    if not duplicate or not draft_root.exists():
        wiki_root = draft_root / "wiki"
        manifests_root = draft_root / "manifests"
        wiki_root.mkdir(parents=True, exist_ok=True)
        manifests_root.mkdir(parents=True, exist_ok=True)
        claims: List[Dict[str, Any]] = []
        links: List[Dict[str, Any]] = []
        body_items: List[str] = []
        for record in records:
            record_id = str(record["record_id"])
            summary = str(record["summary"])
            claim = build_claim(
                source_id,
                summary,
                "json",
                locator_value="extracted/text.txt",
                sensitivity=sensitivity(workspace.config_path),
                client_record_id=client_record_id(workspace.config_path),
                source_category="agent_memory_proposal",
            )
            claim["record_id"] = record_id
            claim["memory_kind"] = str(record["kind"])
            claims.append(claim)
            links.append({"from_id": page_id, "to_id": claim["claim_id"], "relationship": "supported_by"})
            for superseded_id in record.get("supersedes", []):
                links.append({"from_id": record_id, "to_id": str(superseded_id), "relationship": "supersedes"})
            body_items.append(f"<li><strong>{escape(record_id)}</strong>: {escape(summary)}</li>")
        html = render_page(
            title=f"Agent Memory Proposal: {proposal_id}",
            page_id=page_id,
            page_type="concept",
            body='<main><section id="proposal"><h2>Proposed Memory</h2><ul>'
            + "".join(body_items)
            + "</ul></section></main>",
            metadata={
                "memwiki:agentMemoryType": "proposal",
                "memwiki:proposalId": proposal_id,
                "memwiki:sourceId": source_id,
                "memwiki:recordIds": record_ids,
            },
        )
        (wiki_root / f"{page_id}.html").write_text(html, encoding="utf-8")
        write_jsonl(
            manifests_root / "pages.jsonl",
            [{
                "page_id": page_id,
                "title": f"Agent Memory Proposal: {proposal_id}",
                "slug": f"agent-memory-proposal-{proposal_id}",
                "page_type": "concept",
                "review_status": "draft",
                "html_path": f"{page_id}.html",
                "sensitivity": sensitivity(workspace.config_path),
            }],
        )
        write_jsonl(manifests_root / "claims.jsonl", claims)
        write_jsonl(manifests_root / "links.jsonl", links)
        (draft_root / "draft.json").write_text(
            json.dumps(
                {
                    "draft_id": draft_id,
                    "kind": "agent_memory_proposal",
                    "created_at": utc_now(),
                    "source_id": source_id,
                    "page_id": page_id,
                    "proposal_id": proposal_id,
                    "record_ids": record_ids,
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        append_event(
            workspace.root,
            "agent_memory_propose",
            {"proposal_id": proposal_id, "source_id": source_id, "draft_id": draft_id},
            context,
        )
    return AgentMemoryProposalResult(
        proposal_path=str(source_path),
        proposal_id=proposal_id,
        source_id=source_id,
        draft_id=draft_id,
        page_id=page_id,
        record_ids=record_ids,
        duplicate=duplicate,
    )


def assess_agent_memory_impact(
    memory_state_path: Path | str,
    changed_record_id: str,
) -> AgentMemoryImpactResult:
    state = _load_object(memory_state_path, "Agent memory state")
    validate_memory_record(state, "agent_memory_state")
    _require_schema_version_one(state, "Agent memory state")
    records = state.get("records")
    if not isinstance(records, list):
        raise ValueError("Agent memory state records must be a list")
    by_id = {
        str(item.get("record_id")): item
        for item in records
        if isinstance(item, dict) and item.get("record_id")
    }
    if changed_record_id not in by_id:
        raise ValueError(f"Unknown changed agent memory record: {changed_record_id}")
    impacted = set()
    pending = [changed_record_id]
    while pending:
        current = pending.pop(0)
        neighbors = {str(value) for value in by_id[current].get("related_record_ids", [])}
        neighbors.update(
            record_id
            for record_id, item in by_id.items()
            if current in [str(value) for value in item.get("related_record_ids", [])]
        )
        for neighbor in neighbors:
            if neighbor != changed_record_id and neighbor not in impacted:
                impacted.add(neighbor)
                if neighbor in by_id:
                    pending.append(neighbor)
    impacted.discard(changed_record_id)
    return AgentMemoryImpactResult(
        memory_state_path=str(Path(memory_state_path)),
        changed_record_id=changed_record_id,
        impacted_record_ids=sorted(impacted),
    )


__all__ = [
    "AgentContextResult",
    "AgentMemoryImpactResult",
    "AgentMemoryObservationResult",
    "AgentMemoryProposalResult",
    "DesignContext",
    "DesignVariance",
    "PhiDevelopmentPolicy",
    "ScreenReference",
    "VisualValidationResult",
    "assess_agent_memory_impact",
    "build_agent_context",
    "compile_design_context",
    "create_screen_reference",
    "observe_agent_memory",
    "phi_policy_for",
    "propose_agent_memory",
    "validate_memory_record",
    "validate_visual_reference",
]
