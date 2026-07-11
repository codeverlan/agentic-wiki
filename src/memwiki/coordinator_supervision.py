from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass, replace
from datetime import datetime
from enum import Enum
from typing import Dict, Mapping, Optional, Sequence, Tuple


class SupervisionCategory(str, Enum):
    SAFETY = "safety"
    LEGAL = "legal"
    IRREVERSIBLE = "irreversible"
    DESTRUCTIVE = "destructive"
    FINANCIAL = "financial"
    EXTERNAL_PUBLICATION = "external_publication"
    AUTHORIZATION = "authorization"
    ADVISORY = "advisory"


class SupervisionStatus(str, Enum):
    OPEN = "open"
    DEFERRED = "deferred"
    RESOLVED = "resolved"


_PRIORITY = {category: index for index, category in enumerate(SupervisionCategory)}
_SECRET_PATTERN = re.compile(
    r"(?i)(?:api[_-]?key|password|passwd|secret|access[_-]?token|bearer)\s*[:=]\s*\S+"
)


@dataclass(frozen=True)
class SupervisionEvent:
    action: str
    recorded_at: datetime
    reason: Optional[str] = None
    disposition: Optional[str] = None
    evidence_ids: Tuple[str, ...] = ()
    resume_after: Optional[datetime] = None


@dataclass(frozen=True)
class SupervisionRecord:
    item_id: str
    category: SupervisionCategory
    summary: str
    choices: Tuple[str, ...]
    evidence_ids: Tuple[str, ...]
    affected_slice_ids: Tuple[str, ...]
    resumable: bool
    created_at: datetime
    expires_at: Optional[datetime]
    status: SupervisionStatus
    resume_after: Optional[datetime]
    disposition: Optional[str]
    history: Tuple[SupervisionEvent, ...]
    expired: bool = False

    @property
    def mandatory(self) -> bool:
        return self.category is not SupervisionCategory.ADVISORY


@dataclass(frozen=True)
class QueueEvent:
    action: str
    recorded_at: datetime
    active_slice_ids: Tuple[str, ...] = ()
    item_ids: Tuple[str, ...] = ()
    cancel_active_work: bool = False


