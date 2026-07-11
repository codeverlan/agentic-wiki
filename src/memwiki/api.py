from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from memwiki.agent_development import (
    AgentHandoffDigestMetadata,
    AgentHandoffDigestRenderResult,
    AgentIncidentLogRenderResult,
    AgentRunMetadata,
    AgentRunStateDraftResult,
    AgentRunStateRenderResult,
    draft_agent_run_state,
    render_agent_handoff_digest,
    render_agent_incident_log,
    render_agent_run_state,
)
from memwiki.agent_memory import (
    AgentContextResult,
    AgentMemoryImpactResult,
    AgentMemoryObservationResult,
    AgentMemoryProposalResult,
    assess_agent_memory_impact,
    build_agent_context,
    observe_agent_memory,
    propose_agent_memory,
)
from memwiki.capabilities import CAPABILITIES
from memwiki.compiler import compile_source
from memwiki.docs import DocsStatus, docs_status, draft_docs
from memwiki.exporter import export_static
from memwiki.external_memory import (
    ExternalEntity,
    ExternalEntityKind,
    ExternalMemoryCatalog,
    ExternalRelationship,
    ExternalRelationshipKind,
)
from memwiki.graph import build_graph_index
from memwiki.init import init_workspace
from memwiki.linter import LintResult, lint_workspace
from memwiki.locking import workspace_lock
from memwiki.manifest import read_jsonl
from memwiki.policy import AuthorityVerifier, OperationContext, require_operation_context
from memwiki.promote import promote_draft
from memwiki.query import QueryResult, query_workspace_structured
from memwiki.sources import preview_source, register_source
from memwiki.workspace import Workspace


@dataclass(frozen=True)
class InitResult:
    workspace: str
    status: str
    profile: str = "standard"
    client_record_id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class IngestResult:
    source_id: str
    kind: str
    sha256: str
    raw_path: Optional[str]
    extraction_status: str
    dry_run: bool
    metadata: Dict[str, Any]
    sensitivity: str = "general"
    client_record_id: Optional[str] = None
    source_category: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class DraftResult:
    draft_id: str
    source_id: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PromoteResult:
    draft_id: str
    promoted_pages: int
    check_only: bool

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ExportResult:
    output: str
    pages: int

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ExternalEntityListResult:
    entities: Tuple[ExternalEntity, ...]

    def to_dict(self) -> Dict[str, Any]:
        return {"entities": [entity.to_dict() for entity in self.entities]}


@dataclass(frozen=True)
class ExternalRelationshipListResult:
    relationships: Tuple[ExternalRelationship, ...]

    def to_dict(self) -> Dict[str, Any]:
        return {"relationships": [relationship.to_dict() for relationship in self.relationships]}


@dataclass(frozen=True)
class ExternalMemoryViewResult:
    output: str
    entities: int
    relationships: int

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ExternalPathResult:
    from_id: str
    to_id: str
    relationships: Tuple[ExternalRelationship, ...]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "from_id": self.from_id,
            "to_id": self.to_id,
            "relationships": [relationship.to_dict() for relationship in self.relationships],
        }


@dataclass(frozen=True)
class ExternalImpactItem:
    object_id: str
    depth: int
    via: ExternalRelationshipKind

    def to_dict(self) -> Dict[str, Any]:
        return {"object_id": self.object_id, "depth": self.depth, "via": self.via.value}


@dataclass(frozen=True)
class ExternalImpactResult:
    object_id: str
    items: Tuple[ExternalImpactItem, ...]

    def to_dict(self) -> Dict[str, Any]:
        return {"object_id": self.object_id, "items": [item.to_dict() for item in self.items]}


