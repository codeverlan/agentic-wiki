from __future__ import annotations

import json
from copy import deepcopy

import pytest

from memwiki.coordinator_views import (
    ProjectionBundle,
    StaleProjectionError,
    render_coordinator_views,
    verify_projection_view,
)


def projection() -> dict[str, object]:
    return {
        "schema_version": 1,
        "run_id": "run-037",
        "objective": "Build <the> coordinator",
        "status": "running",
        "updated_at": "2026-07-11T18:00:00+00:00",
        "source_revision": 42,
        "slices": [
            {"id": "AC-1", "title": "Active <slice>", "status": "active", "worker_id": "w-1"},
            {"id": "AC-2", "title": "Pending", "status": "ready"},
            {"id": "AC-3", "title": "Done", "status": "completed"},
        ],
        "agents": [{"id": "w-1", "status": "working", "slice_id": "AC-1", "last_seen": "18:00"}],
        "resources": {"tokens": {"used": 120, "remaining": 880}, "cost": {"used": None}},
        "blockers": [{"id": "b-1", "scope": "AC-9", "summary": "Needs user input", "status": "open"}],
        "supervision": [{"id": "s-1", "summary": "Approve publish", "priority": "mandatory", "status": "open"}],
        "external_connections": [
            {"id": "github", "kind": "api", "status": "available", "credential_ref": "secret-token"},
            {"id": "browser", "kind": "plugin", "status": "missing"},
        ],
        "artifacts": [
            {"category": "status", "title": "Run state", "path": "status/run.html"},
            {"category": "evidence", "title": "Report", "path": "evidence/report.html"},
        ],
        "branches": [
            {"name": "codex/ac-1", "updated_at": "2026-07-11T17:59:00+00:00", "description": "AC-1 work"}
        ],
        "incidents": [{"id": "inc-1", "severity": "warning", "status": "open", "summary": "Retry exhausted"}],
        "handoff": {"summary": "Continue active work", "resume_instruction": "Run coordinator resume"},
        "controls": [{"name": "Stop", "status": "available", "href": "controls/stop.html"}],
    }


def test_renders_all_semantic_standalone_views_from_one_projection() -> None:
    bundle = render_coordinator_views(projection())

    assert isinstance(bundle, ProjectionBundle)
    assert set(bundle.html) == {"run-state", "handoff", "incidents", "dashboard", "control-index"}
    for name, page in bundle.html.items():
        assert page.startswith("<!doctype html>")
        assert '<script type="application/ld+json">' in page
        assert f'"memwiki:viewType": "{name}"' in page
        assert bundle.source_hash in page
        verify_projection_view(page, projection())


def test_dashboard_contains_requested_operational_surfaces() -> None:
    bundle = render_coordinator_views(projection())
    dashboard = bundle.html["dashboard"]

    for section in (
        "active-slices",
        "pending-slices",
        "completed-slices",
        "agent-activity",
        "resource-use",
        "blockers",
        "supervision",
        "external-connections",
        "artifacts",
        "branch-ledger",
    ):
        assert f'id="{section}"' in dashboard
    assert "codex/ac-1" in dashboard
    assert "2026-07-11T17:59:00+00:00" in dashboard
    assert "AC-1 work" in dashboard


def test_external_connections_show_presence_without_credentials() -> None:
    bundle = render_coordinator_views(projection())
    serialized = json.dumps(bundle.dashboard, sort_keys=True)
    joined_html = "".join(bundle.html.values())

    assert "github" in serialized and "available" in serialized
    assert "secret-token" not in serialized
    assert "credential_ref" not in serialized
    assert "secret-token" not in joined_html


def test_rendering_is_deterministic_xss_safe_and_source_is_not_mutated() -> None:
    source = projection()
    source["handoff"] = {
        "summary": '<script>alert("x")</script>',
        "resume_instruction": "resume & verify",
    }
    before = deepcopy(source)

    first = render_coordinator_views(source)
    second = render_coordinator_views(source)

    assert first == second
    assert source == before
    assert '<script>alert("x")</script>' not in "".join(first.html.values())
    assert "&lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt;" in first.html["handoff"]


def test_stale_or_tampered_views_are_rejected() -> None:
    source = projection()
    page = render_coordinator_views(source).html["run-state"]
    changed = deepcopy(source)
    changed["source_revision"] = 43

    with pytest.raises(StaleProjectionError, match="stale"):
        verify_projection_view(page, changed)
    with pytest.raises(StaleProjectionError, match="missing"):
        verify_projection_view(page.replace("memwiki:sourceHash", "memwiki:noHash"), source)


def test_dashboard_json_is_bounded_and_rejects_non_json_or_missing_identity() -> None:
    source = projection()
    source["extra"] = object()
    with pytest.raises(ValueError, match="JSON-compatible"):
        render_coordinator_views(source)

    source = projection()
    del source["run_id"]
    with pytest.raises(ValueError, match="run_id"):
        render_coordinator_views(source)

    source = projection()
    source["slices"] = [
        {"id": f"AC-{index}", "title": "queued", "status": "ready"} for index in range(501)
    ]
    with pytest.raises(ValueError, match="500"):
        render_coordinator_views(source)


def test_dashboard_json_snapshot_has_stable_shape() -> None:
    dashboard = render_coordinator_views(projection()).dashboard
    assert dashboard == {
        "schema_version": 1,
        "run_id": "run-037",
        "source_hash": dashboard["source_hash"],
        "source_revision": 42,
        "status": "running",
        "updated_at": "2026-07-11T18:00:00+00:00",
        "counts": {"active": 1, "pending": 1, "completed": 1},
        "agents": [{"id": "w-1", "last_seen": "18:00", "slice_id": "AC-1", "status": "working"}],
        "resources": {"cost": {"used": None}, "tokens": {"remaining": 880, "used": 120}},
        "blockers": [{"id": "b-1", "scope": "AC-9", "status": "open", "summary": "Needs user input"}],
        "supervision": [{"id": "s-1", "priority": "mandatory", "status": "open", "summary": "Approve publish"}],
        "external_connections": [
            {"id": "browser", "kind": "plugin", "status": "missing"},
            {"id": "github", "kind": "api", "status": "available"},
        ],
        "artifacts": [
            {"category": "evidence", "path": "evidence/report.html", "title": "Report"},
            {"category": "status", "path": "status/run.html", "title": "Run state"},
        ],
    }
