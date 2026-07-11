from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any, Callable, Dict, Mapping, Optional, Sequence, Tuple, cast

LEVELS = ("debug", "info", "warning", "error", "critical")
CATEGORIES = (
    "coordinator",
    "scheduler",
    "worker",
    "command",
    "capability",
    "validation",
    "integration",
    "resource",
    "security",
    "recovery",
)
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,255}$")
_SECRET_KEY = re.compile(r"(?i)(password|passwd|secret|api[_-]?key|access[_-]?token|authorization|bearer)")
_SECRET_VALUE = re.compile(
    r"(?i)(?:password|passwd|secret|api[_-]?key|access[_-]?token|authorization|bearer)\s*[:=]\s*\S+"
)
_PHI_VALUE = re.compile(r"(?i)\b(?:patient|mrn|medical record|date of birth|dob)\b.{0,80}\b\d{3,}\b")


def _identifier(name: str, value: Optional[str], *, required: bool = False) -> None:
    if value is None and not required:
        return
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise ValueError(f"{name} must be a stable identifier")


def _timestamp(value: datetime) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("occurred_at must be timezone-aware")


def _scan_sensitive(value: Any, *, key: Optional[str] = None) -> None:
    if key is not None and _SECRET_KEY.search(key):
        raise ValueError("observability payload contains sensitive data")
    if isinstance(value, str):
        if _SECRET_VALUE.search(value) or _PHI_VALUE.search(value):
            raise ValueError("observability payload contains sensitive data")
    elif isinstance(value, Mapping):
        for child_key, child in value.items():
            if not isinstance(child_key, str):
                raise ValueError("attribute keys must be strings")
            _scan_sensitive(child, key=child_key)
    elif isinstance(value, (list, tuple)):
        for child in value:
            _scan_sensitive(child)
    elif value is not None and not isinstance(value, (bool, int, float)):
        raise ValueError("attributes must be JSON-compatible")


def _normalized_json(value: Mapping[str, Any]) -> Dict[str, Any]:
    _scan_sensitive(value)
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    if len(encoded.encode("utf-8")) > 64 * 1024:
        raise ValueError("attributes exceed size limit")
    return cast(Dict[str, Any], json.loads(encoded))


@dataclass(frozen=True)
class CorrelationIds:
    run_id: str
    slice_id: Optional[str] = None
    attempt_id: Optional[str] = None
    assignment_id: Optional[str] = None
    lease_id: Optional[str] = None
    worker_id: Optional[str] = None
    command_id: Optional[str] = None
    capability_id: Optional[str] = None
    validation_id: Optional[str] = None
    integration_id: Optional[str] = None
    resource_id: Optional[str] = None
    commit_id: Optional[str] = None
    invocation_id: Optional[str] = None
    incident_id: Optional[str] = None

    def __post_init__(self) -> None:
        for name in self.__dataclass_fields__:
            _identifier(name, getattr(self, name), required=name == "run_id")

    def to_dict(self) -> Dict[str, Optional[str]]:
        return {name: getattr(self, name) for name in self.__dataclass_fields__}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "CorrelationIds":
        payload: Dict[str, Any] = {name: value.get(name) for name in cls.__dataclass_fields__}
        return cls(**payload)


