from __future__ import annotations

import hashlib
import html
import json
import re
from dataclasses import dataclass
from typing import Any, Dict, Mapping, Sequence, Tuple, cast

VIEW_TYPES = ("agent-activity", "agent-connections", "agent-provenance")
MAX_ITEMS = 500
MAX_SOURCE_BYTES = 2 * 1024 * 1024
MAX_DASHBOARD_BYTES = 256 * 1024
_SOURCE_HASH = re.compile(r'"memwiki:sourceHash"\s*:\s*"([0-9a-f]{64})"')
_ACTIVE_STATUSES = {"planned", "authorized", "started", "waiting"}


class StaleAgentProjectionError(ValueError):
    """Raised when an agent view is detached from its authoritative source."""


@dataclass(frozen=True)
class AgentProjectionBundle:
    source_hash: str
    dashboard: Mapping[str, Any]
    html: Mapping[str, str]


def _canonical(value: Any) -> bytes:
    try:
        encoded = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )
    except (TypeError, ValueError) as error:
        raise ValueError("projection must be JSON-compatible") from error
    payload = encoded.encode("utf-8")
    if len(payload) > MAX_SOURCE_BYTES:
        raise ValueError("projection exceeds size limit")
    return payload


def _source_hash(projection: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical(projection)).hexdigest()


def _items(projection: Mapping[str, Any], key: str) -> Tuple[Mapping[str, Any], ...]:
    raw = projection.get(key, [])
    if not isinstance(raw, list):
        raise ValueError(f"{key} must be a list")
    if len(raw) > MAX_ITEMS:
        raise ValueError(f"{key} cannot contain more than 500 items")
    if not all(isinstance(item, Mapping) for item in raw):
        raise ValueError(f"{key} entries must be objects")
    return tuple(cast(Sequence[Mapping[str, Any]], raw))


def _text(value: Any) -> str:
    if value is None:
        return "unknown"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, (str, int, float)):
        return str(value)
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _escape(value: Any) -> str:
    return html.escape(_text(value), quote=True)


def _select(item: Mapping[str, Any], fields: Sequence[str]) -> Dict[str, Any]:
    return {field: item.get(field) for field in fields}


def _selected_items(
    projection: Mapping[str, Any],
    key: str,
    fields: Sequence[str],
    sort_fields: Sequence[str],
) -> list[Dict[str, Any]]:
    rows = [_select(item, fields) for item in _items(projection, key)]
    return sorted(rows, key=lambda row: tuple(_text(row.get(field, "")) for field in sort_fields))


def _table(
    *, section_id: str, title: str, rows: Sequence[Mapping[str, Any]], columns: Sequence[str]
) -> str:
    headings = "".join(
        f'<th scope="col">{_escape(column.replace("_", " ").title())}</th>'
        for column in columns
    )
    body = []
    for row in rows:
        cells = "".join(f"<td>{_escape(row.get(column))}</td>" for column in columns)
        body.append(f"<tr>{cells}</tr>")
    if not body:
        body.append(f'<tr><td colspan="{len(columns)}">None recorded.</td></tr>')
    return (
        f'<section id="{section_id}"><h2>{_escape(title)}</h2><table><thead><tr>'
        f"{headings}</tr></thead><tbody>{''.join(body)}</tbody></table></section>"
    )


