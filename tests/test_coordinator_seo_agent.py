from __future__ import annotations

from dataclasses import replace

import pytest

from memwiki.coordinator_seo_agent import (
    DEFAULT_SEO_PROFILE,
    DeterministicFakeSeoMcp,
    SeoAssumptions,
    SeoDecisionLabAdapter,
    SeoRequest,
    SeoRoute,
    seo_decision_lab_profile,
)


def request(**overrides: object) -> SeoRequest:
    values: dict[str, object] = {
        "slice_id": "SEO-12",
        "intent": "keyword_ideas",
        "domain": "example.com",
        "payload": {"keywords": ["therapy"]},
    }
    values.update(overrides)
    return SeoRequest(**values)  # type: ignore[arg-type]


def test_profile_uses_portable_plugin_references_and_discovered_version() -> None:
    profile = DEFAULT_SEO_PROFILE
    assert profile.plugin_id == "seo-decision-lab@personal"
    assert profile.version == "unresolved"
    assert profile.skill_reference.startswith("plugin://seo-decision-lab@personal/")
    assert profile.mcp_reference.startswith("plugin://seo-decision-lab@personal/")
    assert profile.auth_tool == "dfs_auth_status"
    assert profile.account_tool == "dfs_account_status"
    assert "dfs_labs_keyword_ideas" in profile.shortcut_tools
    assert profile.generic_tool == "dfs_request"
    discovered = seo_decision_lab_profile("0.1.0+codex.test")
    assert discovered.version == "0.1.0+codex.test"
    with pytest.raises(ValueError, match="discovery"):
        seo_decision_lab_profile("unresolved")


def test_shortcut_route_applies_recorded_us_defaults() -> None:
    plan = SeoDecisionLabAdapter().plan(request())
    assert plan.route is SeoRoute.SHORTCUT
    assert plan.tool == "dfs_labs_keyword_ideas"
    assert plan.arguments["locationCode"] == 2840
    assert plan.arguments["languageCode"] == "en"
    assert plan.assumptions.defaulted == ("location_code", "language_code")
    assert plan.assumptions.domain == "example.com"
    assert plan.assumptions.intent == "keyword_ideas"


def test_explicit_market_language_domain_and_intent_are_preserved() -> None:
    assumptions = SeoAssumptions(
        location_code=2826,
        language_code="en",
        market="United Kingdom",
        domain="example.co.uk",
        intent="ranked_keywords",
        defaulted=(),
    )
    plan = SeoDecisionLabAdapter().plan(
        request(
            intent="ranked_keywords",
            domain="example.co.uk",
            assumptions=assumptions,
            payload={"limit": 25},
        )
    )
    assert plan.arguments["target"] == "example.co.uk"
    assert plan.arguments["locationCode"] == 2826
    assert plan.assumptions.market == "United Kingdom"
    assert plan.assumptions.defaulted == ()


def test_unknown_workflow_uses_valid_generic_dfs_request() -> None:
    plan = SeoDecisionLabAdapter().plan(
        request(
            intent="business_listings",
            payload={
                "method": "POST",
                "path": "/v3/business_data/business_listings/search/live",
                "body": [{"categories": ["psychologist"]}],
            },
        )
    )
    assert plan.route is SeoRoute.GENERIC
    assert plan.tool == "dfs_request"
    assert plan.arguments["path"] == "/v3/business_data/business_listings/search/live"


@pytest.mark.parametrize("path", ["https://example.test/v3/a", "/v2/a", "../v3/a"])
def test_generic_route_rejects_non_v3_paths(path: str) -> None:
    with pytest.raises(ValueError, match="documented /v3 path"):
        SeoDecisionLabAdapter().plan(request(intent="custom", payload={"method": "POST", "path": path}))


def test_execution_checks_auth_and_account_before_costed_request() -> None:
    mcp = DeterministicFakeSeoMcp(
        authenticated=True,
        account={"balance": 42.5, "currency": "USD"},
        responses={"dfs_labs_keyword_ideas": {"tasks": [{"cost": 0.021}]}},
    )
    result = SeoDecisionLabAdapter(seo_decision_lab_profile("0.1.0+codex.test")).execute(request(), mcp)
    assert mcp.calls == (
        ("dfs_auth_status", {}),
        ("dfs_account_status", {}),
        (
            "dfs_labs_keyword_ideas",
            {
                "keywords": ["therapy"],
                "locationCode": 2840,
                "languageCode": "en",
            },
        ),
    )
    assert result.status == "succeeded"
    assert result.cost.amount == 0.021
    assert result.cost.currency == "USD"
    assert result.cost.source == "response.tasks[].cost"
    assert result.request_hash.startswith("sha256:")
    assert result.profile_version == "0.1.0+codex.test"


def test_missing_auth_blocks_only_the_seo_slice() -> None:
    mcp = DeterministicFakeSeoMcp(authenticated=False)
    result = SeoDecisionLabAdapter().execute(request(), mcp)
    assert result.status == "blocked"
    assert result.blocked_slice_id == "SEO-12"
    assert result.blocker_scope == "slice"
    assert result.reason == "DataForSEO authentication is not configured"
    assert mcp.calls == (("dfs_auth_status", {}),)


def test_request_hash_is_deterministic_and_changes_with_assumptions() -> None:
    adapter = SeoDecisionLabAdapter()
    first = adapter.plan(request())
    second = adapter.plan(request())
    changed = adapter.plan(request(assumptions=replace(first.assumptions, location_code=2124, defaulted=())))
    assert first.request_hash == second.request_hash
    assert first.request_hash != changed.request_hash


def test_recursive_redaction_covers_inputs_outputs_and_receipt() -> None:
    secret = "dfs-secret-value"
    mcp = DeterministicFakeSeoMcp(
        authenticated=True,
        account={"balance": 1, "password": secret},
        responses={
            "dfs_labs_keyword_ideas": {
                "tasks": [{"cost": 0, "result": [{"authorization": secret}]}],
                "nested": {"note": f"prefix {secret} suffix"},
            }
        },
    )
    result = SeoDecisionLabAdapter().execute(
        request(payload={"keywords": ["therapy"], "api_key": secret}),
        mcp,
        secret_values=(secret,),
    )
    serialized = result.to_json()
    assert secret not in serialized
    assert "[REDACTED]" in serialized
    assert result.request_hash.startswith("sha256:")


def test_fake_mcp_is_deterministic_and_never_needs_live_credentials() -> None:
    fake = DeterministicFakeSeoMcp(authenticated=True)
    first = SeoDecisionLabAdapter().execute(request(), fake)
    second = SeoDecisionLabAdapter().execute(request(), DeterministicFakeSeoMcp(True))
    assert first.to_json() == second.to_json()