@dataclass(frozen=True)
class ObservabilityEvent:
    event_id: str
    occurred_at: datetime
    level: str
    category: str
    message: str
    correlation: CorrelationIds
    evidence_ids: Tuple[str, ...] = ()
    attributes: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _identifier("event_id", self.event_id, required=True)
        _timestamp(self.occurred_at)
        if self.level not in LEVELS:
            raise ValueError("unknown event level")
        if self.category not in CATEGORIES:
            raise ValueError("unknown event category")
        if not isinstance(self.message, str) or not self.message.strip():
            raise ValueError("message must be non-empty")
        _scan_sensitive(self.message)
        evidence = tuple(self.evidence_ids)
        if len(set(evidence)) != len(evidence):
            raise ValueError("duplicate evidence identifier")
        for evidence_id in evidence:
            _identifier("evidence_id", evidence_id, required=True)
        object.__setattr__(self, "evidence_ids", evidence)
        object.__setattr__(self, "attributes", _normalized_json(self.attributes))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_id": self.event_id,
            "occurred_at": self.occurred_at.isoformat(),
            "level": self.level,
            "category": self.category,
            "message": self.message,
            "correlation": self.correlation.to_dict(),
            "evidence_ids": list(self.evidence_ids),
            "attributes": dict(self.attributes),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ObservabilityEvent":
        return cls(
            event_id=value["event_id"],
            occurred_at=datetime.fromisoformat(value["occurred_at"]),
            level=value["level"],
            category=value["category"],
            message=value["message"],
            correlation=CorrelationIds.from_dict(value["correlation"]),
            evidence_ids=tuple(value["evidence_ids"]),
            attributes=value["attributes"],
        )