def _page(
    *, view_type: str, title: str, run_id: str, updated_at: str, source_hash: str, body: str
) -> str:
    metadata = {
        "@context": "https://schema.org",
        "@type": "Dataset",
        "identifier": f"{run_id}:{view_type}:{source_hash}",
        "name": title,
        "dateModified": updated_at,
        "memwiki:authoritative": False,
        "memwiki:containsSensitiveResults": False,
        "memwiki:runId": run_id,
        "memwiki:sourceHash": source_hash,
        "memwiki:viewType": view_type,
    }
    json_ld = json.dumps(metadata, sort_keys=True).replace("<", "\\u003c")
    navigation = "".join(
        f'<a href="{name}.html">{_escape(name.replace("-", " ").title())}</a>'
        for name in VIEW_TYPES
    )
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_escape(title)}</title><script type="application/ld+json">{json_ld}</script>
<style>
body{{font-family:system-ui,sans-serif;line-height:1.5;margin:2rem;max-width:90rem}}
nav a{{margin-right:1rem}}table{{border-collapse:collapse;width:100%;margin-bottom:1.5rem}}
th,td{{border:1px solid #bbb;padding:.45rem;text-align:left;vertical-align:top}}
th{{background:#eee}}code{{overflow-wrap:anywhere}}
</style></head><body><header><h1>{_escape(title)}</h1>
<p>Run <code>{_escape(run_id)}</code> | Updated {_escape(updated_at)}</p></header>
<nav aria-label="Agent views">{navigation}</nav><main>{body}</main>
<footer><p>Generated redacted view of authoritative projection <code>{source_hash}</code>.</p>
</footer></body></html>
"""


def _dashboard(projection: Mapping[str, Any], source_hash: str) -> Dict[str, Any]:
    agents = _selected_items(
        projection,
        "agents",
        (
            "agent_id",
            "display_name",
            "product",
            "classification",
            "version",
            "observed_version",
            "drift",
            "trust",
            "availability",
        ),
        ("agent_id",),
    )
    invocations = _selected_items(
        projection,
        "invocations",
        ("id", "agent_id", "slice_id", "operation", "status", "started_at", "cost"),
        ("id",),
    )
    active = [row for row in invocations if row["status"] in _ACTIVE_STATUSES]
    handoffs = _selected_items(
        projection,
        "handoffs",
        ("id", "agent_id", "slice_id", "status", "reason"),
        ("id",),
    )
    failures = _selected_items(
        projection,
        "failures",
        ("id", "agent_id", "slice_id", "category", "status", "summary"),
        ("id",),
    )
    connections = _selected_items(
        projection,
        "connections",
        ("id", "kind", "agent_id", "version", "status"),
        ("id",),
    )
    provenance = _selected_items(
        projection,
        "provenance",
        (
            "id",
            "agent_id",
            "invocation_id",
            "slice_id",
            "artifact",
            "input_hash",
            "output_hash",
            "recorded_at",
        ),
        ("id",),
    )
    result: Dict[str, Any] = {
        "schema_version": 1,
        "run_id": projection["run_id"],
        "source_revision": projection.get("source_revision"),
        "source_hash": source_hash,
        "updated_at": projection.get("updated_at", "unknown"),
        "counts": {
            "agents": len(agents),
            "available": sum(row["availability"] == "available" for row in agents),
            "active_invocations": len(active),
            "handoffs": len(handoffs),
            "open_failures": sum(row["status"] == "open" for row in failures),
            "connections": len(connections),
        },
        "agents": agents,
        "active_invocations": active,
        "handoffs": handoffs,
        "failures": failures,
        "connections": connections,
        "provenance": provenance,
    }
    if len(_canonical(result)) > MAX_DASHBOARD_BYTES:
        raise ValueError("agent dashboard projection exceeds size limit")
    return result


def _assert_redacted(bundle: AgentProjectionBundle, sensitive_values: Sequence[str]) -> None:
    rendered = json.dumps(bundle.dashboard, sort_keys=True) + "".join(bundle.html.values())
    for value in sensitive_values:
        if value and value in rendered:
            raise ValueError("sensitive value reached an agent projection")


def render_agent_views(
    projection: Mapping[str, Any], *, sensitive_values: Sequence[str] = ()
) -> AgentProjectionBundle:
    """Render bounded, allowlisted activity, connection, and provenance projections."""
    if not isinstance(projection, Mapping):
        raise ValueError("projection must be an object")
    _canonical(projection)
    run_id = projection.get("run_id")
    if not isinstance(run_id, str) or not run_id.strip():
        raise ValueError("run_id must be a non-empty string")
    source_hash = _source_hash(projection)
    updated_at = _text(projection.get("updated_at"))
    dashboard = _dashboard(projection, source_hash)

    activity = (
        _table(
            section_id="available-agents",
            title="Available Public and Private Agents",
            rows=cast(Sequence[Mapping[str, Any]], dashboard["agents"]),
            columns=(
                "agent_id",
                "display_name",
                "product",
                "classification",
                "version",
                "observed_version",
                "drift",
                "trust",
                "availability",
            ),
        )
        + _table(
            section_id="active-invocations",
            title="Active Invocations and Cost",
            rows=cast(Sequence[Mapping[str, Any]], dashboard["active_invocations"]),
            columns=("id", "agent_id", "slice_id", "operation", "status", "started_at", "cost"),
        )
        + _table(
            section_id="handoffs",
            title="Conversational Handoffs",
            rows=cast(Sequence[Mapping[str, Any]], dashboard["handoffs"]),
            columns=("id", "agent_id", "slice_id", "status", "reason"),
        )
        + _table(
            section_id="agent-failures",
            title="Agent Failures",
            rows=cast(Sequence[Mapping[str, Any]], dashboard["failures"]),
            columns=("id", "agent_id", "slice_id", "category", "status", "summary"),
        )
    )
    connections = _table(
        section_id="external-connections",
        title="External Connections",
        rows=cast(Sequence[Mapping[str, Any]], dashboard["connections"]),
        columns=("id", "kind", "agent_id", "version", "status"),
    )
    provenance = _table(
        section_id="agent-provenance",
        title="Agent Provenance",
        rows=cast(Sequence[Mapping[str, Any]], dashboard["provenance"]),
        columns=(
            "id",
            "agent_id",
            "invocation_id",
            "slice_id",
            "artifact",
            "input_hash",
            "output_hash",
            "recorded_at",
        ),
    )
    page_specs = {
        "agent-activity": ("Agent Activity", activity),
        "agent-connections": ("Agent Connections", connections),
        "agent-provenance": ("Agent Provenance", provenance),
    }
    pages = {
        view_type: _page(
            view_type=view_type,
            title=title,
            run_id=run_id,
            updated_at=updated_at,
            source_hash=source_hash,
            body=body,
        )
        for view_type, (title, body) in page_specs.items()
    }
    bundle = AgentProjectionBundle(source_hash=source_hash, dashboard=dashboard, html=pages)
    _assert_redacted(bundle, sensitive_values)
    return bundle


def verify_agent_view(page: str, projection: Mapping[str, Any]) -> None:
    """Reject a malformed or stale agent projection."""
    match = _SOURCE_HASH.search(page)
    if match is None:
        raise StaleAgentProjectionError("agent view is missing its source projection hash")
    if match.group(1) != _source_hash(projection):
        raise StaleAgentProjectionError("agent view is stale for the supplied projection")
