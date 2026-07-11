from __future__ import annotations

import html
import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime
from enum import Enum
from typing import Dict, List, Mapping, Optional, Tuple

from memwiki.coordinator_privacy import DataProfile

_HASH = re.compile(r"^[0-9a-f]{64}$")
_CREDENTIAL = re.compile(
    r"(?i)(api[_-]?key|password|passwd|secret|access[_-]?token|refresh[_-]?token|authorization)\s*[=:]"
)
_VIEWPORTS = {"desktop", "mobile", "tablet"}


def _text(value: str, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be non-empty")
    return value.strip()


def _aware(value: datetime, label: str) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware")


def _safe_external(value: str) -> str:
    text = _text(value, "external reference")
    if _CREDENTIAL.search(text) or "@" in text.partition("://")[2].partition("/")[0]:
        raise ValueError("external reference must not contain credential material")
    return text


def _list(value: object, label: str) -> List[object]:
    if not isinstance(value, list):
        raise ValueError(f"{label} must be a list")
    return value


def _mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or not all(isinstance(key, str) for key in value):
        raise ValueError(f"{label} must be an object")
    return value


@dataclass(frozen=True)
class DesignReference:
    reference_id: str
    kind: str
    label: str
    locator: str
    version: Optional[str]

    def __post_init__(self) -> None:
        _text(self.reference_id, "reference id")
        if self.kind not in {"api", "asset", "image", "plugin", "skill", "url"}:
            raise ValueError("unsupported design reference kind")
        _safe_external(self.label)
        _safe_external(self.locator)
        if self.version is not None:
            _safe_external(self.version)


@dataclass(frozen=True)
class ScreenInventoryItem:
    screen_id: str
    route: str
    states: Tuple[str, ...]

    def __post_init__(self) -> None:
        _text(self.screen_id, "screen id")
        if not self.route.startswith("/"):
            raise ValueError("screen route must be project-relative")
        if not self.states or any(not state.strip() for state in self.states):
            raise ValueError("screen states must be non-empty")


@dataclass(frozen=True)
class DesignDecision:
    decision_id: str
    version: int
    summary: str
    status: str
    recorded_at: datetime

    def __post_init__(self) -> None:
        _text(self.decision_id, "decision id")
        if self.version < 1:
            raise ValueError("decision version must be positive")
        _text(self.summary, "decision summary")
        if self.status not in {"accepted", "proposed", "superseded"}:
            raise ValueError("invalid design decision status")
        _aware(self.recorded_at, "decision timestamp")


@dataclass(frozen=True)
class DesignMemory:
    project_type: str
    graphics_choice: str
    data_profile: DataProfile
    principles: Tuple[str, ...] = ()
    references: Tuple[DesignReference, ...] = ()
    assets: Tuple[str, ...] = ()
    screens: Tuple[ScreenInventoryItem, ...] = ()
    decisions: Tuple[DesignDecision, ...] = ()
    updated_at: Optional[datetime] = None

    def __post_init__(self) -> None:
        _text(self.project_type, "project type")
        _text(self.graphics_choice, "graphics choice")
        if not isinstance(self.data_profile, DataProfile):
            raise ValueError("data profile must be yes, no, or unknown")
        if self.updated_at is None:
            raise ValueError("updated timestamp is required")
        _aware(self.updated_at, "updated timestamp")
        for principle in self.principles:
            _text(principle, "design principle")
        for asset in self.assets:
            value = _safe_external(asset)
            if value.startswith("/") or ".." in value.split("/"):
                raise ValueError("design asset must be project-relative")
        if len({item.reference_id for item in self.references}) != len(self.references):
            raise ValueError("duplicate design reference id")
        if len({item.screen_id for item in self.screens}) != len(self.screens):
            raise ValueError("duplicate screen id")
        versions: Dict[str, int] = {}
        for decision in self.decisions:
            previous = versions.get(decision.decision_id, 0)
            if decision.version <= previous:
                raise ValueError("decision versions must increase chronologically")
            versions[decision.decision_id] = decision.version

    def to_dict(self) -> Dict[str, object]:
        return {
            "project_type": self.project_type,
            "graphics_choice": self.graphics_choice,
            "data_profile": self.data_profile.value,
            "principles": list(self.principles),
            "references": [asdict(item) for item in self.references],
            "assets": list(self.assets),
            "screens": [
                {"screen_id": item.screen_id, "route": item.route, "states": list(item.states)}
                for item in self.screens
            ],
            "decisions": [
                {
                    **asdict(item),
                    "recorded_at": item.recorded_at.isoformat(),
                }
                for item in self.decisions
            ],
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> DesignMemory:
        try:
            references = tuple(
                DesignReference(
                    reference_id=str(item["reference_id"]),
                    kind=str(item["kind"]),
                    label=str(item["label"]),
                    locator=str(item["locator"]),
                    version=None if item["version"] is None else str(item["version"]),
                )
                for item in (
                    _mapping(raw, "design reference")
                    for raw in _list(value["references"], "references")
                )
            )
            screens = tuple(
                ScreenInventoryItem(
                    screen_id=str(item["screen_id"]),
                    route=str(item["route"]),
                    states=tuple(str(state) for state in _list(item["states"], "screen states")),
                )
                for item in (
                    _mapping(raw, "screen") for raw in _list(value["screens"], "screens")
                )
            )
            decisions = tuple(
                DesignDecision(
                    decision_id=str(item["decision_id"]),
                    version=int(str(item["version"])),
                    summary=str(item["summary"]),
                    status=str(item["status"]),
                    recorded_at=datetime.fromisoformat(str(item["recorded_at"])),
                )
                for item in (
                    _mapping(raw, "decision")
                    for raw in _list(value["decisions"], "decisions")
                )
            )
            updated = datetime.fromisoformat(str(value["updated_at"]))
            return cls(
                project_type=str(value["project_type"]),
                graphics_choice=str(value["graphics_choice"]),
                data_profile=DataProfile(str(value["data_profile"])),
                principles=tuple(str(item) for item in _list(value["principles"], "principles")),
                references=references,
                assets=tuple(str(item) for item in _list(value["assets"], "assets")),
                screens=screens,
                decisions=decisions,
                updated_at=updated,
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("invalid design memory") from exc

    def to_html(self) -> str:
        principles = "".join(f"<li>{html.escape(item)}</li>" for item in self.principles)
        references = "".join(
            f'<li data-kind="{html.escape(item.kind)}"><a href="{html.escape(item.locator, quote=True)}">'
            f"{html.escape(item.label)}</a></li>"
            for item in self.references
        )
        screens = "".join(
            f"<tr><th scope=\"row\">{html.escape(item.screen_id)}</th>"
            f"<td>{html.escape(item.route)}</td><td>{html.escape(', '.join(item.states))}</td></tr>"
            for item in self.screens
        )
        decisions = "".join(
            f"<li><strong>{html.escape(item.summary)}</strong> "
            f"(v{item.version}, {html.escape(item.status)})</li>"
            for item in self.decisions
        )
        structured = {
            "@context": "https://schema.org",
            "@type": "DigitalDocument",
            "additionalType": "ProjectDesignMemory",
            "dateModified": self.updated_at.isoformat() if self.updated_at else None,
            "about": self.project_type,
        }
        return (
            '<article class="design-memory"><header><h1>Project design memory</h1>'
            f"<p>Project type: {html.escape(self.project_type)}</p>"
            f"<p>Graphics direction: {html.escape(self.graphics_choice)}</p></header>"
            f"<section><h2>Principles</h2><ul>{principles}</ul></section>"
            f"<section><h2>References</h2><ul>{references}</ul></section>"
            f"<section><h2>Screen inventory</h2><table><thead><tr><th>Screen</th><th>Route</th>"
            f"<th>States</th></tr></thead><tbody>{screens}</tbody></table></section>"
            f"<section><h2>Decisions</h2><ol>{decisions}</ol></section>"
            '<script type="application/ld+json">'
            f"{html.escape(json.dumps(structured, sort_keys=True), quote=False)}</script></article>"
        )


class EvidenceDisposition(str, Enum):
    PROVIDED = "provided"
    NOT_APPLICABLE = "not-applicable"


@dataclass(frozen=True)
class ScreenshotEvidence:
    evidence_id: str
    route: str
    screen_id: str
    state: str
    viewport: str
    commit_revision: str
    content_sha256: str
    baseline_sha256: Optional[str]
    variance_percent: Optional[float]
    variance_limit_percent: Optional[float]
    captured_at: datetime
    reviewed: bool
    synthetic: bool
    baseline_proposal_supervision_id: Optional[str] = None


@dataclass(frozen=True)
class DesignEvidence:
    slice_id: str
    material_frontend: bool
    commit_revision: str
    screenshots: Tuple[ScreenshotEvidence, ...]
    accessibility_status: str
    accessibility_evidence_sha256: Optional[str]
    disposition: EvidenceDisposition
    disposition_reason: Optional[str]
    reviewed_at: datetime


@dataclass(frozen=True)
class DesignGateResult:
    accepted: bool
    findings: Tuple[str, ...]

    @property
    def can_complete(self) -> bool:
        return self.accepted


class DesignEvidenceGate:
    """Admit commit-bound visual proof before frontend slice completion."""

    def evaluate(
        self, memory: DesignMemory, evidence: DesignEvidence, *, expected_commit: str
    ) -> DesignGateResult:
        findings: list[str] = []
        _aware(evidence.reviewed_at, "evidence review timestamp")
        if evidence.commit_revision != expected_commit:
            findings.append("stale commit in design evidence")
        if not evidence.material_frontend:
            if evidence.disposition is not EvidenceDisposition.NOT_APPLICABLE:
                findings.append("non-frontend work requires an explicit disposition")
            if not evidence.disposition_reason or not evidence.disposition_reason.strip():
                findings.append("not-applicable disposition requires justification")
            return DesignGateResult(not findings, tuple(findings))
        if evidence.disposition is not EvidenceDisposition.PROVIDED:
            findings.append("material frontend work requires provided design evidence")
        present_viewports: set[str] = set()
        for shot in evidence.screenshots:
            present_viewports.add(shot.viewport)
            self._check_screenshot(memory, shot, expected_commit, findings)
        for viewport in ("desktop", "mobile"):
            if viewport not in present_viewports:
                findings.append(f"required viewport missing: {viewport}")
        if evidence.accessibility_status != "passed":
            findings.append("accessibility validation did not pass")
        if not evidence.accessibility_evidence_sha256 or not _HASH.fullmatch(
            evidence.accessibility_evidence_sha256
        ):
            findings.append("invalid accessibility evidence hash")
        return DesignGateResult(not findings, tuple(findings))

    @staticmethod
    def _check_screenshot(
        memory: DesignMemory,
        shot: ScreenshotEvidence,
        expected_commit: str,
        findings: list[str],
    ) -> None:
        if shot.viewport not in _VIEWPORTS:
            findings.append(f"unsupported viewport: {shot.viewport}")
        if shot.commit_revision != expected_commit:
            findings.append(f"stale commit for screenshot: {shot.evidence_id}")
        if not _HASH.fullmatch(shot.content_sha256):
            findings.append(f"invalid screenshot hash: {shot.evidence_id}")
        if shot.baseline_sha256 is not None and not _HASH.fullmatch(shot.baseline_sha256):
            findings.append(f"invalid baseline hash: {shot.evidence_id}")
        if shot.baseline_sha256 is None and not shot.baseline_proposal_supervision_id:
            findings.append(f"baseline missing without supervised proposal: {shot.evidence_id}")
        if not shot.reviewed:
            findings.append(f"screenshot not reviewed: {shot.evidence_id}")
        if memory.data_profile in {DataProfile.YES, DataProfile.UNKNOWN} and not shot.synthetic:
            findings.append("PHI posture requires synthetic screenshot evidence")
        if shot.variance_percent is not None:
            if shot.variance_limit_percent is None:
                findings.append(f"variance limit missing: {shot.evidence_id}")
            elif shot.variance_percent > shot.variance_limit_percent:
                findings.append(f"variance exceeds limit: {shot.evidence_id}")
        known = {item.screen_id: item for item in memory.screens}
        screen = known.get(shot.screen_id)
        if screen is None or screen.route != shot.route or shot.state not in screen.states:
            findings.append(f"screenshot does not match screen inventory: {shot.evidence_id}")
