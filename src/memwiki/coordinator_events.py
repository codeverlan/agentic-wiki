from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional


class EventChainError(ValueError):
    """Raised when a coordinator event stream fails integrity verification."""


def _canonical_json(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError("event data must be JSON serializable") from exc


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def _required_string(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value


def _optional_string(value: object, field: str) -> Optional[str]:
    if value is None:
        return None
    return _required_string(value, field)


def _timestamp(value: object) -> str:
    text = _required_string(value, "occurred_at")
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError("occurred_at must be an ISO-8601 timestamp") from None
    if parsed.tzinfo is None:
        raise ValueError("occurred_at must be an ISO-8601 timestamp with an offset")
    return text


@dataclass(frozen=True)
class CoordinatorEvent:
    schema_version: int
    run_id: str
    sequence: int
    event_id: str
    event_type: str
    occurred_at: str
    idempotency_key: str
    correlation_id: Optional[str]
    causation_id: Optional[str]
    actor: Dict[str, Any]
    projection_revision: int
    prior_hash: Optional[str]
    payload: Dict[str, Any]
    payload_hash: str
    event_hash: str

    @classmethod
    def create(
        cls,
        *,
        run_id: str,
        sequence: int,
        projection_revision: int,
        event_type: str,
        payload: Dict[str, Any],
        actor: Dict[str, Any],
        idempotency_key: str,
        prior_hash: Optional[str],
        event_id: Optional[str] = None,
        occurred_at: Optional[str] = None,
        correlation_id: Optional[str] = None,
        causation_id: Optional[str] = None,
    ) -> "CoordinatorEvent":
        if sequence < 1 or projection_revision < 1:
            raise ValueError("sequence and projection_revision must be positive integers")
        timestamp = _timestamp(occurred_at or datetime.now(timezone.utc).isoformat())
        normalized_payload = json.loads(_canonical_json(payload))
        normalized_actor = json.loads(_canonical_json(actor))
        payload_hash = _digest(normalized_payload)
        unsigned = {
            "schema_version": 1,
            "run_id": _required_string(run_id, "run_id"),
            "sequence": sequence,
            "event_id": _required_string(event_id or f"{run_id}:{sequence}", "event_id"),
            "event_type": _required_string(event_type, "event_type"),
            "occurred_at": timestamp,
            "idempotency_key": _required_string(idempotency_key, "idempotency_key"),
            "correlation_id": _optional_string(correlation_id, "correlation_id"),
            "causation_id": _optional_string(causation_id, "causation_id"),
            "actor": normalized_actor,
            "projection_revision": projection_revision,
            "prior_hash": _optional_string(prior_hash, "prior_hash"),
            "payload": normalized_payload,
            "payload_hash": payload_hash,
        }
        return cls(**unsigned, event_hash=_digest(unsigned))

    @classmethod
    def from_dict(cls, payload: object) -> "CoordinatorEvent":
        if not isinstance(payload, dict):
            raise ValueError("coordinator event must be an object")
        fields = {field.name for field in cls.__dataclass_fields__.values()}
        if set(payload) != fields:
            raise ValueError("coordinator event fields do not match schema")
        event = cls(**payload)
        _validate_event_shape(event)
        return event

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def unsigned_dict(self) -> Dict[str, Any]:
        value = self.to_dict()
        del value["event_hash"]
        return value


def _validate_event_shape(event: CoordinatorEvent) -> None:
    if event.schema_version != 1:
        raise ValueError("unsupported coordinator event schema_version")
    if not isinstance(event.sequence, int) or event.sequence < 1:
        raise ValueError("event sequence must be a positive integer")
    if not isinstance(event.projection_revision, int) or event.projection_revision < 1:
        raise ValueError("projection_revision must be a positive integer")
    for field in ["run_id", "event_id", "event_type", "idempotency_key", "payload_hash", "event_hash"]:
        _required_string(getattr(event, field), field)
    _timestamp(event.occurred_at)
    _canonical_json(event.actor)
    _canonical_json(event.payload)


def verify_event_chain(events: Iterable[CoordinatorEvent], *, run_id: Optional[str] = None) -> None:
    materialized: List[CoordinatorEvent] = list(events)
    prior_hash: Optional[str] = None
    event_ids = set()
    idempotency_keys = set()
    for expected_sequence, event in enumerate(materialized, start=1):
        _validate_event_shape(event)
        if run_id is not None and event.run_id != run_id:
            raise EventChainError("event run_id does not match requested run")
        if event.sequence != expected_sequence or event.projection_revision != expected_sequence:
            raise EventChainError("event sequence or projection revision is not monotonic")
        if event.event_id in event_ids or event.idempotency_key in idempotency_keys:
            raise EventChainError("duplicate event or idempotency key")
        if event.prior_hash != prior_hash:
            raise EventChainError("event prior hash does not match chain")
        if _digest(event.payload) != event.payload_hash:
            raise EventChainError("event payload hash does not match payload")
        if _digest(event.unsigned_dict()) != event.event_hash:
            raise EventChainError("event hash does not match envelope")
        event_ids.add(event.event_id)
        idempotency_keys.add(event.idempotency_key)
        prior_hash = event.event_hash
