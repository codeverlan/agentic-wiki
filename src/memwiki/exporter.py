from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Dict, List

from memwiki.query import SearchEntry, build_search_index
from memwiki.workspace import Workspace


def export_static(workspace: Workspace, output: Path) -> Dict[str, object]:
    workspace.require()
    output.mkdir(parents=True, exist_ok=True)
    for html_path in workspace.path("wiki").glob("*.html"):
        shutil.copy2(html_path, output / html_path.name)
    index = build_search_index(workspace)
    search_records: List[Dict[str, object]] = []
    for value in index.values():
        entry: SearchEntry = value
        search_records.append(
            {
                "path": entry["path"],
                "title": entry["title"],
                "summary": entry["summary"],
                "tokens": dict(entry["tokens"]),
            }
        )
    (output / "search.json").write_text(
        json.dumps(search_records, indent=2, sort_keys=True), encoding="utf-8"
    )
    return {"output": str(output), "pages": len(list(output.glob("*.html")))}
