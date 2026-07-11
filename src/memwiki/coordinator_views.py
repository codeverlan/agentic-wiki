from __future__ import annotations

import hashlib
import html
import json
import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, Dict, Mapping, Sequence, Tuple, cast

VIEW_TYPES = ("run-state", "handoff", "incidents", "dashboard", "control-index")
MAX_ITEMS = 500
MAX_SOURCE_BYTES = 2 * 1024 * 1024
MAX_DASHBOARD_BYTES = 256 * 1024
_SOURCE_HASH = re.compile(r'"memwiki:sourceHash"\s*:\s*"([0-9a-f]{64})"')
_SECRET_KEY = re.compile(
    r"(?i)(credential|password|passwd|secret|api[_-]?key|access[_-]?token|authorization|bearer)"
)


class StaleProjectionError(ValueError):
    """Raised when a generated view does not match its authoritative projection."""


@dataclass(frozen=True)
class ProjectionBundle:
    source_hash: str
    dashboard: Mapping[str, Any]
    html: Mapping[str, str]


def _canonical(value: Any) -> bytes:
    try:
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError("projection must be JSON-compatible") from exc
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


def _safe_href(value: Any) -> str:
    path = _text(value)
    parsed = PurePosixPath(path)
    if path.startswith(("/", "\\")) or ".." in parsed.parts or ":" in parsed.parts[0]:
        return "#"
    return html.escape(path, quote=True)


def _selected(item: Mapping[str, Any], fields: Sequence[str]) -> Dict[str, Any]:
    return {field: item[field] for field in fields if field in item and not _SECRET_KEY.search(field)}


def _sorted_selected(
    items: Sequence[Mapping[str, Any]], fields: Sequence[str], sort_fields: Sequence[str]
) -> list[Dict[str, Any]]:
    selected = [_selected(item, fields) for item in items]
    return sorted(selected, key=lambda item: tuple(_text(item.get(key, "")) for key in sort_fields))


def _table(
    *, section_id: str, title: str, rows: Sequence[Mapping[str, Any]], columns: Sequence[str]
) -> str:
    headings = "".join(f"<th scope=\"col\">{_escape(column.replace('_', ' ').title())}</th>" for column in columns)
    body_rows = []
    for row in rows:
        cells = "".join(f"<td>{_escape(row.get(column))}</td>" for column in columns)
        body_rows.append(f"<tr>{cells}</tr>")
    if not body_rows:
        body_rows.append(f'<tr><td colspan="{len(columns)}">None recorded.</td></tr>')
    return (
        f'<section id="{section_id}"><h2>{_escape(title)}</h2><table><thead><tr>{headings}</tr></thead>'
        f"<tbody>{''.join(body_rows)}</tbody></table></section>"
    )


def _artifact_table(rows: Sequence[Mapping[str, Any]]) -> str:
    rendered = []
    for row in rows:
        rendered.append(
            "<tr>"
            f"<td>{_escape(row.get('category'))}</td><td>{_escape(row.get('title'))}</td>"
            f"<td><a href=\"{_safe_href(row.get('path'))}\">{_escape(row.get('path'))}</a></td>"
            "</tr>"
        )
    if not rendered:
        rendered.append('<tr><td colspan="3">None recorded.</td></tr>')
    return (
        '<section id="artifacts"><h2>Categorized HTML Artifacts</h2><table><thead><tr>'
        '<th scope="col">Category</th><th scope="col">Title</th><th scope="col">Path</th>'
        f"</tr></thead><tbody>{''.join(rendered)}</tbody></table></section>"
    )


