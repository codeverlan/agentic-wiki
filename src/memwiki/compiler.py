from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

from memwiki.claims import build_claim
from memwiki.html import render_source_page
from memwiki.ids import slugify, stable_id, utc_now
from memwiki.manifest import read_jsonl, write_jsonl
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


def compile_source(workspace: Workspace, source_id: str) -> Dict[str, Any]:
    workspace.require()
    source = _source_by_id(workspace, source_id)
    text_path = workspace.path(f".memwiki/extracted/{source_id}/text.txt")
    if not text_path.exists():
        raise ValueError(f"Missing extraction artifact for {source_id}")
    text = text_path.read_text(encoding="utf-8")
    title = f"Source: {Path(str(source['origin'])).name}"
    slug = slugify(f"{source_id}-{Path(str(source['origin'])).stem}")
    page_id = stable_id("page", source_id, slug)
    claim = build_claim(source_id, _excerpt(text, 500) or "No extractable text.", source["kind"])
    html_path = f"{slug}.html"
    page = {
        "page_id": page_id,
        "title": title,
        "slug": slug,
        "page_type": "source",
        "review_status": "draft",
        "html_path": html_path,
    }
    link = {"from_id": page_id, "to_id": claim["claim_id"], "relationship": "contains_claim"}
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
    )
    (draft_root / "wiki" / html_path).write_text(page_html, encoding="utf-8")
    write_jsonl(draft_root / "manifests/pages.jsonl", [page])
    write_jsonl(draft_root / "manifests/claims.jsonl", [claim])
    write_jsonl(draft_root / "manifests/links.jsonl", [link])
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
    return {"draft_id": draft_id, "source_id": source_id}
