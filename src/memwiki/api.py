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
from memwiki.promote import promote_draft
from memwiki.query import QueryResult, query_workspace_structured
from memwiki.sources import preview_source, register_source
from memwiki.workspace import Workspace


@dataclass(frozen=True)
class InitResult:
    workspace: str
    status: str

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


class MemwikiWorkspace:
    """Stable library API for embedding Memwiki in programs and coding-agent tools."""

    def __init__(self, root: Path | str) -> None:
        self.workspace = Workspace(Path(root))

    @property
    def root(self) -> Path:
        return self.workspace.root

    def init(self) -> InitResult:
        workspace = init_workspace(self.root)
        self.workspace = workspace
        return InitResult(workspace=str(workspace.root), status="initialized")

    def ingest(self, path: Path | str, alias: Optional[str] = None, dry_run: bool = False) -> IngestResult:
        source_path = Path(path)
        record = preview_source(self.workspace, source_path, alias=alias) if dry_run else register_source(
            self.workspace, source_path, alias=alias
        )
        return IngestResult(
            source_id=str(record["source_id"]),
            kind=str(record["kind"]),
            sha256=str(record["sha256"]),
            raw_path=record.get("raw_path") if isinstance(record.get("raw_path"), str) else None,
            extraction_status=str(record["extraction_status"]),
            dry_run=dry_run,
            metadata=dict(record["metadata"]),
        )

    def compile(self, source_id: str) -> DraftResult:
        result = compile_source(self.workspace, source_id)
        return DraftResult(draft_id=str(result["draft_id"]), source_id=str(result["source_id"]))

    def promote(self, draft_id: str, check_only: bool = False) -> PromoteResult:
        result = promote_draft(self.workspace, draft_id, check_only=check_only)
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

    def query(self, question: str, draft_page: bool = False) -> QueryResult:
        return query_workspace_structured(self.workspace, question, draft_page=draft_page)

    def resolve(self, object_id: str) -> Dict[str, Any]:
        for name, key, kind in [
            ("sources", "source_id", "source"),
            ("pages", "page_id", "page"),
            ("claims", "claim_id", "claim"),
        ]:
            for record in read_jsonl(self.workspace.path(f"manifests/{name}.jsonl")):
                if record.get(key) == object_id:
                    return {"kind": kind, "id": object_id, "record": record}
        raise KeyError(f"Unknown memwiki object: {object_id}")

    def backlinks(self, object_id: str) -> List[Dict[str, Any]]:
        links = read_jsonl(self.workspace.path("manifests/links.jsonl"))
        return [record for record in links if record.get("to_id") == object_id]

    def docs_check(self) -> DocsStatus:
        return docs_status(self.workspace)

    def docs_draft(self) -> Dict[str, object]:
        return draft_docs(self.workspace)

    def export_static(self, output: Path | str) -> ExportResult:
        result = export_static(self.workspace, Path(output))
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
