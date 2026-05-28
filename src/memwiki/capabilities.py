from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

CAPABILITIES: Dict[str, Any] = {
    "name": "memwiki",
    "version": "0.1.0",
    "defaults": {
        "storage": "plain-files",
        "canonical_format": "semantic-html",
        "privacy": "local-first-opt-in-remote",
        "mutation_policy": "draft-then-promote",
    },
    "tools": {
        "memwiki.init_workspace": {"mutates": True},
        "memwiki.ingest_source": {"mutates": True, "supports_dry_run": True},
        "memwiki.compile_source": {"mutates": True, "writes": "drafts/"},
        "memwiki.validate_draft": {"mutates": False},
        "memwiki.promote_draft": {"mutates": True, "supports_check_only": True},
        "memwiki.query": {"mutates": False, "supports_draft_page": True},
        "memwiki.resolve": {"mutates": False},
        "memwiki.backlinks": {"mutates": False},
        "memwiki.docs_check": {"mutates": False},
        "memwiki.docs_draft": {"mutates": True, "writes": "drafts/"},
        "memwiki.export_static": {"mutates": True, "writes": "caller-selected output"},
    },
}


def write_capabilities(path: Path) -> Dict[str, Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(CAPABILITIES, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return CAPABILITIES
