from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any, Dict, Mapping, Optional, Protocol, Sequence, Tuple

_PLUGIN_REFERENCE = "plugin://seo-decision-lab@personal/"
_CREDENTIAL_KEYS = {
    "access_token",
    "api_key",
    "authorization",
    "credential",
    "credentials",
    "password",
    "private_key",
    "refresh_token",
    "secret",
    "token",
}


class SeoRoute(str, Enum):
    SHORTCUT = "shortcut"
    GENERIC = "generic"


def _canonical(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise ValueError("SEO request content must be JSON serializable") from error


def _redact(value: object, secrets: Sequence[str] = (), key: str = "") -> object:
    normalized = key.lower().replace("-", "_")
    if normalized in _CREDENTIAL_KEYS:
        return "[REDACTED]"
    if isinstance(value, Mapping):
        return {str(item): _redact(value[item], secrets, str(item)) for item in sorted(value, key=str)}
    if isinstance(value, (list, tuple)):
        return [_redact(item, secrets) for item in value]
    if isinstance(value, str):
        result = value
        for secret in sorted((item for item in secrets if item), key=len, reverse=True):
            result = result.replace(secret, "[REDACTED]")
        return result
    if value is None or isinstance(value, (bool, int, float)):
        return value
    raise ValueError("SEO receipt content must be JSON serializable")


@dataclass(frozen=True)
class SeoDecisionLabProfile:
    plugin_id: str
    version: str
    skill_reference: str
    mcp_reference: str
    auth_tool: str
    account_tool: str
    shortcut_tools: Tuple[str, ...]
    generic_tool: str


DEFAULT_SEO_PROFILE = SeoDecisionLabProfile(
    plugin_id="seo-decision-lab@personal",
    version="unresolved",
    skill_reference=f"{_PLUGIN_REFERENCE}skills/seo-decision-lab",
    mcp_reference=f"{_PLUGIN_REFERENCE}mcp/seo-decision-lab",
    auth_tool="dfs_auth_status",
    account_tool="dfs_account_status",
    shortcut_tools=(
        "dfs_google_serp_live",
        "dfs_labs_keyword_ideas",
        "dfs_labs_keyword_overview",
        "dfs_labs_ranked_keywords",
        "dfs_backlinks_summary",
        "dfs_llm_mentions_search",
        "dfs_onpage_task_post",
        "dfs_onpage_task_get",
    ),
    generic_tool="dfs_request",
)


def seo_decision_lab_profile(version: str) -> SeoDecisionLabProfile:
    if not version.strip() or version == "unresolved":
        raise ValueError("SEO Decision Lab version must come from discovery")
    return SeoDecisionLabProfile(**{**asdict(DEFAULT_SEO_PROFILE), "version": version})


@dataclass(frozen=True)
class SeoAssumptions:
    location_code: int = 2840
    language_code: str = "en"
    market: str = "United States"
    domain: str = ""
    intent: str = ""
    defaulted: Tuple[str, ...] = ("location_code", "language_code")

    def __post_init__(self) -> None:
        if self.location_code <= 0 or not self.language_code.strip():
            raise ValueError("SEO market and language assumptions must be valid")


@dataclass(frozen=True)
class SeoRequest:
    slice_id: str
    intent: str
    domain: str
    payload: Mapping[str, Any]
    assumptions: Optional[SeoAssumptions] = None

    def __post_init__(self) -> None:
        if not self.slice_id.strip() or not self.intent.strip():
            raise ValueError("SEO slice and intent must be non-empty")
        _canonical(self.payload)


@dataclass(frozen=True)
class SeoInvocationPlan:
    slice_id: str
    route: SeoRoute
    tool: str
    arguments: Mapping[str, Any]
    assumptions: SeoAssumptions
    request_hash: str
    profile_version: str
    profile_reference: str


@dataclass(frozen=True)
class SeoCostProvenance:
    amount: Optional[float]
    currency: Optional[str]
    source: str


@dataclass(frozen=True)
class SeoExecutionReceipt:
    status: str
    slice_id: str
    request_hash: str
    profile_version: str
    profile_reference: str
    tool: Optional[str]
    assumptions: SeoAssumptions
    auth_status: Mapping[str, Any]
    account_status: Optional[Mapping[str, Any]]
    output: Optional[object]
    cost: SeoCostProvenance
    blocked_slice_id: Optional[str] = None
    blocker_scope: Optional[str] = None
    reason: Optional[str] = None

    def to_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True, separators=(",", ":"))


class SeoMcp(Protocol):
    def call(self, tool: str, arguments: Mapping[str, Any]) -> object: ...


_SHORTCUTS = {
    "google_serp": "dfs_google_serp_live",
    "keyword_ideas": "dfs_labs_keyword_ideas",
    "keyword_overview": "dfs_labs_keyword_overview",
    "ranked_keywords": "dfs_labs_ranked_keywords",
    "backlinks_summary": "dfs_backlinks_summary",
    "llm_mentions": "dfs_llm_mentions_search",
    "onpage_start": "dfs_onpage_task_post",
    "onpage_result": "dfs_onpage_task_get",
}


