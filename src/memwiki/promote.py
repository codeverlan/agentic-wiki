from __future__ import annotations

import os
import shutil
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, List

from memwiki.html import render_contradictions, render_index
from memwiki.linter import lint_workspace
from memwiki.locking import workspace_lock
from memwiki.manifest import append_jsonl, read_jsonl
from memwiki.policy import (
    AuthorityVerifier,
    OperationContext,
    append_event,
    require_operation_context,
    verify_coordinator_authority,
)
from memwiki.sources import verify_source_integrity
from memwiki.workspace import Workspace


@contextmanager
def _promotion_transaction(workspace: Workspace) -> Iterator[None]:
    with workspace_lock(workspace, exclusive=True):
        transaction_root = Path(tempfile.mkdtemp(prefix="promote-", dir=workspace.path(".memwiki")))
        canonical_roots = ["wiki", "docs", "manifests"]
        for name in canonical_roots:
            source = workspace.path(name)
            if source.exists():
                shutil.copytree(source, transaction_root / name)
        try:
            yield
        except BaseException:
            prepared: Dict[str, Path] = {}
            for name in canonical_roots:
                backup = transaction_root / name
                restore_path = transaction_root / f"restore-{name}"
                if backup.exists():
                    shutil.copytree(backup, restore_path)
                else:
                    restore_path.mkdir(parents=True)
                prepared[name] = restore_path
            restored: List[tuple[Path, Path]] = []
            try:
                for name in canonical_roots:
                    destination = workspace.path(name)
                    failed_path = transaction_root / f"failed-{name}"
                    if destination.exists():
                        os.replace(destination, failed_path)
                    try:
                        os.replace(prepared[name], destination)
                    except BaseException:
                        if failed_path.exists():
                            os.replace(failed_path, destination)
                        raise
                    restored.append((destination, failed_path))
            except BaseException:
                for destination, failed_path in reversed(restored):
                    discarded = transaction_root / f"discarded-{destination.name}"
                    if destination.exists():
                        os.replace(destination, discarded)
                    if failed_path.exists():
                        os.replace(failed_path, destination)
                raise
            raise
        finally:
            shutil.rmtree(transaction_root, ignore_errors=True)


def _verify_draft_sources(workspace: Workspace, draft_root: Path) -> bool:
    source_ids = {
        str(record["source_id"])
        for record in read_jsonl(draft_root / "manifests/claims.jsonl")
        if isinstance(record.get("source_id"), str)
    }
    sources = [verify_source_integrity(workspace, source_id) for source_id in sorted(source_ids)]
    return any(str(source.get("source_category", "")).startswith("agent_memory_") for source in sources)


def _already_promoted(workspace: Workspace, draft_id: str) -> bool:
    return any(
        record.get("event_type") == "promote"
        and isinstance(record.get("details"), dict)
        and record["details"].get("draft_id") == draft_id
        for record in read_jsonl(workspace.path("manifests/events.jsonl"))
    )


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
    authority_verifier: AuthorityVerifier | None = None,
) -> Dict[str, Any]:
    workspace.require()
    require_operation_context(workspace.config_path, "promote", context)
    draft_root = workspace.path(f"drafts/{draft_id}")
    if not draft_root.exists():
        raise ValueError(f"Draft does not exist: {draft_id}")
    with _promotion_transaction(workspace):
        protected_agent_memory = _verify_draft_sources(workspace, draft_root)
        result = lint_workspace(workspace, base=draft_root)
        if not result.ok:
            raise ValueError("\n".join(result.errors))
        verified_authority = None
        if protected_agent_memory:
            verified_authority = verify_coordinator_authority(
                context,
                authority_verifier,
                "promote_agent_memory",
                workspace.root,
            )
        if check_only:
            append_event(
                workspace.root,
                "promote_check",
                {"draft_id": draft_id, "result": "valid"},
                context,
            )
            return {"draft_id": draft_id, "promoted_pages": 0, "check_only": True}

        draft_pages = read_jsonl(draft_root / "manifests/pages.jsonl")
        if _already_promoted(workspace, draft_id):
            return {"draft_id": draft_id, "promoted_pages": len(draft_pages), "check_only": False}

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
        event_details: Dict[str, object] = {
            "draft_id": draft_id,
            "pages": [p.get("page_id") for p in promoted_pages],
        }
        if verified_authority is not None:
            event_details["verified_authority"] = verified_authority.audit_details()
        append_event(
            workspace.root,
            "promote",
            event_details,
            context,
        )
        return {"draft_id": draft_id, "promoted_pages": len(promoted_pages), "check_only": False}