@dataclass(frozen=True)
class ExternalExplanationResult:
    from_id: str
    to_id: str
    explanation: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class AgenticWikiWorkspace:
    """Stable library API for embedding Agentic Wiki in programs and coding-agent tools."""

    def __init__(self, root: Path | str, authority_verifier: Optional[AuthorityVerifier] = None) -> None:
        self.workspace = Workspace(Path(root))
        self.authority_verifier = authority_verifier

    @property
    def root(self) -> Path:
        return self.workspace.root

    def init(
        self,
        profile: str = "standard",
        client_record_id: Optional[str] = None,
        local_encrypted_storage_attested: bool = False,
    ) -> InitResult:
        workspace = init_workspace(
            self.root,
            profile=profile,
            client_record_id=client_record_id,
            local_encrypted_storage_attested=local_encrypted_storage_attested,
        )
        self.workspace = workspace
        resolved_client_id = client_record_id if profile.replace("-", "_") == "clinical_phi" else None
        if resolved_client_id is None and workspace.path(".memwiki/client.json").exists():
            client_data = json.loads(workspace.path(".memwiki/client.json").read_text(encoding="utf-8"))
            value = client_data.get("client_record_id")
            resolved_client_id = str(value) if isinstance(value, str) else None
        return InitResult(
            workspace=str(workspace.root),
            status="initialized",
            profile=profile.replace("-", "_"),
            client_record_id=resolved_client_id,
        )

    def ingest(
        self,
        path: Path | str,
        alias: Optional[str] = None,
        dry_run: bool = False,
        source_category: Optional[str] = None,
        context: Optional[OperationContext] = None,
    ) -> IngestResult:
        source_path = Path(path)
        record = (
            preview_source(
                self.workspace,
                source_path,
                alias=alias,
                source_category=source_category,
                context=context,
            )
            if dry_run
            else register_source(
                self.workspace,
                source_path,
                alias=alias,
                source_category=source_category,
                context=context,
            )
        )
        return IngestResult(
            source_id=str(record["source_id"]),
            kind=str(record["kind"]),
            sha256=str(record["sha256"]),
            raw_path=record.get("raw_path") if isinstance(record.get("raw_path"), str) else None,
            extraction_status=str(record["extraction_status"]),
            dry_run=dry_run,
            metadata=dict(record["metadata"]),
            sensitivity=str(record.get("sensitivity", "general")),
            client_record_id=record.get("client_record_id")
            if isinstance(record.get("client_record_id"), str)
            else None,
            source_category=record.get("source_category") if isinstance(record.get("source_category"), str) else None,
        )

    def compile(self, source_id: str, context: Optional[OperationContext] = None) -> DraftResult:
        result = compile_source(self.workspace, source_id, context=context)
        return DraftResult(draft_id=str(result["draft_id"]), source_id=str(result["source_id"]))

    def promote(
        self,
        draft_id: str,
        check_only: bool = False,
        context: Optional[OperationContext] = None,
    ) -> PromoteResult:
        result = promote_draft(
            self.workspace,
            draft_id,
            check_only=check_only,
            context=context,
            authority_verifier=self.authority_verifier,
        )
        return PromoteResult(
            draft_id=str(result["draft_id"]),
            promoted_pages=int(result["promoted_pages"]),
            check_only=bool(result.get("check_only", False)),
        )

    def lint(self) -> LintResult:
        with workspace_lock(self.workspace, exclusive=False):
            result = lint_workspace(self.workspace)
            if result.ok:
                build_graph_index(self.workspace)
            return result

    def query(
        self,
        question: str,
        draft_page: bool = False,
        context: Optional[OperationContext] = None,
    ) -> QueryResult:
        with workspace_lock(self.workspace, exclusive=False):
            return query_workspace_structured(self.workspace, question, draft_page=draft_page, context=context)

    def resolve(self, object_id: str, context: Optional[OperationContext] = None) -> Dict[str, Any]:
        require_operation_context(self.workspace.config_path, "resolve", context)
        with workspace_lock(self.workspace, exclusive=False):
            for name, key, kind in [
                ("sources", "source_id", "source"),
                ("pages", "page_id", "page"),
                ("claims", "claim_id", "claim"),
            ]:
                for record in read_jsonl(self.workspace.path(f"manifests/{name}.jsonl")):
                    if record.get(key) == object_id:
                        return {"kind": kind, "id": object_id, "record": record}
        raise KeyError(f"Unknown agentic wiki object: {object_id}")

    def backlinks(self, object_id: str, context: Optional[OperationContext] = None) -> List[Dict[str, Any]]:
        require_operation_context(self.workspace.config_path, "backlinks", context)
        with workspace_lock(self.workspace, exclusive=False):
            links = read_jsonl(self.workspace.path("manifests/links.jsonl"))
            return [record for record in links if record.get("to_id") == object_id]

    def register_external_entity(
        self,
        *,
        name: str,
        kind: ExternalEntityKind,
        locator: str,
        version: str,
        publisher: str,
        capabilities: Sequence[str],
        metadata: Mapping[str, Any] = {},
        source_id: Optional[str] = None,
        context: Optional[OperationContext] = None,
    ) -> ExternalEntity:
        require_operation_context(self.workspace.config_path, "register_external_entity", context)
        return ExternalMemoryCatalog(self.workspace).register(
            name=name,
            kind=kind,
            locator=locator,
            version=version,
            publisher=publisher,
            capabilities=capabilities,
            metadata=metadata,
            source_id=source_id,
        )

    def update_external_entity(
        self,
        entity_id: str,
        *,
        expected_revision: int,
        name: Optional[str] = None,
        version: Optional[str] = None,
        capabilities: Optional[Sequence[str]] = None,
        metadata: Optional[Mapping[str, Any]] = None,
        source_id: Optional[str] = None,
        context: Optional[OperationContext] = None,
    ) -> ExternalEntity:
        require_operation_context(self.workspace.config_path, "update_external_entity", context)
        return ExternalMemoryCatalog(self.workspace).update(
            entity_id,
            expected_revision=expected_revision,
            name=name,
            version=version,
            capabilities=capabilities,
            metadata=metadata,
            source_id=source_id,
        )

    def resolve_external_entity(self, entity_id: str, context: Optional[OperationContext] = None) -> ExternalEntity:
        require_operation_context(self.workspace.config_path, "resolve_external_entity", context)
        with workspace_lock(self.workspace, exclusive=False):
            return ExternalMemoryCatalog(self.workspace).resolve(entity_id)

    def list_external_entities(self, context: Optional[OperationContext] = None) -> ExternalEntityListResult:
        require_operation_context(self.workspace.config_path, "list_external_entities", context)
        with workspace_lock(self.workspace, exclusive=False):
            values = ExternalMemoryCatalog(self.workspace).graph_index()["external_entities"]
            return ExternalEntityListResult(tuple(ExternalEntity.from_dict(value) for value in values))

    def relate_external_memory(
        self,
        from_id: str,
        to_id: str,
        relationship: ExternalRelationshipKind,
        *,
        evidence: Mapping[str, Any] = {},
        context: Optional[OperationContext] = None,
    ) -> ExternalRelationship:
        require_operation_context(self.workspace.config_path, "relate_external_memory", context)
        with workspace_lock(self.workspace, exclusive=True):
            return ExternalMemoryCatalog(self.workspace).relate(from_id, to_id, relationship, evidence=evidence)

    def update_external_relationship(
        self,
        relationship_id: str,
        *,
        expected_revision: int,
        relationship: ExternalRelationshipKind,
        evidence: Mapping[str, Any],
        context: Optional[OperationContext] = None,
    ) -> ExternalRelationship:
        require_operation_context(self.workspace.config_path, "update_external_relationship", context)
        with workspace_lock(self.workspace, exclusive=True):
            return ExternalMemoryCatalog(self.workspace).update_relationship(
                relationship_id,
                expected_revision=expected_revision,
                relationship=relationship,
                evidence=evidence,
            )

    def external_neighbors(
        self, object_id: str, context: Optional[OperationContext] = None
    ) -> ExternalRelationshipListResult:
        require_operation_context(self.workspace.config_path, "external_neighbors", context)
        with workspace_lock(self.workspace, exclusive=False):
            values = ExternalMemoryCatalog(self.workspace).neighbors(object_id)
            return ExternalRelationshipListResult(values)

    def external_backlinks(
        self, object_id: str, context: Optional[OperationContext] = None
    ) -> ExternalRelationshipListResult:
        require_operation_context(self.workspace.config_path, "external_backlinks", context)
        with workspace_lock(self.workspace, exclusive=False):
            values = ExternalMemoryCatalog(self.workspace).backlinks(object_id)
            return ExternalRelationshipListResult(values)

    def external_path(
        self,
        from_id: str,
        to_id: str,
        max_depth: int = 8,
        context: Optional[OperationContext] = None,
    ) -> ExternalPathResult:
        require_operation_context(self.workspace.config_path, "external_path", context)
        with workspace_lock(self.workspace, exclusive=False):
            values = ExternalMemoryCatalog(self.workspace).find_path(from_id, to_id, max_depth=max_depth)
            return ExternalPathResult(from_id, to_id, values)

    def external_impact(
        self,
        object_id: str,
        max_depth: int = 4,
        context: Optional[OperationContext] = None,
    ) -> ExternalImpactResult:
        require_operation_context(self.workspace.config_path, "external_impact", context)
        with workspace_lock(self.workspace, exclusive=False):
            values = ExternalMemoryCatalog(self.workspace).impact(object_id, max_depth=max_depth)
            items = tuple(
                ExternalImpactItem(
                    object_id=str(value["object_id"]),
                    depth=int(value["depth"]),
                    via=ExternalRelationshipKind(str(value["via"])),
                )
                for value in values
            )
            return ExternalImpactResult(object_id, items)

    def explain_external_relationship(
        self,
        from_id: str,
        to_id: str,
        context: Optional[OperationContext] = None,
    ) -> ExternalExplanationResult:
        require_operation_context(self.workspace.config_path, "explain_external_relationship", context)
        with workspace_lock(self.workspace, exclusive=False):
            explanation = ExternalMemoryCatalog(self.workspace).explain(from_id, to_id)
            return ExternalExplanationResult(from_id, to_id, explanation)

    def render_external_memory(
        self,
        output: Path | str = ".memwiki/index/external-memory.html",
        context: Optional[OperationContext] = None,
    ) -> ExternalMemoryViewResult:
        require_operation_context(self.workspace.config_path, "render_external_memory", context)
        candidate = Path(output)
        target = candidate.resolve() if candidate.is_absolute() else self.workspace.path(str(candidate)).resolve()
        try:
            target.relative_to(self.root)
        except ValueError:
            raise ValueError("external memory view must remain inside the workspace") from None
        if target.exists() and target.is_symlink():
            raise ValueError("external memory view cannot replace a symlink")
        with workspace_lock(self.workspace, exclusive=True):
            catalog = ExternalMemoryCatalog(self.workspace)
            graph = catalog.graph_index()
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(catalog.render_html(), encoding="utf-8")
            return ExternalMemoryViewResult(
                output=str(target),
                entities=len(graph["external_entities"]),
                relationships=len(graph["external_relationships"]),
            )

    def docs_check(self) -> DocsStatus:
        return docs_status(self.workspace, allow_repo_checkout=True)

    def docs_draft(self, context: Optional[OperationContext] = None) -> Dict[str, object]:
        require_operation_context(self.workspace.config_path, "docs_draft", context)
        return draft_docs(self.workspace)

    def render_agent_run_state(
        self,
        queue_path: Path | str,
        output_path: Path | str,
        *,
        objective: str = "",
        stop_reason: str = "",
        active_branch: str = "",
        commit: str = "",
        remote_pr: str = "",
        branch_ledger: str = "",
    ) -> AgentRunStateRenderResult:
        return render_agent_run_state(
            queue_path,
            output_path,
            AgentRunMetadata(
                objective=objective,
                stop_reason=stop_reason,
                active_branch=active_branch,
                commit=commit,
                remote_pr=remote_pr,
                branch_ledger=branch_ledger,
            ),
            output_root=self.root,
        )

    def render_agent_handoff_digest(
        self,
        queue_path: Path | str,
        output_path: Path | str,
        *,
        objective: str = "",
        trigger: str = "",
        active_authority: str = "",
        resume_command: str = "",
        resume_path: str = "",
    ) -> AgentHandoffDigestRenderResult:
        return render_agent_handoff_digest(
            queue_path,
            output_path,
            AgentHandoffDigestMetadata(
                objective=objective,
                trigger=trigger,
                active_authority=active_authority,
                resume_command=resume_command,
                resume_path=resume_path,
            ),
            output_root=self.root,
        )

    def render_agent_incident_log(
        self,
        incident_log_path: Path | str,
        output_path: Path | str,
    ) -> AgentIncidentLogRenderResult:
        return render_agent_incident_log(incident_log_path, output_path, output_root=self.root)

    def draft_agent_run_state(
        self,
        queue_path: Path | str,
        *,
        objective: str = "",
        stop_reason: str = "",
        active_branch: str = "",
        commit: str = "",
        remote_pr: str = "",
        branch_ledger: str = "",
        context: Optional[OperationContext] = None,
    ) -> AgentRunStateDraftResult:
        return draft_agent_run_state(
            self.workspace,
            queue_path,
            AgentRunMetadata(
                objective=objective,
                stop_reason=stop_reason,
                active_branch=active_branch,
                commit=commit,
                remote_pr=remote_pr,
                branch_ledger=branch_ledger,
            ),
            context=context,
        )

    def observe_agent_memory(
        self,
        event_path: Path | str,
        context: Optional[OperationContext] = None,
    ) -> AgentMemoryObservationResult:
        return observe_agent_memory(event_path, workspace=self.workspace, context=context)

    def build_agent_context(
        self,
        memory_state_path: Path | str,
        request_path: Path | str,
        context: Optional[OperationContext] = None,
    ) -> AgentContextResult:
        require_operation_context(self.workspace.config_path, "agent_memory_context", context)
        return build_agent_context(memory_state_path, request_path)

    def propose_agent_memory(
        self,
        delta_path: Path | str,
        context: Optional[OperationContext] = None,
    ) -> AgentMemoryProposalResult:
        return propose_agent_memory(self.workspace, delta_path, context=context)

    def assess_agent_memory_impact(
        self,
        memory_state_path: Path | str,
        changed_record_id: str,
        context: Optional[OperationContext] = None,
    ) -> AgentMemoryImpactResult:
        require_operation_context(self.workspace.config_path, "agent_memory_impact", context)
        return assess_agent_memory_impact(memory_state_path, changed_record_id)

    def export_static(
        self,
        output: Path | str,
        context: Optional[OperationContext] = None,
        deidentified: bool = False,
    ) -> ExportResult:
        with workspace_lock(self.workspace, exclusive=False):
            result = export_static(self.workspace, Path(output), context=context, deidentified=deidentified)
        pages = result.get("pages", 0)
        if not isinstance(pages, int):
            pages = int(str(pages))
        return ExportResult(output=str(result["output"]), pages=pages)

    def capabilities(self) -> Dict[str, Any]:
        path = self.workspace.path(".memwiki/agent-capabilities.json")
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError(f"Invalid capabilities file: {path}")
            return data
        return CAPABILITIES


MemwikiWorkspace = AgenticWikiWorkspace

__all__ = [
    "AgenticWikiWorkspace",
    "AgentHandoffDigestRenderResult",
    "AgentIncidentLogRenderResult",
    "AgentRunStateDraftResult",
    "AgentRunStateRenderResult",
    "AgentContextResult",
    "AgentMemoryImpactResult",
    "AgentMemoryObservationResult",
    "AgentMemoryProposalResult",
    "DraftResult",
    "ExportResult",
    "ExternalEntity",
    "ExternalEntityKind",
    "ExternalEntityListResult",
    "ExternalExplanationResult",
    "ExternalImpactItem",
    "ExternalImpactResult",
    "ExternalPathResult",
    "ExternalRelationship",
    "ExternalRelationshipKind",
    "ExternalRelationshipListResult",
    "ExternalMemoryViewResult",
    "IngestResult",
    "InitResult",
    "MemwikiWorkspace",
    "PromoteResult",
]