class SeoDecisionLabAdapter:
    def __init__(self, profile: SeoDecisionLabProfile = DEFAULT_SEO_PROFILE) -> None:
        self.profile = profile

    def plan(self, request: SeoRequest) -> SeoInvocationPlan:
        assumptions = request.assumptions or SeoAssumptions(
            domain=request.domain,
            intent=request.intent,
        )
        if assumptions.domain and request.domain and assumptions.domain != request.domain:
            raise ValueError("SEO domain assumption conflicts with the request")
        arguments = _redact(request.payload)
        if not isinstance(arguments, dict):
            raise ValueError("SEO request payload must be an object")
        tool = _SHORTCUTS.get(request.intent)
        if tool is None:
            self._validate_generic(arguments)
            route = SeoRoute.GENERIC
            tool = self.profile.generic_tool
        else:
            route = SeoRoute.SHORTCUT
            arguments = self._shortcut_arguments(tool, arguments, request, assumptions)
        identity = {
            "slice_id": request.slice_id,
            "tool": tool,
            "arguments": arguments,
            "assumptions": asdict(assumptions),
            "profile_version": self.profile.version,
            "profile_reference": self.profile.mcp_reference,
        }
        request_hash = "sha256:" + hashlib.sha256(_canonical(identity)).hexdigest()
        return SeoInvocationPlan(
            slice_id=request.slice_id,
            route=route,
            tool=tool,
            arguments=arguments,
            assumptions=assumptions,
            request_hash=request_hash,
            profile_version=self.profile.version,
            profile_reference=self.profile.mcp_reference,
        )

    def execute(
        self,
        request: SeoRequest,
        mcp: SeoMcp,
        *,
        secret_values: Sequence[str] = (),
    ) -> SeoExecutionReceipt:
        plan = self.plan(request)
        auth = _redact(mcp.call(self.profile.auth_tool, {}), secret_values)
        if not isinstance(auth, dict):
            raise ValueError("dfs_auth_status must return an object")
        if not auth.get("configured", False):
            return SeoExecutionReceipt(
                status="blocked",
                slice_id=request.slice_id,
                request_hash=plan.request_hash,
                profile_version=plan.profile_version,
                profile_reference=plan.profile_reference,
                tool=None,
                assumptions=plan.assumptions,
                auth_status=auth,
                account_status=None,
                output=None,
                cost=SeoCostProvenance(None, None, "not incurred"),
                blocked_slice_id=request.slice_id,
                blocker_scope="slice",
                reason="DataForSEO authentication is not configured",
            )
        account = _redact(mcp.call(self.profile.account_tool, {}), secret_values)
        if not isinstance(account, dict):
            raise ValueError("dfs_account_status must return an object")
        output = _redact(mcp.call(plan.tool, plan.arguments), secret_values)
        amount = _find_cost(output)
        currency_value = account.get("currency")
        currency = currency_value if isinstance(currency_value, str) else None
        return SeoExecutionReceipt(
            status="succeeded",
            slice_id=request.slice_id,
            request_hash=plan.request_hash,
            profile_version=plan.profile_version,
            profile_reference=plan.profile_reference,
            tool=plan.tool,
            assumptions=plan.assumptions,
            auth_status=auth,
            account_status=account,
            output=output,
            cost=SeoCostProvenance(
                amount,
                currency,
                "response.tasks[].cost" if amount is not None else "not reported",
            ),
        )

    @staticmethod
    def _validate_generic(arguments: Mapping[str, Any]) -> None:
        path = arguments.get("path")
        method = arguments.get("method", "POST")
        if not isinstance(path, str) or not path.startswith("/v3/") or ".." in path:
            raise ValueError("generic SEO routing requires a documented /v3 path")
        if method not in {"GET", "POST"}:
            raise ValueError("generic SEO routing supports GET or POST")

    @staticmethod
    def _shortcut_arguments(
        tool: str,
        payload: Dict[str, Any],
        request: SeoRequest,
        assumptions: SeoAssumptions,
    ) -> Dict[str, Any]:
        arguments = dict(payload)
        market_tools = {
            "dfs_google_serp_live",
            "dfs_labs_keyword_ideas",
            "dfs_labs_keyword_overview",
            "dfs_labs_ranked_keywords",
            "dfs_llm_mentions_search",
        }
        if tool in market_tools:
            arguments.setdefault("locationCode", assumptions.location_code)
            arguments.setdefault("languageCode", assumptions.language_code)
        if tool == "dfs_labs_ranked_keywords":
            arguments.setdefault("target", request.domain)
        return arguments


def _find_cost(value: object) -> Optional[float]:
    if isinstance(value, Mapping):
        cost = value.get("cost")
        if isinstance(cost, (int, float)) and not isinstance(cost, bool):
            return float(cost)
        for key in sorted(value, key=str):
            found = _find_cost(value[key])
            if found is not None:
                return found
    elif isinstance(value, (list, tuple)):
        for item in value:
            found = _find_cost(item)
            if found is not None:
                return found
    return None


class DeterministicFakeSeoMcp:
    def __init__(
        self,
        authenticated: bool,
        *,
        account: Optional[Mapping[str, Any]] = None,
        responses: Optional[Mapping[str, object]] = None,
    ) -> None:
        self.authenticated = authenticated
        self.account = dict(account or {"balance": 0, "currency": "USD"})
        self.responses = dict(responses or {})
        self._calls: list[Tuple[str, Mapping[str, Any]]] = []

    @property
    def calls(self) -> Tuple[Tuple[str, Mapping[str, Any]], ...]:
        return tuple(self._calls)

    def call(self, tool: str, arguments: Mapping[str, Any]) -> object:
        safe_arguments = _redact(arguments)
        if not isinstance(safe_arguments, dict):
            raise ValueError("fake MCP arguments must be an object")
        self._calls.append((tool, safe_arguments))
        if tool == DEFAULT_SEO_PROFILE.auth_tool:
            return {"configured": self.authenticated, "active_source": "fake"}
        if tool == DEFAULT_SEO_PROFILE.account_tool:
            return dict(self.account)
        return self.responses.get(tool, {"tasks": [], "tool": tool})
