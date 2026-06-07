from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List

from memwiki.html import render_page
from memwiki.ids import stable_id, utc_now
from memwiki.schemas import write_schemas
from memwiki.workspace import Workspace

CLI_COMMANDS = [
    "agentic-wiki init",
    "agentic-wiki init --profile clinical-phi --client-record-id <id> --attest-local-encryption",
    "agentic-wiki ingest <path>",
    "agentic-wiki ingest <path> --dry-run",
    "agentic-wiki ingest <path> --source-category <category>",
    "agentic-wiki compile <source-id>",
    "agentic-wiki promote <draft-id>",
    "agentic-wiki promote <draft-id> --check-only",
    "agentic-wiki query <question>",
    "agentic-wiki query <question> --json",
    "agentic-wiki resolve <object-id>",
    "agentic-wiki backlinks <object-id>",
    "agentic-wiki capabilities",
    "agentic-wiki lint",
    "agentic-wiki docs check",
    "agentic-wiki docs draft",
    "agentic-wiki export static",
]

DOC_GENERATED_AT = "1970-01-01T00:00:00+00:00"


@dataclass(frozen=True)
class DocsStatus:
    clean: bool
    stale_paths: List[str]


def is_agentic_wiki_source_checkout(root: Path) -> bool:
    pyproject = root / "pyproject.toml"
    if not pyproject.exists():
        return False
    try:
        pyproject_text = pyproject.read_text(encoding="utf-8")
    except OSError:
        return False
    return (
        'name = "agentic-wiki"' in pyproject_text
        and (root / "src/memwiki").is_dir()
        and (root / "src/agentic_wiki").is_dir()
    )


def _render_docs_from_clean_workspace() -> Dict[str, str]:
    with tempfile.TemporaryDirectory(prefix="agentic-wiki-docs-") as temp_root:
        workspace = Workspace(Path(temp_root))
        workspace.ensure_dirs()
        write_schemas(workspace.path("schemas"))
        return dict(render_docs(workspace))


def repo_docs_status(repo_root: Path) -> DocsStatus:
    if not is_agentic_wiki_source_checkout(repo_root):
        raise ValueError(f"Not an Agentic Wiki source checkout: {repo_root}")
    stale: List[str] = []
    for relative, expected in _render_docs_from_clean_workspace().items():
        path = repo_root / relative
        if not path.exists() or path.read_text(encoding="utf-8") != expected:
            stale.append(relative)
    return DocsStatus(clean=not stale, stale_paths=stale)


def _section_list(items: List[str]) -> str:
    return "<ul>" + "".join(f"<li><code>{item}</code></li>" for item in items) + "</ul>"


