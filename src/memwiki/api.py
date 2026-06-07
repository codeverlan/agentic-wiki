from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from memwiki.capabilities import CAPABILITIES
from memwiki.compiler import compile_source
from memwiki.docs import DocsStatus, docs_status, draft_docs
from memwiki.exporter import export_static
from memwiki.graph import build_graph_index
from memwiki.init import init_workspace
from memwiki.linter import LintResult, lint_workspace
from memwiki.manifest import read_jsonl
from memwiki.policy import OperationContext, require_operation_context
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


class AgenticWikiWorkspace:
    """Stable library API for embedding Agentic Wiki in programs and coding-agent tools."""

    def __init__(self, root: Path | str) -> None:
        self.workspace = Workspace(Path(root))

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
        record = preview_source(
            self.workspace,
            source_path,
            alias=alias,
            source_category=source_category,
            context=context,
        ) if dry_run else register_source(
            self.workspace,
            source_path,
            alias=alias,
            source_category=source_category,
            context=context,
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
        result = promote_draft(self.workspace, draft_id, check_only=check_only, context=context)
        return PromoteResult(
            draft_id=str(result["draft_id"]),
            promoted_pages=int(result["promoted_pages"]),
            check_only=bool(result.get("check_only", False)),
        )

    def lint(self) -> LintResult:
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
        return query_workspace_structured(self.workspace, question, draft_page=draft_page, context=context)

    def resolve(self, object_id: str, context: Optional[OperationContext] = None) -> Dict[str, Any]:
        require_operation_context(self.workspace.config_path, "resolve", context)
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
        links = read_jsonl(self.workspace.path("manifests/links.jsonl"))
        return [record for record in links if record.get("to_id") == object_id]

    def docs_check(self) -> DocsStatus:
        return docs_status(self.workspace, allow_repo_checkout=True)

    def docs_draft(self, context: Optional[OperationContext] = None) -> Dict[str, object]:
        require_operation_context(self.workspace.config_path, "docs_draft", context)
        return draft_docs(self.workspace)

    def export_static(
        self,
        output: Path | str,
        context: Optional[OperationContext] = None,
        deidentified: bool = False,
    ) -> ExportResult:
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
