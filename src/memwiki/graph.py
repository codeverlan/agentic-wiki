from __future__ import annotations

import json
from typing import Dict, List

from memwiki.manifest import read_jsonl
from memwiki.workspace import Workspace


def build_graph_index(workspace: Workspace) -> Dict[str, List[Dict[str, str]]]:
    links = [
        {
            "from_id": str(record.get("from_id")),
            "to_id": str(record.get("to_id")),
            "relationship": str(record.get("relationship")),
        }
        for record in read_jsonl(workspace.path("manifests/links.jsonl"))
    ]
    graph = {"links": links}
    workspace.path(".memwiki/index/graph.json").write_text(
        json.dumps(graph, indent=2, sort_keys=True), encoding="utf-8"
    )
    return graph
