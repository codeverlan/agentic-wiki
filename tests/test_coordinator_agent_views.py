from __future__ import annotations

import json
from copy import deepcopy

import pytest

from memwiki.coordinator_agent_views import (
    AgentProjectionBundle,
    StaleAgentProjectionError,
    render_agent_views,
    verify_agent_view,
)


def projection() -> dict[str, object]:
    return {
        "schema_version": 1,
        "run_id": "run-agents-011",
        "source_revision": 17,
        "updated_at": "2026-07-11T19:30:00+00:00",
        "agents": [
            {
                "agent_id": "seo-decision-lab",
                "display_name": "SEO <Decision> Lab",
                "product": "codex",
                "classification": "personalized_private",
                "version": "0.1.0",
                "observed_version": "0.2.0",
                "drift": "upgrade_available",
                "trust": "trusted",
                "availability": "available",
                "marketplace": "personal",
                "hidden_prompt": "never render this instruction",
            },
            {
                "agent_id": "public-research",
                "display_name": "Public Research",
                "product": "chatgpt",
                "classification": "public",
                "version": "3",
                "drift": "none",
                "trust": "verified",
                "availability": "handoff_only",
            },
        ],
        "invocations": [
            {
                "id": "inv-2",
                "agent_id": "public-research",
                "slice_id": "AX-13",
                "operation": "research",
                "status": "handoff_required",
                "started_at": None,
                "cost": None,
                "output": "sensitive result must not render",
            },
            {
                "id": "inv-1",
                "agent_id": "seo-decision-lab",
                "slice_id": "AX-8",
                "operation": "keyword_research",
                "status": "started",
                "started_at": "2026-07-11T19:29:00+00:00",
                "cost": {"amount": "0.04", "currency": "USD", "status": "reported"},
                "api_key": "dfs-secret",
            },
        ],
        "handoffs": [
            {
                "id": "handoff-1",
                "agent_id": "public-research",
                "slice_id": "AX-13",
                "status": "waiting_for_user",
                "reason": "Open public agent",
            }
        ],
        "failures": [
            {
                "id": "failure-1",
                "agent_id": "seo-decision-lab",
                "slice_id": "AX-8",
                "category": "missing_auth",
                "status": "open",
                "summary": "Credential reference unavailable",
                "details": "Authorization: Bearer dfs-secret",
            }
        ],
        "connections": [
            {
                "id": "dataforseo",
                "kind": "mcp",
                "agent_id": "seo-decision-lab",
                "version": "0.1.0",
                "status": "available",
                "authorization_present": True,
                "credential_ref": "local-secret",
            }
        ],
        "provenance": [
            {
                "id": "prov-1",
                "agent_id": "seo-decision-lab",
                "invocation_id": "inv-1",
                "slice_id": "AX-8",
                "artifact": "drafts/seo/report.html",
                "input_hash": "sha256:" + "1" * 64,
                "output_hash": "sha256:" + "2" * 64,
                "recorded_at": "2026-07-11T19:30:00+00:00",
                "result": "private result",
            }
        ],
    }


def test_renders_semantic_standalone_agent_views() -> None:
    bundle = render_agent_views(projection())

    assert isinstance(bundle, AgentProjectionBundle)
    assert set(bundle.html) == {"agent-activity", "agent-connections", "agent-provenance"}
    for view_type, page in bundle.html.items():
        assert page.startswith("<!doctype html>")
        assert '<script type="application/ld+json">' in page
        assert f'"memwiki:viewType": "{view_type}"' in page
        assert bundle.source_hash in page
        verify_agent_view(page, projection())


