from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

SCHEMAS: Dict[str, Dict[str, Any]] = {
    "agent_memory_event": {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "Agent Memory Event",
        "type": "object",
        "required": ["schema_version", "event_id", "event_type", "occurred_at", "record"],
        "properties": {
            "schema_version": {"type": "integer"},
            "event_id": {"type": "string"},
            "event_type": {"type": "string"},
            "occurred_at": {"type": "string"},
            "record": {"type": "object"},
        },
    },
    "agent_context_request": {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "Agent Context Request",
        "type": "object",
        "required": ["schema_version", "request_id", "objective"],
        "properties": {
            "schema_version": {"type": "integer"},
            "request_id": {"type": "string"},
            "objective": {"type": "string"},
            "tags": {"type": "array", "items": {"type": "string"}},
        },
    },
    "agent_memory_proposal": {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "Agent Memory Proposal",
        "type": "object",
        "required": ["schema_version", "proposal_id", "proposed_at", "records"],
        "properties": {
            "schema_version": {"type": "integer"},
            "proposal_id": {"type": "string"},
            "proposed_at": {"type": "string"},
            "records": {"type": "array", "items": {"type": "object"}, "minItems": 1},
        },
    },
    "source": {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "Agentic Wiki Source Record",
        "type": "object",
        "required": [
            "source_id",
            "sha256",
            "kind",
            "raw_path",
            "origin",
            "ingested_at",
            "extraction_status",
            "metadata",
        ],
        "properties": {
            "source_id": {"type": "string"},
            "sha256": {"type": "string"},
            "extracted_sha256": {"type": "string"},
            "kind": {"enum": ["text", "markdown", "html", "json", "pdf", "image"]},
            "raw_path": {"type": "string"},
            "origin": {"type": "string"},
            "ingested_at": {"type": "string"},
            "extraction_status": {"enum": ["extracted", "partial", "failed"]},
            "metadata": {"type": "object"},
            "sensitivity": {"enum": ["general", "phi", "deidentified", "synthetic"]},
            "client_record_id": {"type": "string"},
            "source_category": {"type": "string"},
        },
    },
    "page": {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "Agentic Wiki Page Record",
        "type": "object",
        "required": ["page_id", "title", "slug", "page_type", "review_status", "html_path"],
        "properties": {
            "page_id": {"type": "string"},
            "title": {"type": "string"},
            "slug": {"type": "string"},
            "page_type": {
                "enum": [
                    "source",
                    "entity",
                    "concept",
                    "claim",
                    "contradiction",
                    "index",
                    "agent_handoff_digest",
                    "agent_incident_log",
                    "agent_run_state",
                ]
            },
            "review_status": {"enum": ["draft", "accepted", "needs_review", "rejected"]},
            "html_path": {"type": "string"},
            "sensitivity": {"enum": ["general", "phi", "deidentified", "synthetic"]},
            "client_record_id": {"type": "string"},
        },
    },
    "claim": {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "Agentic Wiki Claim Record",
        "type": "object",
        "required": [
            "claim_id",
            "text",
            "source_id",
            "confidence",
            "review_status",
            "provenance",
        ],
        "properties": {
            "claim_id": {"type": "string"},
            "text": {"type": "string"},
            "source_id": {"type": "string"},
            "confidence": {"type": "number"},
            "review_status": {"enum": ["draft", "accepted", "needs_review", "rejected"]},
            "contradicts": {"type": "array", "items": {"type": "string"}},
            "provenance": {"type": "object"},
            "sensitivity": {"enum": ["general", "phi", "deidentified", "synthetic"]},
            "client_record_id": {"type": "string"},
            "source_category": {"type": "string"},
            "clinical_claim_type": {"enum": ["source_fact", "clinical_guidance"]},
            "guidance_type": {"type": "string"},
            "cited_claim_ids": {"type": "array", "items": {"type": "string"}},
            "reviewed_by": {"type": "string"},
        },
    },
    "link": {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "Agentic Wiki Link Record",
        "type": "object",
        "required": ["from_id", "to_id", "relationship"],
        "properties": {
            "from_id": {"type": "string"},
            "to_id": {"type": "string"},
            "relationship": {"type": "string"},
        },
    },
    "event": {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "Agentic Wiki Event Record",
        "type": "object",
        "required": ["event_id", "event_type", "created_at"],
        "properties": {
            "event_id": {"type": "string"},
            "event_type": {"type": "string"},
            "created_at": {"type": "string"},
            "details": {"type": "object"},
        },
    },
}


def write_schemas(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    for name, schema in SCHEMAS.items():
        (directory / f"{name}.schema.json").write_text(
            json.dumps(schema, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
