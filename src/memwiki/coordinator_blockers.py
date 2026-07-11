from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from enum import Enum
from typing import Dict, Mapping, Optional, Sequence, Set, Tuple


class BlockerType(str, Enum):
    USER_INPUT = "user_input"
    MISSING_EXPECTED_CAPABILITY = "missing_expected_capability"
    FUTURE_RESOURCE_ADVISORY = "future_resource_advisory"
    TECHNICAL_RETRYABLE = "technical_retryable"
    POLICY_SUPERVISION = "policy_supervision"


class BlockerScope(str, Enum):
    SLICE = "slice"
    GLOBAL = "global"
    ADVISORY = "advisory"


class BlockerStatus(str, Enum):
    OPEN = "open"
    RESOLVED = "resolved"
    SUPERSEDED = "superseded"


@dataclass(frozen=True)
class BlockerEvent:
    action: str
    recorded_at: datetime
    cause: Optional[str]
    evidence_ids: Tuple[str, ...]
    resolution: Optional[str] = None
    superseded_by: Optional[str] = None


@dataclass(frozen=True)
class BlockerRecord:
    blocker_id: str
    blocker_type: BlockerType
    cause: str
    source_slice_id: Optional[str]
    scope: BlockerScope
    status: BlockerStatus
    authority_need: Optional[str]
    notification_metadata: Dict[str, str]
    resolution: Optional[str]
    superseded_by: Optional[str]
    history: Tuple[BlockerEvent, ...]

    @property
    def notify_user(self) -> bool:
        return self.blocker_type is BlockerType.MISSING_EXPECTED_CAPABILITY

    @property
    def is_advisory(self) -> bool:
        return self.blocker_type is BlockerType.FUTURE_RESOURCE_ADVISORY


@dataclass(frozen=True)
class BlockerImpact:
    blocked_slice_ids: Tuple[str, ...]
    schedulable_slice_ids: Tuple[str, ...]
    advisory_ids: Tuple[str, ...]
    active_blocker_ids: Tuple[str, ...]