def test_dashboard_snapshot_is_deterministic_and_allowlisted() -> None:
    dashboard = render_agent_views(projection()).dashboard

    assert dashboard == {
        "schema_version": 1,
        "run_id": "run-agents-011",
        "source_revision": 17,
        "source_hash": dashboard["source_hash"],
        "updated_at": "2026-07-11T19:30:00+00:00",
        "counts": {
            "agents": 2,
            "available": 1,
            "active_invocations": 1,
            "handoffs": 1,
            "open_failures": 1,
            "connections": 1,
        },
        "agents": [
            {
                "agent_id": "public-research",
                "availability": "handoff_only",
                "classification": "public",
                "display_name": "Public Research",
                "drift": "none",
                "observed_version": None,
                "product": "chatgpt",
                "trust": "verified",
                "version": "3",
            },
            {
                "agent_id": "seo-decision-lab",
                "availability": "available",
                "classification": "personalized_private",
                "display_name": "SEO <Decision> Lab",
                "drift": "upgrade_available",
                "observed_version": "0.2.0",
                "product": "codex",
                "trust": "trusted",
                "version": "0.1.0",
            },
        ],
        "active_invocations": [
            {
                "agent_id": "seo-decision-lab",
                "cost": {"amount": "0.04", "currency": "USD", "status": "reported"},
                "id": "inv-1",
                "operation": "keyword_research",
                "slice_id": "AX-8",
                "started_at": "2026-07-11T19:29:00+00:00",
                "status": "started",
            }
        ],
        "handoffs": [
            {
                "agent_id": "public-research",
                "id": "handoff-1",
                "reason": "Open public agent",
                "slice_id": "AX-13",
                "status": "waiting_for_user",
            }
        ],
        "failures": [
            {
                "agent_id": "seo-decision-lab",
                "category": "missing_auth",
                "id": "failure-1",
                "slice_id": "AX-8",
                "status": "open",
                "summary": "Credential reference unavailable",
            }
        ],
        "connections": [
            {
                "agent_id": "seo-decision-lab",
                "id": "dataforseo",
                "kind": "mcp",
                "status": "available",
                "version": "0.1.0",
            }
        ],
        "provenance": [
            {
                "agent_id": "seo-decision-lab",
                "artifact": "drafts/seo/report.html",
                "id": "prov-1",
                "input_hash": "sha256:" + "1" * 64,
                "invocation_id": "inv-1",
                "output_hash": "sha256:" + "2" * 64,
                "recorded_at": "2026-07-11T19:30:00+00:00",
                "slice_id": "AX-8",
            }
        ],
    }


def test_views_exclude_credentials_prompts_phi_and_sensitive_results() -> None:
    bundle = render_agent_views(projection(), sensitive_values=("dfs-secret", "private result"))
    rendered = json.dumps(bundle.dashboard, sort_keys=True) + "".join(bundle.html.values())

    for forbidden in (
        "dfs-secret",
        "local-secret",
        "private result",
        "sensitive result",
        "never render this instruction",
        "credential_ref",
        "authorization_present",
        "hidden_prompt",
    ):
        assert forbidden not in rendered


def test_html_is_xss_safe_and_source_is_not_mutated() -> None:
    source = projection()
    source["handoffs"] = [
        {
            "id": "x",
            "agent_id": "public-research",
            "slice_id": "AX-13",
            "status": "waiting",
            "reason": '<script>alert("x")</script>',
        }
    ]
    before = deepcopy(source)

    first = render_agent_views(source)
    second = render_agent_views(source)

    assert first == second
    assert source == before
    rendered = "".join(first.html.values())
    assert '<script>alert("x")</script>' not in rendered
    assert "&lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt;" in rendered


def test_stale_missing_or_tampered_source_hash_is_rejected() -> None:
    source = projection()
    page = render_agent_views(source).html["agent-activity"]
    changed = deepcopy(source)
    changed["source_revision"] = 18

    with pytest.raises(StaleAgentProjectionError, match="stale"):
        verify_agent_view(page, changed)
    with pytest.raises(StaleAgentProjectionError, match="missing"):
        verify_agent_view(page.replace("memwiki:sourceHash", "memwiki:noHash"), source)


def test_rejects_unbounded_or_invalid_projection() -> None:
    source = projection()
    del source["run_id"]
    with pytest.raises(ValueError, match="run_id"):
        render_agent_views(source)

    source = projection()
    source["agents"] = [{} for _ in range(501)]
    with pytest.raises(ValueError, match="500"):
        render_agent_views(source)

    source = projection()
    source["extra"] = object()
    with pytest.raises(ValueError, match="JSON-compatible"):
        render_agent_views(source)
