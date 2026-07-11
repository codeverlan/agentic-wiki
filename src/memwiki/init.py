from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from memwiki.capabilities import write_capabilities
from memwiki.docs import render_all_docs
from memwiki.html import render_contradictions, render_index
from memwiki.ids import stable_id, utc_now
from memwiki.manifest import append_jsonl
from memwiki.policy import normalize_profile
from memwiki.schemas import write_schemas
from memwiki.workspace import Workspace

CONFIG_TEMPLATE = """# Agentic Wiki local-first configuration
[workspace]
profile = "{profile}"
client_record_id = "{client_record_id}"
per_client_workspace = {per_client_workspace}

[models]
default_adapter = "dry-run"
allow_remote = false

[ingest]
image_ocr = "auto"

[privacy]
require_operation_context = {require_operation_context}
static_export_phi = false
cloud_phi = false
local_encrypted_storage_attested = {local_encrypted_storage_attested}
"""


AGENTS_TEMPLATE = """# Agentic Wiki Workspace Instructions

This workspace stores canonical memory as semantic HTML under `wiki/`.

Required workflow:
- Never write model-generated content directly into `wiki/`.
- Put generated wiki and documentation updates under `drafts/<draft-id>/`.
- Run `agentic-wiki lint` before promotion. The legacy `memwiki lint` alias is still available.
- Run `agentic-wiki docs check` whenever commands, schemas, workflows, storage layout, or validation behavior changes.
  The legacy `memwiki docs check` alias is still available for compatibility.
- Use `agentic-wiki docs draft` to generate draft documentation updates, then promote them through the same
  draft workflow.
- Preserve claim-level provenance for every substantive claim.
- Keep raw source files immutable after ingest.
- In clinical PHI workspaces, every PHI-reading or mutating operation must include authenticated operation
  context from the host application.
- Do not use remote model adapters, cloud storage, telemetry, or static PHI export from clinical PHI workspaces.
"""


def _config_text(profile: str, client_record_id: str, local_encrypted_storage_attested: bool) -> str:
    clinical = profile == "clinical_phi"
    return CONFIG_TEMPLATE.format(
        profile=profile,
        client_record_id=client_record_id,
        per_client_workspace=str(clinical).lower(),
        require_operation_context=str(clinical).lower(),
        local_encrypted_storage_attested=str(local_encrypted_storage_attested).lower(),
    )


def init_workspace(
    root: Path,
    profile: str = "standard",
    client_record_id: Optional[str] = None,
    local_encrypted_storage_attested: bool = False,
) -> Workspace:
    normalized_profile = normalize_profile(profile)
    workspace = Workspace(root)
    if workspace.config_path.exists():
        workspace.ensure_dirs()
        for manifest_name in ["sources", "pages", "claims", "links", "events"]:
            workspace.path(f"manifests/{manifest_name}.jsonl").touch(exist_ok=True)
        return workspace
    managed_paths = [
        "wiki/index.html",
        "wiki/contradictions.html",
        "docs/architecture.html",
        "docs/schema.html",
        "docs/operations.html",
        "docs/cli-reference.html",
        "docs/hipaa-local.html",
        ".memwiki/agent-capabilities.json",
    ]
    collision = next((path for path in managed_paths if workspace.path(path).exists()), None)
    if collision is not None:
        raise ValueError(f"Agentic Wiki managed file already exists in non-workspace: {collision}")
    workspace.ensure_dirs()
    resolved_client_record_id = client_record_id or (
        stable_id("client", str(workspace.root), utc_now()) if normalized_profile == "clinical_phi" else ""
    )
    if not workspace.config_path.exists():
        workspace.config_path.write_text(
            _config_text(
                normalized_profile,
                resolved_client_record_id,
                local_encrypted_storage_attested,
            ),
            encoding="utf-8",
        )
    if normalized_profile == "clinical_phi":
        workspace.path(".memwiki/client.json").write_text(
            json.dumps(
                {
                    "client_record_id": resolved_client_record_id,
                    "profile": normalized_profile,
                    "sensitivity": "phi",
                    "one_workspace_per_client": True,
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
    write_capabilities(workspace.path(".memwiki/agent-capabilities.json"))
    write_schemas(workspace.path("schemas"))
    for manifest_name in ["sources", "pages", "claims", "links", "events"]:
        workspace.path(f"manifests/{manifest_name}.jsonl").touch(exist_ok=True)
    agents_path = workspace.root / "AGENTS.md"
    if not agents_path.exists():
        agents_path.write_text(AGENTS_TEMPLATE, encoding="utf-8")
    render_all_docs(workspace, target_root=workspace.root)
    (workspace.path("wiki/index.html")).write_text(render_index([]), encoding="utf-8")
    (workspace.path("wiki/contradictions.html")).write_text(render_contradictions([]), encoding="utf-8")
    append_jsonl(
        workspace.path("manifests/events.jsonl"),
        {
            "event_id": "evt_init",
            "event_type": "init",
            "created_at": "1970-01-01T00:00:00+00:00",
            "details": {
                "workspace": str(workspace.root),
                "profile": normalized_profile,
                "client_record_id": resolved_client_record_id or None,
            },
        },
    )
    return workspace