def _page(*, view_type: str, title: str, run_id: str, updated_at: str, source_hash: str, body: str) -> str:
    metadata = {
        "@context": "https://schema.org",
        "@type": "CreativeWork",
        "identifier": f"{run_id}:{view_type}:{source_hash}",
        "name": title,
        "dateModified": updated_at,
        "memwiki:authoritative": False,
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
</style>
</head><body><header><h1>{_escape(title)}</h1>
<p>Run <code>{_escape(run_id)}</code> | Updated {_escape(updated_at)}</p></header>
<nav aria-label="Coordinator views">{navigation}</nav>
<main>{body}</main><footer><p>Generated view of authoritative projection
<code>{source_hash}</code>.</p></footer></body></html>
"""


def _dashboard(projection: Mapping[str, Any], source_hash: str) -> Dict[str, Any]:
    slices = _items(projection, "slices")
    pending_statuses = {"pending", "waiting", "ready", "blocked", "unreachable"}
    counts = {
        "active": sum(item.get("status") == "active" for item in slices),
        "pending": sum(item.get("status") in pending_statuses for item in slices),
        "completed": sum(item.get("status") == "completed" for item in slices),
    }
    result: Dict[str, Any] = {
        "schema_version": 1,
        "run_id": projection["run_id"],
        "source_hash": source_hash,
        "source_revision": projection.get("source_revision"),
        "status": projection.get("status", "unknown"),
        "updated_at": projection.get("updated_at", "unknown"),
        "counts": counts,
        "agents": _sorted_selected(_items(projection, "agents"), ("id", "last_seen", "slice_id", "status"), ("id",)),
        "resources": projection.get("resources", {}),
        "blockers": _sorted_selected(_items(projection, "blockers"), ("id", "scope", "status", "summary"), ("id",)),
        "supervision": _sorted_selected(
            _items(projection, "supervision"),
            ("id", "priority", "status", "summary"),
            ("id",),
        ),
        "external_connections": _sorted_selected(
            _items(projection, "external_connections"), ("id", "kind", "status"), ("id",)
        ),
        "artifacts": _sorted_selected(
            _items(projection, "artifacts"),
            ("category", "path", "title"),
            ("category", "path"),
        ),
    }
    encoded = _canonical(result)
    if len(encoded) > MAX_DASHBOARD_BYTES:
        raise ValueError("dashboard projection exceeds size limit")
    return result


def render_coordinator_views(projection: Mapping[str, Any]) -> ProjectionBundle:
    """Generate bounded, non-authoritative views of one authoritative run projection."""
    if not isinstance(projection, Mapping):
        raise ValueError("projection must be an object")
    _canonical(projection)
    run_id = projection.get("run_id")
    if not isinstance(run_id, str) or not run_id.strip():
        raise ValueError("run_id must be a non-empty string")
    source_hash = _source_hash(projection)
    updated_at = _text(projection.get("updated_at"))
    slices = _items(projection, "slices")
    active = [item for item in slices if item.get("status") == "active"]
    pending_statuses = {"pending", "waiting", "ready", "blocked", "unreachable"}
    pending = [item for item in slices if item.get("status") in pending_statuses]
    completed = [item for item in slices if item.get("status") == "completed"]
    slice_columns = ("id", "title", "status", "worker_id")
    dashboard = _dashboard(projection, source_hash)

    dashboard_body = "".join(
        (
            _table(section_id="active-slices", title="Active", rows=active, columns=slice_columns),
            _table(section_id="pending-slices", title="Pending", rows=pending, columns=slice_columns),
            _table(section_id="completed-slices", title="Completed", rows=completed, columns=slice_columns),
            _table(
                section_id="agent-activity",
                title="Agent Activity",
                rows=_items(projection, "agents"),
                columns=("id", "status", "slice_id", "last_seen"),
            ),
            '<section id="resource-use"><h2>Token and Resource Use</h2>'
            f'<pre>{_escape(projection.get("resources", {}))}</pre></section>',
            _table(
                section_id="blockers",
                title="Blockers",
                rows=_items(projection, "blockers"),
                columns=("id", "scope", "status", "summary"),
            ),
            _table(
                section_id="supervision",
                title="Supervision",
                rows=_items(projection, "supervision"),
                columns=("id", "priority", "status", "summary"),
            ),
            _table(
                section_id="external-connections",
                title="External Connections",
                rows=dashboard["external_connections"],
                columns=("id", "kind", "status"),
            ),
            _artifact_table(cast(Sequence[Mapping[str, Any]], dashboard["artifacts"])),
            _table(
                section_id="branch-ledger",
                title="Branch Ledger",
                rows=_items(projection, "branches"),
                columns=("name", "updated_at", "description"),
            ),
        )
    )
    handoff = projection.get("handoff", {})
    if not isinstance(handoff, Mapping):
        raise ValueError("handoff must be an object")
    run_body = (
        '<section id="run-summary"><h2>Status</h2><dl>'
        f'<dt>Objective</dt><dd>{_escape(projection.get("objective"))}</dd>'
        f'<dt>Status</dt><dd>{_escape(projection.get("status"))}</dd>'
        f'<dt>Source revision</dt><dd>{_escape(projection.get("source_revision"))}</dd>'
        "</dl></section>"
        + dashboard_body[: dashboard_body.find('<section id="resource-use"')]
    )
    handoff_body = (
        '<section id="handoff"><h2>Resume</h2>'
        f'<p>{_escape(handoff.get("summary"))}</p><p><strong>Instruction:</strong> '
        f'{_escape(handoff.get("resume_instruction"))}</p></section>'
        + _table(
            section_id="handoff-blockers",
            title="Open Blockers",
            rows=_items(projection, "blockers"),
            columns=("id", "scope", "status", "summary"),
        )
        + _table(
            section_id="handoff-supervision",
            title="Pending Supervision",
            rows=_items(projection, "supervision"),
            columns=("id", "priority", "status", "summary"),
        )
    )
    incident_body = _table(
        section_id="incident-log",
        title="Incident Log",
        rows=_items(projection, "incidents"),
        columns=("id", "severity", "status", "summary"),
    )
    control_body = _table(
        section_id="controls",
        title="Controls",
        rows=_items(projection, "controls"),
        columns=("name", "status", "href"),
    ) + _artifact_table(cast(Sequence[Mapping[str, Any]], dashboard["artifacts"]))
    page_bodies = {
        "run-state": ("Coordinator Run State", run_body),
        "handoff": ("Coordinator Handoff", handoff_body),
        "incidents": ("Coordinator Incidents", incident_body),
        "dashboard": ("Coordinator Dashboard", dashboard_body),
        "control-index": ("Coordinator Control Index", control_body),
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
        for view_type, (title, body) in page_bodies.items()
    }
    return ProjectionBundle(source_hash=source_hash, dashboard=dashboard, html=pages)


def verify_projection_view(page: str, projection: Mapping[str, Any]) -> None:
    """Reject a view that is stale, malformed, or detached from its source projection."""
    match = _SOURCE_HASH.search(page)
    if match is None:
        raise StaleProjectionError("view is missing its source projection hash")
    if match.group(1) != _source_hash(projection):
        raise StaleProjectionError("view is stale for the supplied authoritative projection")
