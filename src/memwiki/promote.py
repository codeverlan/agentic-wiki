from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any, Dict, List

from memwiki.html import render_contradictions, render_index
from memwiki.linter import lint_workspace
from memwiki.manifest import append_jsonl, read_jsonl
from memwiki.policy import OperationContext, append_event, require_operation_context
from memwiki.workspace import Workspace


def _accepted(record: Dict[str, Any]) -> Dict[str, Any]:
    updated = dict(record)
    if updated.get("review_status") == "draft":
        updated["review_status"] = "accepted"
    return updated


def _append_manifest_records(workspace: Workspace, draft_root: Path, name: str) -> List[Dict[str, Any]]:
    records = [_accepted(record) for record in read_jsonl(draft_root / "manifests" / f"{name}.jsonl")]
    for record in records:
        append_jsonl(workspace.path(f"manifests/{name}.jsonl"), record)
    return records


def promote_draft(
    workspace: Workspace,
    draft_id: str,
    check_only: bool = False,
    context: OperationContext | None = None,
) -> Dict[str, Any]:
    workspace.require()
    require_operation_context(workspace.config_path, "promote", context)
    draft_root = workspace.path(f"drafts/{draft_id}")
    if not draft_root.exists():
        raise ValueError(f"Draft does not exist: {draft_id}")
    result = lint_workspace(workspace, base=draft_root)
    if not result.ok:
        raise ValueError("\n".join(result.errors))
    if check_only:
        append_event(
            workspace.root,
            "promote_check",
            {"draft_id": draft_id, "result": "valid"},
            context,
        )
        return {"draft_id": draft_id, "promoted_pages": 0, "check_only": True}

    promoted_pages: List[Dict[str, Any]] = []
    if (draft_root / "wiki").exists():
        for path in (draft_root / "wiki").glob("*.html"):
            shutil.copy2(path, workspace.path(f"wiki/{path.name}"))
        promoted_pages.extend(_append_manifest_records(workspace, draft_root, "pages"))
    if (draft_root / "manifests/claims.jsonl").exists():
        _append_manifest_records(workspace, draft_root, "claims")
    if (draft_root / "manifests/links.jsonl").exists():
        _append_manifest_records(workspace, draft_root, "links")
    if (draft_root / "docs").exists():
        for path in (draft_root / "docs").glob("*.html"):
            shutil.copy2(path, workspace.path(f"docs/{path.name}"))

    pages = read_jsonl(workspace.path("manifests/pages.jsonl"))
    claims = read_jsonl(workspace.path("manifests/claims.jsonl"))
    workspace.path("wiki/index.html").write_text(render_index(pages), encoding="utf-8")
    workspace.path("wiki/contradictions.html").write_text(render_contradictions(claims), encoding="utf-8")
    append_event(
        workspace.root,
        "promote",
        {"draft_id": draft_id, "pages": [p.get("page_id") for p in promoted_pages]},
        context,
    )
    return {"draft_id": draft_id, "promoted_pages": len(promoted_pages), "check_only": False}
