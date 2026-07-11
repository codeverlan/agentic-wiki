from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

TOOL_CONTRACTS: Dict[str, Dict[str, Any]] = {
    "init_workspace": {"mutates": True},
    "ingest_source": {
        "mutates": True,
        "supports_dry_run": True,
        "clinical_phi_requires_context": True,
    },
    "compile_source": {
        "mutates": True,
        "writes": "drafts/",
        "clinical_phi_requires_context": True,
    },
    "validate_draft": {"mutates": False},
    "promote_draft": {
        "mutates": True,
        "supports_check_only": True,
        "clinical_phi_requires_context": True,
        "agent_memory_requires_verified_coordinator_authority": True,
        "serialization": "workspace promotion lock with rollback on failure",
    },
    "query": {
        "mutates": False,
        "supports_draft_page": True,
        "clinical_phi_requires_context": True,
    },
    "resolve": {"mutates": False, "clinical_phi_requires_context": True},
    "backlinks": {"mutates": False, "clinical_phi_requires_context": True},
    "docs_check": {"mutates": False},
    "docs_draft": {
        "mutates": True,
        "writes": "drafts/",
        "clinical_phi_requires_context": True,
    },
    "render_agent_run_state": {
        "mutates": True,
        "writes": "caller-selected output",
        "input": "agent slice queue JSON",
    },
    "render_agent_handoff_digest": {
        "mutates": True,
        "writes": "caller-selected output",
        "input": "agent slice queue JSON",
        "output": "semantic HTML handoff digest",
    },
    "render_agent_incident_log": {
        "mutates": True,
        "writes": "caller-selected output",
        "input": "agent incident log JSON",
        "output": "semantic HTML incident and exception log",
        "clinical_phi_policy": "metadata-only; rejects PHI and secret value fields",
    },
    "draft_agent_run_state": {
        "mutates": True,
        "writes": "drafts/",
        "input": "agent slice queue JSON",
        "clinical_phi_requires_context": True,
    },
    "observe_agent_memory": {
        "mutates": True,
        "writes": "raw/, manifests/sources.jsonl, drafts/, and manifests/events.jsonl when initialized",
        "input": "agent memory event JSON",
        "output": "validated structured observation and source-backed draft when initialized",
        "clinical_phi_requires_context": True,
        "clinical_phi_policy": "rejects PHI and secret value fields",
        "content_safety_policy": "local deterministic sensitive-content scan",
    },
    "build_agent_context": {
        "mutates": False,
        "input": "agent memory state JSON and context request JSON",
        "output": "bounded active record context",
        "clinical_phi_requires_context": True,
        "content_safety_policy": "local deterministic sensitive-content scan",
    },
    "propose_agent_memory": {
        "mutates": True,
        "writes": "raw/, manifests/sources.jsonl, drafts/, and manifests/events.jsonl",
        "input": "agent memory proposal JSON",
        "output": "source-backed draft proposal",
        "clinical_phi_requires_context": True,
        "clinical_phi_policy": "rejects PHI and secret value fields",
        "content_safety_policy": "local deterministic sensitive-content scan",
        "supersession_policy": "acyclic additive graph",
    },
    "assess_agent_memory_impact": {
        "mutates": False,
        "input": "agent memory state JSON and changed record ID",
        "output": "related impacted record IDs",
        "clinical_phi_requires_context": True,
        "content_safety_policy": "local deterministic sensitive-content scan",
    },
    "export_static": {
        "mutates": True,
        "writes": "caller-selected output",
        "clinical_phi_requires_context": True,
        "clinical_phi_policy": "blocked unless deidentified",
        "output_policy": "confined nonexistent workspace directory with staged publication",
    },
}


def _prefixed_tools(prefix: str) -> Dict[str, Dict[str, Any]]:
    return {f"{prefix}.{name}": dict(contract) for name, contract in TOOL_CONTRACTS.items()}


CAPABILITIES: Dict[str, Any] = {
    "name": "agentic-wiki",
    "version": "0.1.0",
    "compatibility": {
        "legacy_package": "memwiki",
        "legacy_class": "MemwikiWorkspace",
        "legacy_cli": "memwiki",
    },
    "defaults": {
        "storage": "plain-files",
        "canonical_format": "semantic-html",
        "privacy": "local-first-strict-phi-profile",
        "mutation_policy": "draft-then-promote",
        "clinical_phi": {
            "remote_models": "blocked",
            "workspace_layout": "one-client-per-workspace",
            "operation_context": "required",
            "static_export": "blocked-unless-deidentified",
            "cloud_phi": "blocked",
        },
    },
    "tools": {**_prefixed_tools("agentic_wiki"), **_prefixed_tools("memwiki")},
}


def write_capabilities(path: Path) -> Dict[str, Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(CAPABILITIES, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return CAPABILITIES