def render_architecture_doc() -> str:
    body = """
    <section id="architecture">
      <h2>Architecture</h2>
      <p>Agentic Wiki is a local-first Python library and CLI that compiles raw sources
      into an HTML knowledge wiki. Raw sources are immutable, draft updates are validated
      before promotion, and canonical memory lives as semantic HTML with embedded JSON-LD.</p>
      <p>The canonical public integration surface is
      <code>agentic_wiki.AgenticWikiWorkspace</code>. The legacy
      <code>memwiki.MemwikiWorkspace</code> surface remains as a compatibility shim.
      The CLI delegates to the public API so other programs and coding agents can embed
      the same workflow directly.</p>
    </section>
    <section id="flow">
      <h2>Flow</h2>
      <ol>
        <li>Register immutable raw sources under <code>raw/</code>.</li>
        <li>Extract text and metadata into <code>.memwiki/extracted/</code>.</li>
        <li>Compile source-backed draft HTML pages and manifest deltas under <code>drafts/</code>.</li>
        <li>Validate drafts with <code>agentic-wiki lint</code>.</li>
        <li>Promote valid drafts into canonical <code>wiki/</code>.</li>
      </ol>
    </section>
    <section id="clinical-phi">
      <h2>Clinical PHI Profile</h2>
      <p>The <code>clinical_phi</code> profile treats one workspace as one pseudonymous
      client record. PHI-reading and mutating operations require a host-supplied
      <code>OperationContext</code> with actor, role, purpose of use, and session information.
      Remote model adapters, cloud PHI storage, telemetry, and ordinary static export are
      blocked by default.</p>
      <p>Clinical compilation separates source facts from inferred guidance. Guidance is
      clinician-facing decision support and remains reviewable unless accepted with source
      citations, provenance, confidence, and reviewer metadata.</p>
    </section>
    <section id="agent-integration">
      <h2>Agent Integration</h2>
      <p>Workspaces expose <code>.memwiki/agent-capabilities.json</code>, dry-run ingest,
      check-only promotion, JSON query output, object resolution, backlinks, and PHI-mode
      mutation policy metadata.</p>
    </section>
"""
    return render_page(
        title="Agentic Wiki Architecture",
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
      <p>Use the canonical CLI through <code>uv run agentic-wiki</code> during development.
      The legacy <code>uv run memwiki</code> alias remains available.</p>
      {_section_list(CLI_COMMANDS)}
    </section>
    <section id="documentation-governance">
      <h2>Documentation Governance</h2>
      <p>Run <code>agentic-wiki docs check</code> after command, schema, workflow, storage,
      or validation changes. In an initialized workspace, it checks generated documentation
      under that workspace. In this source checkout, it uses a disposable initialized workspace
      to render expected documentation and compares those files with the tracked
      <code>docs/*.html</code> files, so repository development does not require turning the
      repo root into a wiki workspace.</p>
      <p>The legacy <code>memwiki docs check</code> alias follows the same behavior. Use
      <code>agentic-wiki docs draft</code> inside initialized workspaces to prepare updates,
      then promote the draft after validation.</p>
    </section>
    <section id="operation-context">
      <h2>Operation Context</h2>
      <p>Clinical PHI workspaces require <code>actor_id</code>, <code>actor_role</code>,
      <code>purpose_of_use</code>, and <code>session_id</code> for PHI-reading or mutating operations.
      Event logs record those fields with object IDs and result metadata, not raw source text.</p>
    </section>
    <section id="capability-contract">
      <h2>Capability Contract</h2>
      <p>The <code>.memwiki/agent-capabilities.json</code> manifest advertises the same
      clinical PHI operation-context requirements and static-export restrictions for
      legacy <code>memwiki.*</code> tools as for canonical <code>agentic_wiki.*</code> tools.
      Static export from clinical PHI workspaces requires operation context and remains
      blocked unless the caller explicitly marks the export as deidentified.</p>
    </section>
    <section id="repository">
      <h2>Repository</h2>
      <p>The canonical remote is <code>github-personal:codeverlan/agentic-wiki.git</code>.</p>
      <p>The project tracks the installed Codex skill through
      <code>skills/agentic-wiki-planner</code>, a symlink to
      <code>/Users/tyler-lcsw/.codex/skills/agentic-wiki-planner</code>. Keep skill-link
      changes on branch <code>skill/agentic-wiki-planner</code>.</p>
    </section>
"""
    return render_page(
        title="Agentic Wiki Operations",
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
      <p>Workspace schemas are generated by <code>agentic-wiki init</code> into
      <code>schemas/*.schema.json</code>. The legacy <code>memwiki init</code> alias remains
      available.</p>
      <table>
        <thead><tr><th>File</th><th>Title</th><th>Required</th><th>Properties</th></tr></thead>
        <tbody>
""" + "\n".join(rows) + """
        </tbody>
      </table>
    </section>
    <section id="clinical-claims">
      <h2>Clinical Claims</h2>
      <p>Clinical source facts use <code>clinical_claim_type=source_fact</code>. Inferred
      guidance uses <code>clinical_claim_type=clinical_guidance</code> and must carry cited
      source claims plus provenance linking it back to those claims. Accepted clinical
      guidance must also include reviewer metadata.</p>
    </section>
"""
    return render_page(
        title="Agentic Wiki Schemas",
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
        title="Agentic Wiki CLI Reference",
        page_id="docs-cli-reference",
        page_type="concept",
        body=body,
        metadata={"memwiki:doc": "cli-reference", "memwiki:commands": CLI_COMMANDS},
        generated_at=DOC_GENERATED_AT,
    )


def render_hipaa_local_doc() -> str:
    body = """
    <section id="positioning">
      <h2>Positioning</h2>
      <p>The HIPAA-local profile provides implementation controls for localized PHI-capable
      Agentic Wiki workspaces. It is not a HIPAA certification and does not replace
      organizational risk analysis, workforce policy, legal review, incident response, or a
      covered entity's compliance program.</p>
    </section>
    <section id="default-boundary">
      <h2>Default Boundary</h2>
      <ul>
        <li>One workspace represents one pseudonymous client record.</li>
        <li>Remote model adapters are blocked for clinical PHI workspaces.</li>
        <li>Cloud PHI storage, telemetry, and Cloudflare publishing are blocked in this strict-local branch.</li>
        <li>Host applications must pass operation context for PHI operations.</li>
        <li>Operators must attest that the workspace is stored on local encrypted storage.</li>
        <li>Static export is blocked unless the caller explicitly marks the export as deidentified or synthetic.</li>
      </ul>
    </section>
    <section id="clinical-guidance-boundary">
      <h2>Clinical Guidance Boundary</h2>
      <p>Agentic Wiki separates source facts from inferred clinical guidance. Guidance can
      include evidence summaries, risk flags, care considerations, follow-up questions, and
      clinician-facing decision support. It must not be treated as autonomous diagnosis,
      treatment orders, or patient-facing advice.</p>
    </section>
    <section id="audit-events">
      <h2>Audit Events</h2>
      <p>Clinical PHI events record actor ID, actor role, purpose of use, session ID,
      operation, object IDs, and result metadata. Event logs must not include raw source text
      or free-text query content; query events store a hash of the question.</p>
    </section>
    <section id="references">
      <h2>References</h2>
      <ul>
        <li>HHS HIPAA Security Rule: <code>https://www.hhs.gov/hipaa/for-professionals/security/index.html</code></li>
        <li>HHS de-identification guidance: <code>https://www.hhs.gov/hipaa/for-professionals/special-topics/de-identification/</code></li>
        <li>NIST SP 800-66 Rev. 2: <code>https://csrc.nist.gov/pubs/sp/800/66/r2/final</code></li>
        <li>Cloudflare HIPAA statement: <code>https://www.cloudflare.com/privacy-and-compliance/hipaa/</code></li>
      </ul>
    </section>
"""
    return render_page(
        title="Agentic Wiki HIPAA-Local Profile",
        page_id="docs-hipaa-local",
        page_type="concept",
        body=body,
        metadata={"memwiki:doc": "hipaa-local"},
        generated_at=DOC_GENERATED_AT,
    )


def render_docs(workspace: Workspace) -> Dict[str, str]:
    return {
        "docs/architecture.html": render_architecture_doc(),
        "docs/schema.html": render_schema_doc(workspace),
        "docs/operations.html": render_operations_doc(),
        "docs/cli-reference.html": render_cli_reference_doc(),
        "docs/hipaa-local.html": render_hipaa_local_doc(),
    }


def render_all_docs(workspace: Workspace, target_root: Path) -> None:
    for relative, content in render_docs(workspace).items():
        path = target_root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def docs_status(workspace: Workspace, allow_repo_checkout: bool = False) -> DocsStatus:
    if allow_repo_checkout and not workspace.config_path.exists() and is_agentic_wiki_source_checkout(workspace.root):
        return repo_docs_status(workspace.root)
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
