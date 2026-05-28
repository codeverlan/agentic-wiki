from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any, Dict, List

JsonRecord = Dict[str, Any]


def read_jsonl(path: Path) -> List[JsonRecord]:
    if not path.exists():
        return []
    records: List[JsonRecord] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                value = json.loads(stripped)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSONL at {path}:{line_number}: {exc}") from exc
            if not isinstance(value, dict):
                raise ValueError(f"JSONL record at {path}:{line_number} is not an object")
            records.append(value)
    return records


def append_jsonl(path: Path, record: JsonRecord) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True, ensure_ascii=False) + "\n")


def write_jsonl(path: Path, records: Iterable[JsonRecord]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    with temp.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, sort_keys=True, ensure_ascii=False) + "\n")
    temp.replace(path)


def latest_by_key(records: Iterable[JsonRecord], key: str) -> Dict[str, JsonRecord]:
    by_key: Dict[str, JsonRecord] = {}
    for record in records:
        value = record.get(key)
        if isinstance(value, str):
            by_key[value] = record
    return by_key