@dataclass(frozen=True)
class SecuritySignal:
    signal_id: str
    event_id: str
    kind: str
    severity: str
    summary: str

    def __post_init__(self) -> None:
        _identifier("signal_id", self.signal_id, required=True)
        _identifier("event_id", self.event_id, required=True)
        _identifier("kind", self.kind, required=True)
        if self.severity not in ("info", "warning", "error", "critical"):
            raise ValueError("unknown signal severity")
        if not self.summary.strip():
            raise ValueError("summary must be non-empty")
        _scan_sensitive(self.summary)

    def to_dict(self) -> Dict[str, str]:
        return {
            "signal_id": self.signal_id,
            "event_id": self.event_id,
            "kind": self.kind,
            "severity": self.severity,
            "summary": self.summary,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "SecuritySignal":
        return cls(**value)


SignalHook = Callable[[ObservabilityEvent], Optional[SecuritySignal]]


@dataclass(frozen=True)
class EvidenceIndex:
    events: Tuple[ObservabilityEvent, ...] = ()
    signals: Tuple[SecuritySignal, ...] = ()
    hooks: Tuple[SignalHook, ...] = field(default=(), compare=False, repr=False)

    @classmethod
    def empty(cls, *, hooks: Sequence[SignalHook] = ()) -> "EvidenceIndex":
        return cls(hooks=tuple(hooks))

    @property
    def evidence_to_events(self) -> Dict[str, Tuple[str, ...]]:
        result: Dict[str, list[str]] = {}
        for item in self.events:
            for evidence_id in item.evidence_ids:
                result.setdefault(evidence_id, []).append(item.event_id)
        return {key: tuple(value) for key, value in sorted(result.items())}

    def append(self, item: ObservabilityEvent) -> "EvidenceIndex":
        existing = next((event for event in self.events if event.event_id == item.event_id), None)
        if existing is not None:
            if existing != item:
                raise ValueError("conflicting event identity")
            return self
        if self.events and item.occurred_at < self.events[-1].occurred_at:
            raise ValueError("events must be appended in chronological order")
        generated = tuple(signal for hook in self.hooks if (signal := hook(item)) is not None)
        for signal in generated:
            if signal.event_id != item.event_id:
                raise ValueError("security signal must reference the appended event")
        signal_ids = [signal.signal_id for signal in self.signals + generated]
        if len(signal_ids) != len(set(signal_ids)):
            raise ValueError("duplicate security signal identity")
        return replace(self, events=self.events + (item,), signals=self.signals + generated)

    def query(
        self,
        *,
        run_id: Optional[str] = None,
        slice_id: Optional[str] = None,
        attempt_id: Optional[str] = None,
        assignment_id: Optional[str] = None,
        lease_id: Optional[str] = None,
        command_id: Optional[str] = None,
        capability_id: Optional[str] = None,
        validation_id: Optional[str] = None,
        integration_id: Optional[str] = None,
        resource_id: Optional[str] = None,
        levels: Sequence[str] = (),
        categories: Sequence[str] = (),
        evidence_id: Optional[str] = None,
    ) -> Tuple[ObservabilityEvent, ...]:
        return tuple(
            item
            for item in self.events
            if (run_id is None or item.correlation.run_id == run_id)
            and (slice_id is None or item.correlation.slice_id == slice_id)
            and (attempt_id is None or item.correlation.attempt_id == attempt_id)
            and (assignment_id is None or item.correlation.assignment_id == assignment_id)
            and (lease_id is None or item.correlation.lease_id == lease_id)
            and (command_id is None or item.correlation.command_id == command_id)
            and (capability_id is None or item.correlation.capability_id == capability_id)
            and (validation_id is None or item.correlation.validation_id == validation_id)
            and (integration_id is None or item.correlation.integration_id == integration_id)
            and (resource_id is None or item.correlation.resource_id == resource_id)
            and (not levels or item.level in levels)
            and (not categories or item.category in categories)
            and (evidence_id is None or evidence_id in item.evidence_ids)
        )

    def require_evidence(self, event_id: str) -> Tuple[str, ...]:
        item = next((event for event in self.events if event.event_id == event_id), None)
        if item is None:
            raise ValueError("unknown event")
        if not item.evidence_ids:
            raise ValueError("event has no evidence")
        return item.evidence_ids

    def to_dict(self) -> Dict[str, Any]:
        return {
            "events": [item.to_dict() for item in self.events],
            "signals": [item.to_dict() for item in self.signals],
        }

    @classmethod
    def from_dict(
        cls, value: Mapping[str, Any], *, hooks: Sequence[SignalHook] = ()
    ) -> "EvidenceIndex":
        index = cls(
            events=tuple(ObservabilityEvent.from_dict(item) for item in value["events"]),
            signals=tuple(SecuritySignal.from_dict(item) for item in value["signals"]),
            hooks=tuple(hooks),
        )
        index._validate_history()
        return index

    def _validate_history(self) -> None:
        event_ids = [item.event_id for item in self.events]
        if len(event_ids) != len(set(event_ids)):
            raise ValueError("duplicate event identity")
        if tuple(sorted(self.events, key=lambda item: item.occurred_at)) != self.events:
            raise ValueError("events are not chronological")
        known = set(event_ids)
        signal_ids = [item.signal_id for item in self.signals]
        if len(signal_ids) != len(set(signal_ids)):
            raise ValueError("duplicate security signal identity")
        if any(item.event_id not in known for item in self.signals):
            raise ValueError("security signal references unknown event")

    def to_html(self, *, title: str = "Coordinator observability") -> str:
        _scan_sensitive(title)
        json_ld = json.dumps(
            {
                "@context": "https://schema.org",
                "@type": "Dataset",
                "name": title,
                "numberOfItems": len(self.events),
            },
            sort_keys=True,
        ).replace("</", "<\\/")
        rows = "".join(
            "<tr>"
            f"<td><code>{html.escape(item.event_id)}</code></td>"
            f"<td><time datetime=\"{html.escape(item.occurred_at.isoformat())}\">"
            f"{html.escape(item.occurred_at.isoformat())}</time></td>"
            f"<td>{html.escape(item.level)}</td><td>{html.escape(item.category)}</td>"
            f"<td>{html.escape(item.message)}</td>"
            f"<td><code>{html.escape(json.dumps(item.attributes, sort_keys=True))}</code></td>"
            "</tr>"
            for item in self.events
        )
        return (
            "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
            f"<title>{html.escape(title)}</title><script type=\"application/ld+json\">{json_ld}</script>"
            "</head><body><main><h1>" + html.escape(title) + "</h1>"
            "<table><caption>Append-only coordinator evidence events</caption>"
            "<thead><tr><th>ID</th><th>Time</th><th>Level</th><th>Category</th>"
            "<th>Message</th><th>Attributes</th></tr></thead><tbody>"
            + rows
            + "</tbody></table></main></body></html>"
        )
