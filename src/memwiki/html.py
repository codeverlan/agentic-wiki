from __future__ import annotations

import html as html_lib
import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any, Dict, List

from bs4 import BeautifulSoup

from memwiki.ids import utc_now


def escape(value: str) -> str:
    return html_lib.escape(value, quote=True)


def render_page(
    *,
    title: str,
    page_id: str,
    page_type: str,
    body: str,
    metadata: Dict[str, Any],
    generated_at: str | None = None,
) -> str:
    jsonld = dict(metadata)
    jsonld.update(
        {
            "@context": "https://schema.org",
            "@type": "CreativeWork",
            "identifier": page_id,
            "name": title,
            "memwiki:pageType": page_type,
            "memwiki:generatedAt": generated_at or utc_now(),
        }
    )
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(title)}</title>
  <script type="application/ld+json">{json.dumps(jsonld, sort_keys=True)}</script>
  <style>
    body {{ font-family: system-ui, sans-serif; margin: 2rem; line-height: 1.55; max-width: 72rem; }}
    nav a {{ margin-right: 1rem; }}
    article {{ margin-top: 1.5rem; }}
    .claim {{ border-left: 4px solid #2f6f73; padding-left: 1rem; }}
    .provenance {{ background: #f5f5f5; padding: 1rem; border-radius: 6px; }}
    code {{ background: #f2f2f2; padding: 0.1rem 0.25rem; }}
  </style>
</head>
<body>
  <nav><a href="index.html">Index</a><a href="contradictions.html">Contradictions</a></nav>
  <article id="{escape(page_id)}" data-page-type="{escape(page_type)}">
    <h1>{escape(title)}</h1>
{body}
  </article>
</body>
</html>
"""


def render_source_page(
    *,
    title: str,
    page_id: str,
    source_id: str,
    claim_id: str,
    claim_text: str,
    excerpt: str,
    source_record: Dict[str, Any],
) -> str:
    body = f"""
    <section id="summary">
      <h2>Summary</h2>
      <p>{escape(excerpt)}</p>
    </section>
    <section id="{escape(claim_id)}" class="claim" data-claim-id="{escape(claim_id)}">
      <h2>Claim</h2>
      <p>{escape(claim_text)}</p>
      <aside class="provenance">
        <strong>Provenance:</strong>
        <a href="../{escape(source_record["raw_path"])}">{escape(source_id)}</a>
      </aside>
    </section>
"""
    return render_page(
        title=title,
        page_id=page_id,
        page_type="source",
        body=body,
        metadata={
            "memwiki:sourceId": source_id,
            "memwiki:claims": [claim_id],
            "memwiki:source": source_record,
        },
    )


def render_index(pages: Iterable[Dict[str, Any]]) -> str:
    rows = "\n".join(
        f'<li><a href="{escape(page["html_path"])}">{escape(page["title"])}</a> '
        f'<code>{escape(page["page_type"])}</code></li>'
        for page in pages
    )
    return render_page(
        title="Memwiki Index",
        page_id="index",
        page_type="index",
        body=f"<section id=\"pages\"><h2>Pages</h2><ul>{rows}</ul></section>",
        metadata={"memwiki:index": True},
    )


def render_contradictions(claims: Iterable[Dict[str, Any]]) -> str:
    contradictory = [claim for claim in claims if claim.get("contradicts")]
    if contradictory:
        rows = "\n".join(
            f'<li id="{escape(claim["claim_id"])}">{escape(claim["text"])}</li>'
            for claim in contradictory
        )
    else:
        rows = "<li>No explicit contradictions recorded.</li>"
    return render_page(
        title="Contradictions",
        page_id="contradictions",
        page_type="contradiction",
        body=f"<section id=\"contradiction-list\"><h2>Contradictions</h2><ul>{rows}</ul></section>",
        metadata={"memwiki:contradictions": [claim["claim_id"] for claim in contradictory]},
    )


def extract_jsonld(path: Path) -> Dict[str, Any]:
    soup = BeautifulSoup(path.read_text(encoding="utf-8"), "html.parser")
    tag = soup.find("script", {"type": "application/ld+json"})
    if tag is None or tag.string is None:
        raise ValueError(f"No JSON-LD metadata in {path}")
    value = json.loads(tag.string)
    if not isinstance(value, dict):
        raise ValueError(f"JSON-LD metadata is not an object in {path}")
    return value


def validate_html_file(path: Path) -> List[str]:
    errors: List[str] = []
    try:
        soup = BeautifulSoup(path.read_text(encoding="utf-8"), "html.parser")
    except UnicodeDecodeError as exc:
        return [f"{path}: cannot read as UTF-8: {exc}"]
    if soup.find("article") is None:
        errors.append(f"{path}: missing article element")
    try:
        metadata = extract_jsonld(path)
    except (json.JSONDecodeError, ValueError) as exc:
        errors.append(str(exc))
    else:
        for key in ["identifier", "name", "memwiki:pageType"]:
            if key not in metadata:
                errors.append(f"{path}: JSON-LD missing {key}")
    return errors
