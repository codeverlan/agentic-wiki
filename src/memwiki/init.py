from __future__ import annotations

from pathlib import Path

from memwiki.capabilities import write_capabilities
from memwiki.docs import render_all_docs
from memwiki.html import render_contradictions, render_index
from memwiki.manifest import append_jsonl
from memwiki.schemas import write_schemas
from memwiki.workspace import Workspace

CONFIG_TEMPLATE = """# Memwiki local-first configuration
[models]
default_adapter = "dry-run"
allow_remote = false

[ingest]
image_ocr = "auto"
"""


AGENTS_TEMPLATE = """# Memwiki Agent Instructions

This workspace stores canonical memory as semantic HTML under `wiki/`.

Required workflow:
- Never write model-generated content directly into `wiki/`.
- Put generated wiki and documentation updates under `drafts/<draft-id>/`.
- Run `memwiki lint` before promotion.
- Run `memwiki docs check` whenever commands, schemas, workflows, storage layout, or validation behavior changes.
- Use `memwiki docs draft` to generate draft documentation updates, then promote them through the same draft workflow.
- Preserve claim-level provenance for every substantive claim.
- Keep raw source files immutable after ingest.
"""


def init_workspace(root: Path) -> Workspace:
    workspace = Workspace(root)
    workspace.ensure_dirs()
    if not workspace.config_path.exists():
        workspace.config_path.write_text(CONFIG_TEMPLATE, encoding="utf-8")
    write_capabilities(workspace.path(".memwiki/agent-capabilities.json"))
    write_schemas(workspace.path("schemas"))
    for manifest_name in ["sources", "pages", "claims", "links", "events"]:
        workspace.path(f"manifests/{manifest_name}.jsonl").touch(exist_ok=True)
    (workspace.root / "AGENTS.md").write_text(AGENTS_TEMPLATE, encoding="utf-8")
    render_all_docs(workspace, target_root=workspace.root)
    (workspace.path("wiki/index.html")).write_text(render_index([]), encoding="utf-8")
    (workspace.path("wiki/contradictions.html")).write_text(render_contradictions([]), encoding="utf-8")
    append_jsonl(
        workspace.path("manifests/events.jsonl"),
        {
            "event_id": "evt_init",
            "event_type": "init",
            "created_at": "1970-01-01T00:00:00+00:00",
            "details": {"workspace": str(workspace.root)},
        },
    )
    return workspace
