from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

SCHEMAS: Dict[str, Dict[str, Any]] = {
    "source": {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "Memwiki Source Record",
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
            "kind": {"enum": ["text", "markdown", "html", "pdf", "image"]},
            "raw_path": {"type": "string"},
            "origin": {"type": "string"},
            "ingested_at": {"type": "string"},
            "extraction_status": {"enum": ["extracted", "partial", "failed"]},
            "metadata": {"type": "object"},
        },
    },
    "page": {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "Memwiki Page Record",
        "type": "object",
        "required": ["page_id", "title", "slug", "page_type", "review_status", "html_path"],
        "properties": {
            "page_id": {"type": "string"},
            "title": {"type": "string"},
            "slug": {"type": "string"},
            "page_type": {"enum": ["source", "entity", "concept", "claim", "contradiction", "index"]},
            "review_status": {"enum": ["draft", "accepted", "needs_review", "rejected"]},
            "html_path": {"type": "string"},
        },
    },
    "claim": {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "Memwiki Claim Record",
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
        },
    },
    "link": {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "Memwiki Link Record",
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
        "title": "Memwiki Event Record",
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