class BlockerLedger:
    """Typed blocker lifecycle with dependency-scoped scheduling impact."""

    def __init__(self, records: Optional[Mapping[str, BlockerRecord]] = None) -> None:
        self._records = dict(records or {})

    @classmethod
    def empty(cls) -> "BlockerLedger":
        return cls()

    def get(self, blocker_id: str) -> BlockerRecord:
        try:
            return self._records[blocker_id]
        except KeyError:
            raise ValueError(f"unknown blocker: {blocker_id}") from None

    def open(
        self,
        *,
        blocker_id: str,
        blocker_type: BlockerType,
        cause: str,
        source_slice_id: Optional[str],
        scope: BlockerScope,
        authority_need: Optional[str],
        evidence_ids: Sequence[str],
        recorded_at: datetime,
        notification_metadata: Optional[Mapping[str, str]] = None,
    ) -> BlockerRecord:
        _text(blocker_id, "blocker id")
        if blocker_id in self._records:
            raise ValueError(f"duplicate blocker id: {blocker_id}")
        _text(cause, "cause")
        _aware(recorded_at)
        evidence = _evidence(evidence_ids)
        metadata = _metadata(notification_metadata)
        self._validate_shape(blocker_type, scope, source_slice_id, metadata)
        record = BlockerRecord(
            blocker_id=blocker_id,
            blocker_type=blocker_type,
            cause=cause,
            source_slice_id=source_slice_id,
            scope=scope,
            status=BlockerStatus.OPEN,
            authority_need=_optional_text(authority_need, "authority need"),
            notification_metadata=metadata,
            resolution=None,
            superseded_by=None,
            history=(BlockerEvent("opened", recorded_at, cause, evidence),),
        )
        self._records[blocker_id] = record
        return record

    def update(
        self,
        blocker_id: str,
        *,
        cause: str,
        evidence_ids: Sequence[str],
        recorded_at: datetime,
    ) -> BlockerRecord:
        record = self._require_open(blocker_id)
        _text(cause, "cause")
        event = BlockerEvent("updated", recorded_at, cause, _evidence(evidence_ids))
        return self._store(replace(record, cause=cause, history=self._append(record, event)))

    def resolve(
        self,
        blocker_id: str,
        *,
        resolution: str,
        evidence_ids: Sequence[str],
        recorded_at: datetime,
    ) -> BlockerRecord:
        record = self._require_open(blocker_id)
        _text(resolution, "resolution")
        event = BlockerEvent("resolved", recorded_at, None, _evidence(evidence_ids), resolution=resolution)
        return self._store(
            replace(
                record,
                status=BlockerStatus.RESOLVED,
                resolution=resolution,
                history=self._append(record, event),
            )
        )

    def reopen(
        self,
        blocker_id: str,
        *,
        cause: str,
        evidence_ids: Sequence[str],
        recorded_at: datetime,
    ) -> BlockerRecord:
        record = self.get(blocker_id)
        if record.status is not BlockerStatus.RESOLVED:
            raise ValueError("only a resolved blocker can be reopened")
        _text(cause, "cause")
        event = BlockerEvent("reopened", recorded_at, cause, _evidence(evidence_ids))
        return self._store(
            replace(
                record,
                cause=cause,
                status=BlockerStatus.OPEN,
                resolution=None,
                history=self._append(record, event),
            )
        )

    def supersede(
        self,
        blocker_id: str,
        *,
        superseded_by: str,
        evidence_ids: Sequence[str],
        recorded_at: datetime,
    ) -> BlockerRecord:
        record = self._require_open(blocker_id)
        replacement = self.get(superseded_by)
        if replacement.status is not BlockerStatus.OPEN or superseded_by == blocker_id:
            raise ValueError("replacement blocker must be a different open blocker")
        event = BlockerEvent(
            "superseded", recorded_at, None, _evidence(evidence_ids), superseded_by=superseded_by
        )
        return self._store(
            replace(
                record,
                status=BlockerStatus.SUPERSEDED,
                superseded_by=superseded_by,
                history=self._append(record, event),
            )
        )

    def evaluate_impact(
        self,
        slice_dependencies: Mapping[str, Sequence[str]],
        schedulable_slice_ids: Sequence[str],
    ) -> BlockerImpact:
        dependents = _dependents(slice_dependencies)
        candidates = set(schedulable_slice_ids)
        unknown = candidates - set(slice_dependencies)
        if unknown:
            raise ValueError(f"unknown schedulable slice: {sorted(unknown)[0]}")

        active = [record for record in self._records.values() if record.status is BlockerStatus.OPEN]
        advisory_ids = tuple(sorted(record.blocker_id for record in active if record.is_advisory))
        blockers = [record for record in active if not record.is_advisory]
        blocked: Set[str] = set()
        for record in blockers:
            if record.scope is BlockerScope.GLOBAL:
                blocked.update(candidates)
            else:
                assert record.source_slice_id is not None
                if record.source_slice_id not in slice_dependencies:
                    raise ValueError(f"unknown blocker source slice: {record.source_slice_id}")
                blocked.update(_descendants(record.source_slice_id, dependents) & candidates)
        return BlockerImpact(
            blocked_slice_ids=tuple(sorted(blocked)),
            schedulable_slice_ids=tuple(sorted(candidates - blocked)),
            advisory_ids=advisory_ids,
            active_blocker_ids=tuple(sorted(record.blocker_id for record in blockers)),
        )

    def to_dict(self) -> Dict[str, object]:
        return {
            "blockers": [
                {
                    "blocker_id": record.blocker_id,
                    "blocker_type": record.blocker_type.value,
                    "cause": record.cause,
                    "source_slice_id": record.source_slice_id,
                    "scope": record.scope.value,
                    "status": record.status.value,
                    "authority_need": record.authority_need,
                    "notification_metadata": dict(sorted(record.notification_metadata.items())),
                    "resolution": record.resolution,
                    "superseded_by": record.superseded_by,
                    "history": [
                        {
                            "action": event.action,
                            "recorded_at": event.recorded_at.isoformat(),
                            "cause": event.cause,
                            "evidence_ids": list(event.evidence_ids),
                            "resolution": event.resolution,
                            "superseded_by": event.superseded_by,
                        }
                        for event in record.history
                    ],
                }
                for record in sorted(self._records.values(), key=lambda item: item.blocker_id)
            ]
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "BlockerLedger":
        raw_records = value.get("blockers")
        if not isinstance(raw_records, list):
            raise ValueError("blockers must be a list")
        records: Dict[str, BlockerRecord] = {}
        for raw in raw_records:
            if not isinstance(raw, dict):
                raise ValueError("blocker must be an object")
            history_raw = raw.get("history")
            if not isinstance(history_raw, list):
                raise ValueError("blocker history must be a list")
            history = tuple(_event_from_dict(item) for item in history_raw)
            record = BlockerRecord(
                blocker_id=_required_string(raw, "blocker_id"),
                blocker_type=BlockerType(_required_string(raw, "blocker_type")),
                cause=_required_string(raw, "cause"),
                source_slice_id=_nullable_string(raw.get("source_slice_id"), "source_slice_id"),
                scope=BlockerScope(_required_string(raw, "scope")),
                status=BlockerStatus(_required_string(raw, "status")),
                authority_need=_nullable_string(raw.get("authority_need"), "authority_need"),
                notification_metadata=_metadata(raw.get("notification_metadata")),
                resolution=_nullable_string(raw.get("resolution"), "resolution"),
                superseded_by=_nullable_string(raw.get("superseded_by"), "superseded_by"),
                history=history,
            )
            if record.blocker_id in records:
                raise ValueError(f"duplicate blocker id: {record.blocker_id}")
            cls._validate_shape(record.blocker_type, record.scope, record.source_slice_id, record.notification_metadata)
            _validate_history(record)
            records[record.blocker_id] = record
        return cls(records)

    @staticmethod
    def _validate_shape(
        blocker_type: BlockerType,
        scope: BlockerScope,
        source_slice_id: Optional[str],
        metadata: Mapping[str, str],
    ) -> None:
        if scope is BlockerScope.SLICE and not source_slice_id:
            raise ValueError("slice-scoped blocker requires a source slice")
        if scope is BlockerScope.GLOBAL and source_slice_id is not None:
            raise ValueError("global blocker cannot name a source slice")
        if blocker_type is BlockerType.FUTURE_RESOURCE_ADVISORY and scope is BlockerScope.GLOBAL:
            raise ValueError("future resource advisory cannot have global scope")
        if blocker_type is not BlockerType.FUTURE_RESOURCE_ADVISORY and scope is BlockerScope.ADVISORY:
            raise ValueError("advisory scope requires future resource advisory type")
        if blocker_type is BlockerType.MISSING_EXPECTED_CAPABILITY and metadata.get("channel") != "chat":
            raise ValueError("missing expected capability requires chat notification metadata")

    def _require_open(self, blocker_id: str) -> BlockerRecord:
        record = self.get(blocker_id)
        if record.status is not BlockerStatus.OPEN:
            raise ValueError("blocker is not open")
        return record

    def _append(self, record: BlockerRecord, event: BlockerEvent) -> Tuple[BlockerEvent, ...]:
        _aware(event.recorded_at)
        if event.recorded_at < record.history[-1].recorded_at:
            raise ValueError("blocker evidence must be chronological")
        return record.history + (event,)

    def _store(self, record: BlockerRecord) -> BlockerRecord:
        self._records[record.blocker_id] = record
        return record


def _dependents(dependencies: Mapping[str, Sequence[str]]) -> Dict[str, Set[str]]:
    result: Dict[str, Set[str]] = {slice_id: set() for slice_id in dependencies}
    for slice_id, required in dependencies.items():
        for dependency in required:
            if dependency not in result:
                raise ValueError(f"unknown dependency: {dependency}")
            if dependency == slice_id:
                raise ValueError(f"slice {slice_id} depends on itself")
            result[dependency].add(slice_id)
    _assert_acyclic(dependencies)
    return result


def _assert_acyclic(dependencies: Mapping[str, Sequence[str]]) -> None:
    pending = {key: set(value) for key, value in dependencies.items()}
    while pending:
        ready = {key for key, value in pending.items() if not value}
        if not ready:
            raise ValueError("dependency graph contains a cycle")
        pending = {key: value - ready for key, value in pending.items() if key not in ready}


def _descendants(root: str, dependents: Mapping[str, Set[str]]) -> Set[str]:
    reached = {root}
    pending = [root]
    while pending:
        for child in sorted(dependents[pending.pop(0)]):
            if child not in reached:
                reached.add(child)
                pending.append(child)
    return reached


def _event_from_dict(value: object) -> BlockerEvent:
    if not isinstance(value, dict):
        raise ValueError("blocker event must be an object")
    timestamp = _parse_timestamp(value.get("recorded_at"))
    evidence = value.get("evidence_ids")
    if not isinstance(evidence, list):
        raise ValueError("evidence_ids must be a list")
    return BlockerEvent(
        action=_required_string(value, "action"),
        recorded_at=timestamp,
        cause=_nullable_string(value.get("cause"), "cause"),
        evidence_ids=_evidence(evidence),
        resolution=_nullable_string(value.get("resolution"), "resolution"),
        superseded_by=_nullable_string(value.get("superseded_by"), "superseded_by"),
    )


def _validate_history(record: BlockerRecord) -> None:
    if not record.history or record.history[0].action != "opened":
        raise ValueError("blocker history must begin with opened")
    if any(left.recorded_at > right.recorded_at for left, right in zip(record.history, record.history[1:])):
        raise ValueError("blocker evidence must be chronological")


def _metadata(value: object) -> Dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, Mapping) or not all(
        isinstance(key, str) and isinstance(item, str) for key, item in value.items()
    ):
        raise ValueError("notification metadata must contain string keys and values")
    return dict(value)


def _evidence(values: Sequence[str]) -> Tuple[str, ...]:
    if not values or not all(isinstance(value, str) and value for value in values):
        raise ValueError("evidence ids must be non-empty strings")
    return tuple(values)


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be non-empty text")
    return value


def _optional_text(value: Optional[str], label: str) -> Optional[str]:
    if value is None:
        return None
    return _text(value, label)


def _required_string(value: Mapping[str, object], key: str) -> str:
    return _text(value.get(key), key)


def _nullable_string(value: object, label: str) -> Optional[str]:
    if value is None:
        return None
    return _text(value, label)


def _aware(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError("recorded_at must be offset-aware")
    return value


def _parse_timestamp(value: object) -> datetime:
    if not isinstance(value, str):
        raise ValueError("recorded_at must be an ISO-8601 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError("recorded_at must be an ISO-8601 timestamp") from None
    return _aware(parsed)
