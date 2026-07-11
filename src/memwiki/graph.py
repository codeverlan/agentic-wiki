from __future__ import annotations

import json
from typing import Dict, List

from memwiki.manifest import read_jsonl
from memwiki.workspace import Workspace


def build_graph_index(workspace: Workspace) -> Dict[str, List[Dict[str, object]]]:
    links: List[Dict[str, object]] = [
        {
            "from_id": str(record.get("from_id")),
            "to_id": str(record.get("to_id")),
            "relationship": str(record.get("relationship")),
        }
        for record in read_jsonl(workspace.path("manifests/links.jsonl"))
    ]
    external_entities = _current_revisions(read_jsonl(workspace.path("manifests/external-entities.jsonl")), "entity_id")
    external_relationships = _current_revisions(
        read_jsonl(workspace.path("manifests/external-relationships.jsonl")),
        "relationship_id",
    )
    graph = {
        "links": links,
        "external_entities": external_entities,
        "external_relationships": external_relationships,
    }
    workspace.path(".memwiki/index/graph.json").write_text(
        json.dumps(graph, indent=2, sort_keys=True), encoding="utf-8"
    )
    return graph


def _current_revisions(records: List[Dict[str, object]], identity_key: str) -> List[Dict[str, object]]:
    current: Dict[str, Dict[str, object]] = {}
    for record in records:
        identity = str(record.get(identity_key, ""))
        if identity:
            current[identity] = record
    return [current[key] for key in sorted(current)]
