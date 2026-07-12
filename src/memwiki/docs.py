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
    "agentic-wiki agent render-run-state <queue.json> --output <run-state.html>",
    "agentic-wiki agent render-handoff-digest <queue.json> --output <handoff.html>",
    "agentic-wiki agent render-incident-log <incident-log.json> --output <incident-log.html>",
    "agentic-wiki agent draft-run-state <queue.json>",
    "agentic-wiki agent memory observe <event.json>",
    "agentic-wiki agent memory propose <proposal.json>",
    "agentic-wiki agent memory context <memory-state.json> <request.json>",
    "agentic-wiki agent memory impact <memory-state.json> <changed-record-id>",
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
      <p>Initialization preserves an existing project <code>AGENTS.md</code>, refuses collisions
      with Agentic Wiki-managed files in a non-workspace, and treats reinitialization of an
      existing workspace as idempotent.</p>
    </section>
    <section id="promotion-integrity">
      <h2>Promotion Integrity</h2>
      <p>Ingest binds both persisted raw bytes and extracted text to SHA-256 digests in a chained
      project-local integrity ledger. Compilation and promotion reverify the ledger, manifest,
      confined artifact paths, and content before using source-backed claims. Promotion
      serializes writers with a workspace lock, restores canonical wiki, documentation, manifest,
      and event state after an exception, and treats a repeated successful draft promotion as an
      idempotent retry.</p>
      <p>Agent-memory drafts require host-verified coordinator authority before canonical
      promotion. Hosts inject an <code>AuthorityVerifier</code> into
      <code>AgenticWikiWorkspace</code>; caller-asserted coordinator roles alone are insufficient.
      Raw authority credentials are excluded from operation context serialization and audit
      events.</p>
      <p>Agent-memory inputs and outputs pass a deterministic local sensitive-content scanner that
      blocks high-confidence credentials, private keys, tokens, direct identifiers, and oversized
      strings without echoing matched values. Proposal supersession graphs must be acyclic.</p>
      <p>Agent-development HTML renderers confine output to noncanonical workspace artifact paths
      and reject traversal or symlinks. Static export requires a new confined directory and stages
      the complete export before publication.</p>
      <p>Extractive claim identity and source locators are validated during lint and promotion.
      Approved visual baselines require supervised authority metadata; screenshot paths and hashes
      can be verified against workspace-controlled evidence, and approved successor references
      derive supersession without deleting history.</p>
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
      <p>External agents, plugins, MCP servers, APIs, apps, services, datasets, documents,
      repositories, and websites can be registered as versioned external-memory entities.
      Append-only revisions preserve edit history, while typed relationships connect those
      entities to sources, pages, claims, drafts, design records, slices, and other external
      entities. The public API supports neighbors, backlinks, path discovery, impact traversal,
      relationship explanations, and an XSS-safe semantic HTML/JSON-LD relationship view.</p>
      <p>Raw ingested source files remain immutable. Editing an external entity changes its
      catalog revision and never rewrites source evidence or canonical wiki pages. Canonical
      knowledge still enters through the normal source-backed draft, validation, and promotion
      workflow.</p>
    </section>
    <section id="agent-development-memory">
      <h2>Agent Development Memory</h2>
      <p>Agentic Wiki can render generic agent-development run state from a machine-readable
      slice queue into semantic HTML. The same queue can be drafted into a workspace so accepted
      development memory follows the normal source-backed draft, validation, and promotion flow.</p>
      <p>The queue remains the machine-readable source of truth. Rendered run-state HTML and
      handoff-digest HTML provide human review and resume surfaces, while incident-log HTML records
      abnormal coordinator events and exception handling. JSON-LD metadata records slice counts,
      queue source, source spec, status totals, status lanes, incident severity counts, PHI/HIPAA
      log posture, and any queue-advertised human review artifacts such as intent packet,
      archetype registry, run-state template, handoff digest, incident log, and branch ledger paths.</p>
      <p>Handoff digests are generated at stops, compaction-risk points, stale leases, and
      user-return checkpoints. They summarize the current objective, active authority, queue
      state, worker leases, blockers, validation, external capabilities, supervision items,
      and exact resume command or path.</p>
      <p>If slice entries include <code>worker_lease</code>, rendered run-state pages show worker,
      branch, assigned paths, heartbeat, lease status, stale handling, and reassignment or
      supersession notes.</p>
      <p>Agent-memory operations validate typed observations and proposals, compile bounded context from active
      records, and report relationship-based impact. In initialized workspaces, an observed event
      is preserved as an immutable source and a provenance-backed draft; canonical wiki content
      still requires normal validation and promotion.</p>
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
    <section id="agent-development-memory">
      <h2>Agent Development Memory</h2>
      <p>Use <code>agentic-wiki agent render-run-state</code> to turn a slice queue JSON file
      into standalone semantic HTML. Use <code>agentic-wiki agent render-handoff-digest</code>
      to create a stop, compaction-risk, stale-lease, or user-return digest from the same queue.
      Use <code>agentic-wiki agent draft-run-state</code> to preserve that queue as a raw source
      and create a draft wiki page with provenance before promotion.</p>
      <p>If the queue includes <code>human_artifacts</code>, rendered run-state pages show those
      review paths and include the same map in JSON-LD metadata.</p>
      <p>If the queue includes <code>status_lanes</code>, rendered run-state pages show internal
      agent work, external capability activity, validation/evidence, and supervision-required
      summaries. Queues without lane data still render the four default lanes as empty review rows.</p>
      <p>If slice entries include <code>worker_lease</code>, rendered run-state pages show heartbeat
      and stale-worker handling so coordinators can reassign or supersede stalled slices while
      preserving slice-owned work.</p>
      <p>Handoff digests include the objective, active authority, queue state, worker leases,
      blockers, validation, external-capability artifacts, supervision items, and exact resume
      command or path.</p>
      <p>Use <code>agentic-wiki agent render-incident-log</code> to create append-only incident
      and exception log HTML from a project-local incident log JSON file. PHI-capable projects use
      the HIPAA-aware metadata-only variant, which rejects explicit PHI or secret value fields,
      reminds the user to run regular security scans, and records abnormal-behavior monitoring
      without claiming legal compliance.</p>
      <p>Use <code>agentic-wiki agent memory observe</code> to validate a typed event and, in an
      initialized workspace, preserve it as source evidence with a draft wiki page. Use
      <code>agentic-wiki agent memory propose</code> to preserve a worker proposal as immutable
      evidence and draft-only semantic knowledge. Use
      <code>agentic-wiki agent memory context</code> for bounded tag-selected active records and
      <code>agentic-wiki agent memory impact</code> for explicit relationship impact.</p>
      <p>These commands are provider-agnostic and are intended for local autonomous development
      coordinators, reusable agent workflows, and future plugin packaging. They do not require
      Cloudflare, remote model adapters, or external services.</p>
    </section>
    <section id="external-memory">
      <h2>External Source And Relationship Memory</h2>
      <p>Use <code>AgenticWikiWorkspace.register_external_entity</code> to store a portable external
      identity and <code>update_external_entity</code> to append a checked revision. Use
      <code>relate_external_memory</code> and <code>update_external_relationship</code> for typed,
      evidence-bearing links. Query the graph with <code>external_neighbors</code>,
      <code>external_backlinks</code>, <code>external_path</code>, <code>external_impact</code>, and
      <code>explain_external_relationship</code>.</p>
      <p><code>render_external_memory</code> writes a confined semantic HTML and JSON-LD projection
      under the workspace. The standard graph index includes current external entity and
      relationship revisions alongside canonical wiki links. Credentials are forbidden in entity
      metadata and relationship evidence. Clinical workspaces require operation context.</p>
    </section>
    <section id="evaluation-routing-memory">
      <h2>Evaluation And Routing Memory</h2>
      <p>Material coordinator slices define capability checks, regression checks, a baseline
      revision, and complete baseline results before implementation. Integrated results rerun the
      same checks and record newly passing checks, regressions, unchanged failures, and a stable
      comparison identifier. Required slices cannot satisfy the completion predicate unless their
      passing comparison evidence is bound to the final revision.</p>
      <p>Decomposition advice favors one dominant risk, independent verification, and a clear done
      condition. Time estimates remain advisory and never create phase or milestone stop conditions.
      Agent routing records a provider-neutral minimum capability tier and then uses project policy
      and qualification evidence to select a public, private, personalized, or local agent.</p>
    </section>
    <section id="plugin-qualification">
      <h2>Plugin Qualification</h2>
      <p><code>PluginQualificationHarness</code> compares plugin source with the installed cache and
      runs deterministic offline acceptance cases for manifest and skill discovery, immutability,
      computer-use supervision, credential artifacts, seven project archetypes, six-worker dispatch,
      blocked-slice continuation, malformed reports, public/private/handoff agents, restart
      equivalence, regression rejection, critical-risk routing, PHI synthetic-only enforcement, and
      a natural-language software-start front door covering existing specifications, partial
      resources, and from-scratch discovery.</p>
      <p>The content-addressed result can be written as machine-readable JSON and standalone semantic
      HTML. Qualification failure is distinct from repository unit-test failure and should block a
      plugin release while leaving unrelated project slices available.</p>
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
    <section id="phi-aware-development-monitoring">
      <h2>PHI-Aware Development Monitoring</h2>
      <p>Agent-development incident logs for PHI-capable software must use the HIPAA-aware
      metadata-only variant. The log records incident IDs, timestamps, severity, category,
      evidence pointers, monitoring phase, and next action; it must not record PHI, credentials,
      tokens, passwords, raw secret values, or raw sensitive text.</p>
      <p>The intake interview must ask whether the application will handle PHI. If the answer is
      yes, the project should maintain monitoring phases that remind the user to run regular
      dependency, secret, static-analysis, and access-review scans and to monitor abnormal behavior
      that may indicate leakage of information, credentials, plans, or PHI.</p>
      <p>These controls support HIPAA-aware software development practices such as least privilege,
      audit logging, access review, encryption planning, backup planning, incident response, and
      deidentification discipline. They are implementation safeguards, not a HIPAA certification
      or substitute for organizational risk analysis, legal review, workforce policy, BAAs, or
      production compliance operations.</p>
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
