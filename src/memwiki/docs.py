from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List

from memwiki.html import render_page
from memwiki.ids import stable_id, utc_now
from memwiki.workspace import Workspace

CLI_COMMANDS = [
    "memwiki init",
    "memwiki ingest <path>",
    "memwiki compile <source-id>",
    "memwiki promote <draft-id>",
    "memwiki query <question>",
    "memwiki resolve <object-id>",
    "memwiki backlinks <object-id>",
    "memwiki capabilities",
    "memwiki lint",
    "memwiki docs check",
    "memwiki docs draft",
    "memwiki export static",
]

DOC_GENERATED_AT = "1970-01-01T00:00:00+00:00"


@dataclass(frozen=True)
class DocsStatus:
    clean: bool
    stale_paths: List[str]


def _section_list(items: List[str]) -> str:
    return "<ul>" + "".join(f"<li><code>{item}</code></li>" for item in items) + "</ul>"


def render_architecture_doc() -> str:
    body = """
    <section id="architecture">
      <h2>Architecture</h2>
      <p>Memwiki is a local-first, HTML-first memory wiki. Raw sources are immutable,
      draft updates are validated before promotion, and canonical memory lives as
      semantic HTML with embedded JSON-LD.</p>
    </section>
    <section id="storage">
      <h2>Storage Layout</h2>
      <ul>
        <li><code>raw/</code> stores immutable source copies.</li>
        <li><code>wiki/</code> stores accepted canonical HTML pages.</li>
        <li><code>drafts/</code> stores proposed wiki and documentation updates.</li>
        <li><code>manifests/</code> stores JSONL registries for sources, pages, claims, links, and events.</li>
      </ul>
    </section>
"""
    return render_page(
        title="Memwiki Architecture",
        page_id="docs-architecture",
        page_type="concept",
        body=body,
        metadata={"memwiki:doc": "architecture"},
        generated_at=DOC_GENERATED_AT,
    )


def render_operations_doc() -> str:
    body = f"""
    <section id="commands">
      <h2>Commands</h2>
      {_section_list(CLI_COMMANDS)}
    </section>
    <section id="documentation-governance">
      <h2>Documentation Governance</h2>
      <p>Run <code>memwiki docs check</code> after command, schema, workflow, storage, or validation changes.
      Use <code>memwiki docs draft</code> to prepare updates, then promote the draft after validation.</p>
    </section>
"""
    return render_page(
        title="Memwiki Operations",
        page_id="docs-operations",
        page_type="concept",
        body=body,
        metadata={"memwiki:doc": "operations", "memwiki:commands": CLI_COMMANDS},
        generated_at=DOC_GENERATED_AT,
    )


def render_schema_doc(workspace: Workspace) -> str:
    rows = []
    for schema_path in sorted(workspace.path("schemas").glob("*.schema.json")):
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        required = ", ".join(schema.get("required", []))
        properties = ", ".join(sorted(schema.get("properties", {}).keys()))
        rows.append(
            "<tr>"
            f"<td><code>{schema_path.name}</code></td>"
            f"<td>{schema.get('title', schema_path.stem)}</td>"
            f"<td>{required}</td>"
            f"<td>{properties}</td>"
            "</tr>"
        )
    body = """
    <section id="schemas">
      <h2>Manifest Schemas</h2>
      <table>
        <thead><tr><th>File</th><th>Title</th><th>Required</th><th>Properties</th></tr></thead>
        <tbody>
""" + "\n".join(rows) + """
        </tbody>
      </table>
    </section>
"""
    return render_page(
        title="Memwiki Schemas",
        page_id="docs-schema",
        page_type="concept",
        body=body,
        metadata={"memwiki:doc": "schema"},
        generated_at=DOC_GENERATED_AT,
    )


def render_cli_reference_doc() -> str:
    body = f"""
    <section id="cli-reference">
      <h2>CLI Reference</h2>
      {_section_list(CLI_COMMANDS)}
    </section>
"""
    return render_page(
        title="Memwiki CLI Reference",
        page_id="docs-cli-reference",
        page_type="concept",
        body=body,
        metadata={"memwiki:doc": "cli-reference", "memwiki:commands": CLI_COMMANDS},
        generated_at=DOC_GENERATED_AT,
    )


def render_docs(workspace: Workspace) -> Dict[str, str]:
    return {
        "docs/architecture.html": render_architecture_doc(),
        "docs/schema.html": render_schema_doc(workspace),
        "docs/operations.html": render_operations_doc(),
        "docs/cli-reference.html": render_cli_reference_doc(),
    }


def render_all_docs(workspace: Workspace, target_root: Path) -> None:
    for relative, content in render_docs(workspace).items():
        path = target_root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def docs_status(workspace: Workspace) -> DocsStatus:
    workspace.require()
    stale: List[str] = []
    for relative, expected in render_docs(workspace).items():
        path = workspace.path(relative)
        if not path.exists() or path.read_text(encoding="utf-8") != expected:
            stale.append(relative)
    return DocsStatus(clean=not stale, stale_paths=stale)


def draft_docs(workspace: Workspace) -> Dict[str, object]:
    workspace.require()
    draft_id = stable_id("draft", "docs", utc_now())
    draft_root = workspace.path(f"drafts/{draft_id}")
    render_all_docs(workspace, draft_root)
    (draft_root / "draft.json").write_text(
        json.dumps(
            {"draft_id": draft_id, "kind": "docs", "created_at": utc_now()},
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return {"draft_id": draft_id, "stale_paths": docs_status(workspace).stale_paths}