class SupervisionQueue:
    """Chronological supervision truth with a separate priority presentation view."""

    def __init__(
        self,
        records: Optional[Mapping[str, SupervisionRecord]] = None,
        queue_events: Sequence[QueueEvent] = (),
    ) -> None:
        self._records = dict(records or {})
        self._queue_events = tuple(queue_events)

    @classmethod
    def empty(cls) -> "SupervisionQueue":
        return cls()

    def get(self, item_id: str) -> SupervisionRecord:
        try:
            return self._records[item_id]
        except KeyError:
            raise ValueError(f"unknown supervision item: {item_id}") from None

    def chronological(self) -> Tuple[SupervisionRecord, ...]:
        return tuple(sorted(self._records.values(), key=lambda item: (item.created_at, item.item_id)))

    @property
    def presentation_due(self) -> bool:
        last_return = _last_event_index(self._queue_events, "user_returned")
        last_presented = _last_event_index(self._queue_events, "presented")
        return last_return >= 0 and last_return > last_presented

    def open(
        self,
        *,
        item_id: str,
        category: SupervisionCategory,
        summary: str,
        choices: Sequence[str],
        evidence_ids: Sequence[str],
        affected_slice_ids: Sequence[str],
        resumable: bool,
        created_at: datetime,
        expires_at: Optional[datetime] = None,
    ) -> SupervisionRecord:
        _safe_text(item_id, "item id")
        if item_id in self._records:
            raise ValueError(f"duplicate supervision item: {item_id}")
        _aware(created_at)
        if expires_at is not None:
            _aware(expires_at)
            if expires_at <= created_at:
                raise ValueError("expiry must be after creation")
        summary = _safe_text(summary, "summary")
        normalized_choices = _safe_values(choices, "choice", required=True)
        evidence = _safe_values(evidence_ids, "evidence id")
        affected = _safe_values(affected_slice_ids, "affected slice id")
        event = SupervisionEvent("opened", created_at, evidence_ids=evidence)
        record = SupervisionRecord(
            item_id=item_id,
            category=category,
            summary=summary,
            choices=normalized_choices,
            evidence_ids=evidence,
            affected_slice_ids=affected,
            resumable=resumable,
            created_at=created_at,
            expires_at=expires_at,
            status=SupervisionStatus.OPEN,
            resume_after=None,
            disposition=None,
            history=(event,),
        )
        self._records[item_id] = record
        return record

    def defer(
        self,
        item_id: str,
        *,
        reason: str,
        resume_after: datetime,
        recorded_at: datetime,
    ) -> SupervisionRecord:
        record = self._actionable(item_id, recorded_at)
        _aware(resume_after)
        if resume_after <= recorded_at:
            raise ValueError("resume time must be after deferral")
        event = SupervisionEvent(
            "deferred",
            recorded_at,
            reason=_safe_text(reason, "reason"),
            resume_after=resume_after,
        )
        updated = replace(
            record,
            status=SupervisionStatus.DEFERRED,
            resume_after=resume_after,
            history=record.history + (event,),
        )
        self._records[item_id] = updated
        return updated

    def resolve(
        self,
        item_id: str,
        *,
        disposition: str,
        evidence_ids: Sequence[str],
        recorded_at: datetime,
    ) -> SupervisionRecord:
        record = self._actionable(item_id, recorded_at)
        if record.expires_at is not None and recorded_at > record.expires_at:
            raise ValueError("expired supervision item must be reopened")
        disposition = _safe_text(disposition, "disposition")
        evidence = _safe_values(evidence_ids, "evidence id")
        event = SupervisionEvent(
            "resolved", recorded_at, disposition=disposition, evidence_ids=evidence
        )
        updated = replace(
            record,
            status=SupervisionStatus.RESOLVED,
            resume_after=None,
            disposition=disposition,
            history=record.history + (event,),
        )
        self._records[item_id] = updated
        return updated

    def pending_for_presentation(self, at: datetime) -> Tuple[SupervisionRecord, ...]:
        _aware(at)
        pending = []
        for record in self._records.values():
            if record.status is SupervisionStatus.RESOLVED:
                continue
            if (
                record.status is SupervisionStatus.DEFERRED
                and record.resume_after is not None
                and at < record.resume_after
            ):
                continue
            expired = record.expires_at is not None and at > record.expires_at
            pending.append(replace(record, expired=expired))
        return tuple(
            sorted(
                pending,
                key=lambda item: (_PRIORITY[item.category], item.created_at, item.item_id),
            )
        )

    def record_user_return(
        self, *, recorded_at: datetime, active_slice_ids: Sequence[str]
    ) -> QueueEvent:
        event = QueueEvent(
            action="user_returned",
            recorded_at=recorded_at,
            active_slice_ids=_safe_values(active_slice_ids, "active slice id"),
            cancel_active_work=False,
        )
        self._append_queue_event(event)
        return event

    def mark_presented(self, *, recorded_at: datetime, item_ids: Sequence[str]) -> QueueEvent:
        ids = _safe_values(item_ids, "item id")
        for item_id in ids:
            self.get(item_id)
        event = QueueEvent("presented", recorded_at, item_ids=ids)
        self._append_queue_event(event)
        return event

    def to_dict(self) -> Dict[str, object]:
        return {
            "items": [_record_to_dict(record) for record in self.chronological()],
            "queue_events": [_queue_event_to_dict(event) for event in self._queue_events],
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "SupervisionQueue":
        if set(value) != {"items", "queue_events"}:
            raise ValueError("invalid supervision queue fields")
        items = value["items"]
        events = value["queue_events"]
        if not isinstance(items, list) or not isinstance(events, list):
            raise ValueError("supervision queue collections must be lists")
        queue = cls.empty()
        for raw in items:
            record = _record_from_dict(_mapping(raw, "supervision item"))
            if record.item_id in queue._records:
                raise ValueError(f"duplicate supervision item: {record.item_id}")
            queue._records[record.item_id] = record
        for raw in events:
            queue._append_queue_event(_queue_event_from_dict(_mapping(raw, "queue event")))
        return queue

    def render_html(self, *, generated_at: datetime) -> str:
        _aware(generated_at)
        rows = []
        for item in self.pending_for_presentation(generated_at):
            scope = ", ".join(item.affected_slice_ids) or "All applicable work"
            evidence = ", ".join(item.evidence_ids) or "None"
            status = "expired" if item.expired else item.status.value
            rows.append(
                "<tr>"
                f"<td>{html.escape(item.item_id)}</td>"
                f"<td>{html.escape(item.category.value.replace('_', ' '))}</td>"
                f"<td>{html.escape(item.summary)}</td>"
                f"<td>{html.escape(scope)}</td>"
                f"<td>{html.escape(evidence)}</td>"
                f"<td>{html.escape(status)}</td>"
                "</tr>"
            )
        body = "".join(rows) or '<tr><td colspan="6">No pending supervision.</td></tr>'
        structured = json.dumps(
            {
                "@context": "https://schema.org",
                "@type": "ItemList",
                "name": "Supervision queue",
                "dateModified": _stamp(generated_at),
                "numberOfItems": len(rows),
            },
            sort_keys=True,
            separators=(",", ":"),
        ).replace("<", "\\u003c")
        return (
            "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
            "<title>Supervision queue</title>"
            f'<script type="application/ld+json">{structured}</script></head><body>'
            "<main><h1>Supervision queue</h1>"
            "<table><thead><tr><th>ID</th><th>Priority</th><th>Decision</th>"
            "<th>Affected scope</th><th>Evidence</th><th>Status</th></tr></thead>"
            f"<tbody>{body}</tbody></table></main></body></html>"
        )

    def _actionable(self, item_id: str, recorded_at: datetime) -> SupervisionRecord:
        _aware(recorded_at)
        record = self.get(item_id)
        if record.status not in {SupervisionStatus.OPEN, SupervisionStatus.DEFERRED}:
            raise ValueError("supervision item must be open or deferred")
        if recorded_at < record.history[-1].recorded_at:
            raise ValueError("supervision history must be chronological")
        return record

    def _append_queue_event(self, event: QueueEvent) -> None:
        _aware(event.recorded_at)
        if self._queue_events and event.recorded_at < self._queue_events[-1].recorded_at:
            raise ValueError("queue history must be chronological")
        self._queue_events += (event,)


def _record_to_dict(record: SupervisionRecord) -> Dict[str, object]:
    return {
        "item_id": record.item_id,
        "category": record.category.value,
        "summary": record.summary,
        "choices": list(record.choices),
        "evidence_ids": list(record.evidence_ids),
        "affected_slice_ids": list(record.affected_slice_ids),
        "resumable": record.resumable,
        "created_at": _stamp(record.created_at),
        "expires_at": _optional_stamp(record.expires_at),
        "status": record.status.value,
        "resume_after": _optional_stamp(record.resume_after),
        "disposition": record.disposition,
        "history": [_event_to_dict(event) for event in record.history],
    }


def _record_from_dict(value: Mapping[str, object]) -> SupervisionRecord:
    expected = {
        "item_id", "category", "summary", "choices", "evidence_ids", "affected_slice_ids",
        "resumable", "created_at", "expires_at", "status", "resume_after", "disposition", "history",
    }
    if set(value) != expected:
        raise ValueError("invalid supervision item fields")
    history = value["history"]
    if not isinstance(history, list) or not history:
        raise ValueError("supervision history must be a non-empty list")
    record = SupervisionRecord(
        item_id=_safe_text(value["item_id"], "item id"),
        category=SupervisionCategory(_safe_text(value["category"], "category")),
        summary=_safe_text(value["summary"], "summary"),
        choices=_safe_values(_sequence(value["choices"], "choices"), "choice", required=True),
        evidence_ids=_safe_values(_sequence(value["evidence_ids"], "evidence ids"), "evidence id"),
        affected_slice_ids=_safe_values(
            _sequence(value["affected_slice_ids"], "affected slice ids"), "affected slice id"
        ),
        resumable=_boolean(value["resumable"], "resumable"),
        created_at=_parse_stamp(value["created_at"]),
        expires_at=_parse_optional_stamp(value["expires_at"]),
        status=SupervisionStatus(_safe_text(value["status"], "status")),
        resume_after=_parse_optional_stamp(value["resume_after"]),
        disposition=_optional_safe_text(value["disposition"], "disposition"),
        history=tuple(_event_from_dict(_mapping(item, "history event")) for item in history),
    )
    if record.history[0].action != "opened" or record.history[0].recorded_at != record.created_at:
        raise ValueError("supervision history must begin with creation")
    _validate_chronology(tuple(event.recorded_at for event in record.history))
    return record


def _event_to_dict(event: SupervisionEvent) -> Dict[str, object]:
    return {
        "action": event.action,
        "recorded_at": _stamp(event.recorded_at),
        "reason": event.reason,
        "disposition": event.disposition,
        "evidence_ids": list(event.evidence_ids),
        "resume_after": _optional_stamp(event.resume_after),
    }


def _event_from_dict(value: Mapping[str, object]) -> SupervisionEvent:
    expected = {"action", "recorded_at", "reason", "disposition", "evidence_ids", "resume_after"}
    if set(value) != expected:
        raise ValueError("invalid supervision event fields")
    return SupervisionEvent(
        action=_safe_text(value["action"], "action"),
        recorded_at=_parse_stamp(value["recorded_at"]),
        reason=_optional_safe_text(value["reason"], "reason"),
        disposition=_optional_safe_text(value["disposition"], "disposition"),
        evidence_ids=_safe_values(_sequence(value["evidence_ids"], "evidence ids"), "evidence id"),
        resume_after=_parse_optional_stamp(value["resume_after"]),
    )


def _queue_event_to_dict(event: QueueEvent) -> Dict[str, object]:
    return {
        "action": event.action,
        "recorded_at": _stamp(event.recorded_at),
        "active_slice_ids": list(event.active_slice_ids),
        "item_ids": list(event.item_ids),
        "cancel_active_work": event.cancel_active_work,
    }


def _queue_event_from_dict(value: Mapping[str, object]) -> QueueEvent:
    expected = {"action", "recorded_at", "active_slice_ids", "item_ids", "cancel_active_work"}
    if set(value) != expected:
        raise ValueError("invalid queue event fields")
    action = _safe_text(value["action"], "action")
    if action not in {"user_returned", "presented"}:
        raise ValueError("unknown queue event action")
    cancel = _boolean(value["cancel_active_work"], "cancel active work")
    if action == "user_returned" and cancel:
        raise ValueError("user return cannot cancel active work")
    return QueueEvent(
        action=action,
        recorded_at=_parse_stamp(value["recorded_at"]),
        active_slice_ids=_safe_values(
            _sequence(value["active_slice_ids"], "active slice ids"), "active slice id"
        ),
        item_ids=_safe_values(_sequence(value["item_ids"], "item ids"), "item id"),
        cancel_active_work=cancel,
    )


def _last_event_index(events: Sequence[QueueEvent], action: str) -> int:
    return max((index for index, event in enumerate(events) if event.action == action), default=-1)


def _safe_text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be non-empty text")
    text = value.strip()
    if _SECRET_PATTERN.search(text):
        raise ValueError(f"{label} appears to contain a secret")
    return text


def _optional_safe_text(value: object, label: str) -> Optional[str]:
    if value is None:
        return None
    return _safe_text(value, label)


def _safe_values(values: Sequence[object], label: str, *, required: bool = False) -> Tuple[str, ...]:
    normalized = tuple(_safe_text(value, label) for value in values)
    if required and not normalized:
        raise ValueError(f"at least one {label} is required")
    if len(set(normalized)) != len(normalized):
        raise ValueError(f"duplicate {label}")
    return normalized


def _aware(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must include a timezone")


def _stamp(value: datetime) -> str:
    _aware(value)
    return value.isoformat()


def _optional_stamp(value: Optional[datetime]) -> Optional[str]:
    return None if value is None else _stamp(value)


def _parse_stamp(value: object) -> datetime:
    text = _safe_text(value, "timestamp")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        raise ValueError("invalid timestamp") from None
    _aware(parsed)
    return parsed


def _parse_optional_stamp(value: object) -> Optional[datetime]:
    return None if value is None else _parse_stamp(value)


def _mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ValueError(f"{label} must be an object")
    return value


def _sequence(value: object, label: str) -> Sequence[object]:
    if not isinstance(value, list):
        raise ValueError(f"{label} must be a list")
    return value


def _boolean(value: object, label: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{label} must be boolean")
    return value


def _validate_chronology(values: Sequence[datetime]) -> None:
    if any(later < earlier for earlier, later in zip(values, values[1:])):
        raise ValueError("supervision history must be chronological")
