"""Strict, content-addressed packets exchanged between a coordinator and a worker.

This module deliberately has no runtime integration dependency.  It provides a
durable boundary that callers can validate before admitting a worker result.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from pathlib import PurePosixPath
from typing import Any, ClassVar, Dict, Iterable, Mapping, Optional, Tuple, cast


class PacketValidationError(ValueError):
    """A coordinator-worker packet violates its durable contract."""


_HASH_PREFIX = "sha256:"
_DATA_PROFILES = {"no", "yes", "unknown"}
_VALIDATION_OUTCOMES = {"passed", "failed", "skipped"}
_MEMORY_STATUSES = {"delta", "not_applicable"}
_ACCEPTANCE_STATUSES = {"accepted", "rejected", "blocked"}


def _canonical(value: object) -> bytes:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise PacketValidationError("packet contents must be JSON serializable") from exc


def _hash(value: object) -> str:
    return _HASH_PREFIX + hashlib.sha256(_canonical(value)).hexdigest()


def _nonempty(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PacketValidationError(f"{label} must be a non-empty string")
    return value


def _hash_value(value: object, label: str) -> str:
    value = _nonempty(value, label)
    if not value.startswith(_HASH_PREFIX) or len(value) != len(_HASH_PREFIX) + 64:
        raise PacketValidationError(f"{label} must be a sha256 hash")
    if any(character not in "0123456789abcdef" for character in value[len(_HASH_PREFIX) :]):
        raise PacketValidationError(f"{label} must be a lowercase sha256 hash")
    return value


def _revision(value: object, label: str) -> str:
    value = _nonempty(value, label)
    if any(character.isspace() for character in value):
        raise PacketValidationError(f"{label} must not contain whitespace")
    return value


def _path(value: object, label: str) -> str:
    value = _nonempty(value, label)
    path = PurePosixPath(value)
    if path.is_absolute() or not path.parts or ".." in path.parts or value in {"", "."}:
        raise PacketValidationError(f"{label} must be a confined relative path")
    return path.as_posix().rstrip("/")


def _strings(values: Iterable[object], label: str, *, allow_empty: bool = False) -> Tuple[str, ...]:
    result = tuple(_nonempty(value, label) for value in values)
    if not allow_empty and not result:
        raise PacketValidationError(f"{label} must not be empty")
    if len(result) != len(set(result)):
        raise PacketValidationError(f"{label} must not contain duplicates")
    return result


def _strict_fields(payload: Mapping[str, object], expected: set[str], label: str) -> None:
    if set(payload) != expected:
        raise PacketValidationError(f"{label} fields do not match the contract")


def _mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or not all(isinstance(key, str) for key in value):
        raise PacketValidationError(f"{label} must be an object with string keys")
    return value


def _sequence(value: object, label: str) -> Tuple[object, ...]:
    if not isinstance(value, (list, tuple)):
        raise PacketValidationError(f"{label} must be an array")
    return tuple(value)


def _overlaps(left: str, right: str) -> bool:
    left_parts = PurePosixPath(left).parts
    right_parts = PurePosixPath(right).parts
    return left_parts[: min(len(left_parts), len(right_parts))] == right_parts[: min(len(left_parts), len(right_parts))]


@dataclass(frozen=True)
class AcceptanceContract:
    criteria: Tuple[str, ...]
    required_validations: Tuple[str, ...]

    @classmethod
    def create(cls, *, criteria: Iterable[object], required_validations: Iterable[object]) -> "AcceptanceContract":
        return cls(_strings(criteria, "acceptance criteria"), _strings(required_validations, "required validations"))

    def to_dict(self) -> Dict[str, object]:
        return {"criteria": list(self.criteria), "required_validations": list(self.required_validations)}

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> "AcceptanceContract":
        _strict_fields(payload, {"criteria", "required_validations"}, "acceptance")
        try:
            return cls.create(criteria=payload["criteria"], required_validations=payload["required_validations"])  # type: ignore[arg-type]
        except (TypeError, PacketValidationError) as exc:
            raise PacketValidationError(f"invalid acceptance contract: {exc}") from exc


@dataclass(frozen=True)
class Route:
    channel: str
    destination: str

    @classmethod
    def create(cls, *, channel: object, destination: object) -> "Route":
        return cls(_nonempty(channel, "route channel"), _path(destination, "route destination"))

    def to_dict(self) -> Dict[str, str]:
        return {"channel": self.channel, "destination": self.destination}

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> "Route":
        _strict_fields(payload, {"channel", "destination"}, "route")
        return cls.create(channel=payload["channel"], destination=payload["destination"])


@dataclass(frozen=True)
class PrivacyAttestation:
    data_profile: str
    local_only: bool
    synthetic_only: bool

    @classmethod
    def create(cls, *, data_profile: object, local_only: object, synthetic_only: object) -> "PrivacyAttestation":
        if data_profile not in _DATA_PROFILES:
            raise PacketValidationError("privacy data_profile must be no, yes, or unknown")
        if not isinstance(local_only, bool) or not isinstance(synthetic_only, bool):
            raise PacketValidationError("privacy flags must be booleans")
        if data_profile in {"yes", "unknown"} and (not local_only or not synthetic_only):
            raise PacketValidationError("PHI or unknown privacy profiles require local-only synthetic handling")
        return cls(str(data_profile), local_only, synthetic_only)

    def to_dict(self) -> Dict[str, object]:
        return {"data_profile": self.data_profile, "local_only": self.local_only, "synthetic_only": self.synthetic_only}

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> "PrivacyAttestation":
        _strict_fields(payload, {"data_profile", "local_only", "synthetic_only"}, "privacy")
        return cls.create(
            data_profile=payload["data_profile"],
            local_only=payload["local_only"],
            synthetic_only=payload["synthetic_only"],
        )


@dataclass(frozen=True)
class ContextEntry:
    source_id: str
    revision: str
    content_hash: str

    @classmethod
    def create(cls, *, source_id: object, revision: object, content_hash: object) -> "ContextEntry":
        return cls(
            _nonempty(source_id, "context source_id"),
            _revision(revision, "context revision"),
            _hash_value(content_hash, "context content_hash"),
        )

    def to_dict(self) -> Dict[str, str]:
        return {"source_id": self.source_id, "revision": self.revision, "content_hash": self.content_hash}

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> "ContextEntry":
        _strict_fields(payload, {"source_id", "revision", "content_hash"}, "context entry")
        return cls.create(
            source_id=payload["source_id"],
            revision=payload["revision"],
            content_hash=payload["content_hash"],
        )


@dataclass(frozen=True)
class ValidationReceipt:
    command: str
    outcome: str
    evidence_hash: str

    @classmethod
    def create(cls, *, command: object, outcome: object, evidence_hash: object) -> "ValidationReceipt":
        command = _nonempty(command, "validation command")
        if outcome not in _VALIDATION_OUTCOMES:
            raise PacketValidationError("validation outcome must be passed, failed, or skipped")
        return cls(command, str(outcome), _hash_value(evidence_hash, "validation evidence_hash"))

    def to_dict(self) -> Dict[str, str]:
        return {"command": self.command, "outcome": self.outcome, "evidence_hash": self.evidence_hash}

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> "ValidationReceipt":
        _strict_fields(payload, {"command", "outcome", "evidence_hash"}, "validation receipt")
        return cls.create(
            command=payload["command"],
            outcome=payload["outcome"],
            evidence_hash=payload["evidence_hash"],
        )


@dataclass(frozen=True)
class MemoryDelta:
    status: str
    entries: Tuple[str, ...]
    reason: Optional[str]

    @classmethod
    def create(cls, *, status: object, entries: Iterable[object], reason: object = None) -> "MemoryDelta":
        if status not in _MEMORY_STATUSES:
            raise PacketValidationError("memory delta status must be delta or not_applicable")
        serialized = tuple(_canonical(_mapping(item, "memory delta entry")).decode("utf-8") for item in entries)
        if len(serialized) != len(set(serialized)):
            raise PacketValidationError("memory delta entries must not contain duplicates")
        if status == "delta":
            if not serialized or reason is not None:
                raise PacketValidationError("delta memory requires entries and no reason")
        else:
            if serialized or not isinstance(reason, str) or not reason.strip():
                raise PacketValidationError("not_applicable memory delta requires a reason and no entries")
        return cls(str(status), serialized, reason if isinstance(reason, str) else None)

    @classmethod
    def delta(cls, entries: Iterable[object]) -> "MemoryDelta":
        return cls.create(status="delta", entries=entries)

    @classmethod
    def not_applicable(cls, reason: str) -> "MemoryDelta":
        return cls.create(status="not_applicable", entries=(), reason=reason)

    def to_dict(self) -> Dict[str, object]:
        return {"status": self.status, "entries": [json.loads(item) for item in self.entries], "reason": self.reason}

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> "MemoryDelta":
        _strict_fields(payload, {"status", "entries", "reason"}, "memory delta")
        try:
            return cls.create(status=payload["status"], entries=payload["entries"], reason=payload["reason"])  # type: ignore[arg-type]
        except (TypeError, PacketValidationError) as exc:
            raise PacketValidationError(f"invalid memory delta: {exc}") from exc


class _Packet:
    hash_field: ClassVar[str]

    def _content_dict(self) -> Dict[str, object]:
        value = self.to_dict()
        value.pop(self.hash_field)
        return value

    def to_dict(self) -> Dict[str, object]:
        raise NotImplementedError

    def rehash(self: Any) -> Any:
        return replace(self, **{self.hash_field: _hash(self._content_dict())})


@dataclass(frozen=True)
class AssignmentPacket(_Packet):
    hash_field: ClassVar[str] = "assignment_hash"
    assignment_id: str
    worker_id: str
    owned_paths: Tuple[str, ...]
    acceptance: AcceptanceContract
    route: Route
    base_revision: str
    privacy: PrivacyAttestation
    assignment_hash: str

    @classmethod
    def create(
        cls,
        *,
        assignment_id: object,
        worker_id: object,
        owned_paths: Iterable[object],
        acceptance: AcceptanceContract,
        route: Route,
        base_revision: object,
        privacy: PrivacyAttestation,
    ) -> "AssignmentPacket":
        if (
            not isinstance(acceptance, AcceptanceContract)
            or not isinstance(route, Route)
            or not isinstance(privacy, PrivacyAttestation)
        ):
            raise PacketValidationError("assignment nested records are invalid")
        paths = tuple(_path(value, "owned path") for value in _strings(owned_paths, "owned paths"))
        packet = cls(
            _nonempty(assignment_id, "assignment_id"),
            _nonempty(worker_id, "worker_id"),
            paths,
            acceptance,
            route,
            _revision(base_revision, "base_revision"),
            privacy,
            "",
        )
        return cast("AssignmentPacket", packet.rehash())

    def to_dict(self) -> Dict[str, object]:
        return {
            "assignment_id": self.assignment_id,
            "worker_id": self.worker_id,
            "owned_paths": list(self.owned_paths),
            "acceptance": self.acceptance.to_dict(),
            "route": self.route.to_dict(),
            "base_revision": self.base_revision,
            "privacy": self.privacy.to_dict(),
            "assignment_hash": self.assignment_hash,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> "AssignmentPacket":
        _strict_fields(
            payload,
            {
                "assignment_id",
                "worker_id",
                "owned_paths",
                "acceptance",
                "route",
                "base_revision",
                "privacy",
                "assignment_hash",
            },
            "assignment packet",
        )
        result = cls.create(
            assignment_id=payload["assignment_id"],
            worker_id=payload["worker_id"],
            owned_paths=_sequence(payload["owned_paths"], "owned_paths"),
            acceptance=AcceptanceContract.from_dict(_mapping(payload["acceptance"], "acceptance")),
            route=Route.from_dict(_mapping(payload["route"], "route")),
            base_revision=payload["base_revision"],
            privacy=PrivacyAttestation.from_dict(_mapping(payload["privacy"], "privacy")),
        )
        if payload["assignment_hash"] != result.assignment_hash:
            raise PacketValidationError("assignment_hash does not match contents")
        return result


@dataclass(frozen=True)
class ContextPacket(_Packet):
    hash_field: ClassVar[str] = "context_hash"
    context_id: str
    assignment_id: str
    assignment_hash: str
    entries: Tuple[ContextEntry, ...]
    privacy: PrivacyAttestation
    context_hash: str

    @classmethod
    def create(
        cls,
        *,
        context_id: object,
        assignment_id: object,
        assignment_hash: object,
        entries: Iterable[ContextEntry],
        privacy: PrivacyAttestation,
    ) -> "ContextPacket":
        values = tuple(entries)
        if not values or not all(isinstance(item, ContextEntry) for item in values):
            raise PacketValidationError("context entries must be non-empty context entry records")
        if len({item.source_id for item in values}) != len(values) or not isinstance(privacy, PrivacyAttestation):
            raise PacketValidationError("context entries must have unique source ids and valid privacy")
        packet = cls(
            _nonempty(context_id, "context_id"),
            _nonempty(assignment_id, "assignment_id"),
            _hash_value(assignment_hash, "assignment_hash"),
            values,
            privacy,
            "",
        )
        return cast("ContextPacket", packet.rehash())

    def to_dict(self) -> Dict[str, object]:
        return {
            "context_id": self.context_id,
            "assignment_id": self.assignment_id,
            "assignment_hash": self.assignment_hash,
            "entries": [item.to_dict() for item in self.entries],
            "privacy": self.privacy.to_dict(),
            "context_hash": self.context_hash,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> "ContextPacket":
        _strict_fields(
            payload,
            {"context_id", "assignment_id", "assignment_hash", "entries", "privacy", "context_hash"},
            "context packet",
        )
        entries = tuple(
            ContextEntry.from_dict(_mapping(item, "context entry"))
            for item in _sequence(payload["entries"], "context entries")
        )
        result = cls.create(
            context_id=payload["context_id"],
            assignment_id=payload["assignment_id"],
            assignment_hash=payload["assignment_hash"],
            entries=entries,
            privacy=PrivacyAttestation.from_dict(_mapping(payload["privacy"], "privacy")),
        )
        if payload["context_hash"] != result.context_hash:
            raise PacketValidationError("context_hash does not match contents")
        return result


@dataclass(frozen=True)
class ResultPacket(_Packet):
    hash_field: ClassVar[str] = "result_hash"
    result_id: str
    assignment_id: str
    assignment_hash: str
    context_id: str
    context_hash: str
    base_revision: str
    result_revision: str
    changed_paths: Tuple[Tuple[str, str], ...]
    validations: Tuple[ValidationReceipt, ...]
    privacy: PrivacyAttestation
    memory_delta: MemoryDelta
    result_hash: str

    @classmethod
    def create(
        cls,
        *,
        result_id: object,
        assignment_id: object,
        assignment_hash: object,
        context_id: object,
        context_hash: object,
        base_revision: object,
        result_revision: object,
        changed_paths: Mapping[str, object],
        validations: Iterable[ValidationReceipt],
        privacy: PrivacyAttestation,
        memory_delta: MemoryDelta,
    ) -> "ResultPacket":
        if (
            not isinstance(changed_paths, Mapping)
            or not isinstance(privacy, PrivacyAttestation)
            or not isinstance(memory_delta, MemoryDelta)
        ):
            raise PacketValidationError("result nested records are invalid")
        changes = tuple(
            sorted(
                (_path(path, "changed path"), _hash_value(digest, "changed path hash"))
                for path, digest in changed_paths.items()
            )
        )
        if not changes or len({path for path, _ in changes}) != len(changes):
            raise PacketValidationError("changed paths must be non-empty and unique")
        receipts = tuple(validations)
        if not all(isinstance(item, ValidationReceipt) for item in receipts):
            raise PacketValidationError("validations must be validation receipt records")
        packet = cls(
            _nonempty(result_id, "result_id"),
            _nonempty(assignment_id, "assignment_id"),
            _hash_value(assignment_hash, "assignment_hash"),
            _nonempty(context_id, "context_id"),
            _hash_value(context_hash, "context_hash"),
            _revision(base_revision, "base_revision"),
            _revision(result_revision, "result_revision"),
            changes,
            receipts,
            privacy,
            memory_delta,
            "",
        )
        return cast("ResultPacket", packet.rehash())

    def to_dict(self) -> Dict[str, object]:
        return {
            "result_id": self.result_id,
            "assignment_id": self.assignment_id,
            "assignment_hash": self.assignment_hash,
            "context_id": self.context_id,
            "context_hash": self.context_hash,
            "base_revision": self.base_revision,
            "result_revision": self.result_revision,
            "changed_paths": {path: digest for path, digest in self.changed_paths},
            "validations": [item.to_dict() for item in self.validations],
            "privacy": self.privacy.to_dict(),
            "memory_delta": self.memory_delta.to_dict(),
            "result_hash": self.result_hash,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> "ResultPacket":
        _strict_fields(
            payload,
            {
                "result_id",
                "assignment_id",
                "assignment_hash",
                "context_id",
                "context_hash",
                "base_revision",
                "result_revision",
                "changed_paths",
                "validations",
                "privacy",
                "memory_delta",
                "result_hash",
            },
            "result packet",
        )
        result = cls.create(
            result_id=payload["result_id"],
            assignment_id=payload["assignment_id"],
            assignment_hash=payload["assignment_hash"],
            context_id=payload["context_id"],
            context_hash=payload["context_hash"],
            base_revision=payload["base_revision"],
            result_revision=payload["result_revision"],
            changed_paths=_mapping(payload["changed_paths"], "changed_paths"),
            validations=tuple(
                ValidationReceipt.from_dict(_mapping(item, "validation receipt"))
                for item in _sequence(payload["validations"], "validations")
            ),
            privacy=PrivacyAttestation.from_dict(_mapping(payload["privacy"], "privacy")),
            memory_delta=MemoryDelta.from_dict(_mapping(payload["memory_delta"], "memory_delta")),
        )
        if payload["result_hash"] != result.result_hash:
            raise PacketValidationError("result_hash does not match contents")
        return result


@dataclass(frozen=True)
class WorkerReportPacket(_Packet):
    hash_field: ClassVar[str] = "report_hash"
    report_id: str
    assignment_id: str
    assignment_hash: str
    context_id: str
    context_hash: str
    result_id: str
    result_hash: str
    route: Route
    acceptance_status: str
    privacy: PrivacyAttestation
    memory_delta: MemoryDelta
    summary: str
    report_hash: str

    @classmethod
    def create(
        cls,
        *,
        report_id: object,
        assignment_id: object,
        assignment_hash: object,
        context_id: object,
        context_hash: object,
        result_id: object,
        result_hash: object,
        route: Route,
        acceptance_status: object,
        privacy: PrivacyAttestation,
        memory_delta: MemoryDelta,
        summary: object,
    ) -> "WorkerReportPacket":
        if (
            not isinstance(route, Route)
            or not isinstance(privacy, PrivacyAttestation)
            or not isinstance(memory_delta, MemoryDelta)
        ):
            raise PacketValidationError("report nested records are invalid")
        if acceptance_status not in _ACCEPTANCE_STATUSES:
            raise PacketValidationError("report acceptance_status is invalid")
        packet = cls(
            _nonempty(report_id, "report_id"),
            _nonempty(assignment_id, "assignment_id"),
            _hash_value(assignment_hash, "assignment_hash"),
            _nonempty(context_id, "context_id"),
            _hash_value(context_hash, "context_hash"),
            _nonempty(result_id, "result_id"),
            _hash_value(result_hash, "result_hash"),
            route,
            str(acceptance_status),
            privacy,
            memory_delta,
            _nonempty(summary, "summary"),
            "",
        )
        return cast("WorkerReportPacket", packet.rehash())

    def to_dict(self) -> Dict[str, object]:
        return {
            "report_id": self.report_id,
            "assignment_id": self.assignment_id,
            "assignment_hash": self.assignment_hash,
            "context_id": self.context_id,
            "context_hash": self.context_hash,
            "result_id": self.result_id,
            "result_hash": self.result_hash,
            "route": self.route.to_dict(),
            "acceptance_status": self.acceptance_status,
            "privacy": self.privacy.to_dict(),
            "memory_delta": self.memory_delta.to_dict(),
            "summary": self.summary,
            "report_hash": self.report_hash,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> "WorkerReportPacket":
        _strict_fields(
            payload,
            {
                "report_id",
                "assignment_id",
                "assignment_hash",
                "context_id",
                "context_hash",
                "result_id",
                "result_hash",
                "route",
                "acceptance_status",
                "privacy",
                "memory_delta",
                "summary",
                "report_hash",
            },
            "worker report packet",
        )
        result = cls.create(
            report_id=payload["report_id"],
            assignment_id=payload["assignment_id"],
            assignment_hash=payload["assignment_hash"],
            context_id=payload["context_id"],
            context_hash=payload["context_hash"],
            result_id=payload["result_id"],
            result_hash=payload["result_hash"],
            route=Route.from_dict(_mapping(payload["route"], "route")),
            acceptance_status=payload["acceptance_status"],
            privacy=PrivacyAttestation.from_dict(_mapping(payload["privacy"], "privacy")),
            memory_delta=MemoryDelta.from_dict(_mapping(payload["memory_delta"], "memory_delta")),
            summary=payload["summary"],
        )
        if payload["report_hash"] != result.report_hash:
            raise PacketValidationError("report_hash does not match contents")
        return result


@dataclass(frozen=True)
class AdmissionDecision(_Packet):
    hash_field: ClassVar[str] = "admission_hash"
    report_hash: str
    admitted: bool
    reasons: Tuple[str, ...]
    admission_hash: str

    @classmethod
    def create(cls, *, report_hash: object, admitted: object, reasons: Iterable[object]) -> "AdmissionDecision":
        if not isinstance(admitted, bool):
            raise PacketValidationError("admission admitted must be a boolean")
        values = _strings(reasons, "admission reasons", allow_empty=True)
        if admitted and values:
            raise PacketValidationError("admitted decisions must not have reasons")
        if not admitted and not values:
            raise PacketValidationError("rejected decisions require reasons")
        packet = cls(_hash_value(report_hash, "report_hash"), admitted, values, "")
        return cast("AdmissionDecision", packet.rehash())

    def to_dict(self) -> Dict[str, object]:
        return {
            "report_hash": self.report_hash,
            "admitted": self.admitted,
            "reasons": list(self.reasons),
            "admission_hash": self.admission_hash,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> "AdmissionDecision":
        _strict_fields(payload, {"report_hash", "admitted", "reasons", "admission_hash"}, "admission decision")
        result = cls.create(
            report_hash=payload["report_hash"],
            admitted=payload["admitted"],
            reasons=_sequence(payload["reasons"], "admission reasons"),
        )
        if payload["admission_hash"] != result.admission_hash:
            raise PacketValidationError("admission_hash does not match contents")
        return result


class PacketAdmission:
    """Validate a complete packet chain before it can enter coordinator state."""

    def admit(
        self, assignment: AssignmentPacket, context: ContextPacket, result: ResultPacket, report: WorkerReportPacket
    ) -> AdmissionDecision:
        for value, label in ((assignment, "assignment"), (context, "context"), (result, "result"), (report, "report")):
            if not isinstance(value, (AssignmentPacket, ContextPacket, ResultPacket, WorkerReportPacket)):
                raise PacketValidationError(f"{label} must be a coordinator-worker packet")
        reasons = []
        for packet, label in (
            (assignment, "assignment"),
            (context, "context"),
            (result, "result"),
            (report, "report"),
        ):
            if getattr(packet, packet.hash_field) != getattr(packet.rehash(), packet.hash_field):
                reasons.append(f"{label} hash does not match contents")
        if context.assignment_id != assignment.assignment_id or context.assignment_hash != assignment.assignment_hash:
            reasons.append("context does not match assignment")
        if context.privacy != assignment.privacy:
            reasons.append("context privacy does not match assignment")
        if (result.assignment_id, result.assignment_hash) != (assignment.assignment_id, assignment.assignment_hash):
            reasons.append("result does not match assignment")
        if (result.context_id, result.context_hash) != (context.context_id, context.context_hash):
            reasons.append("result does not match context")
        if result.base_revision != assignment.base_revision:
            reasons.append("result base revision does not match assignment")
        if result.privacy != assignment.privacy:
            reasons.append("result privacy policy is invalid")
        for path, _ in result.changed_paths:
            if not any(_overlaps(path, owned) for owned in assignment.owned_paths):
                reasons.append("changed path is outside assignment ownership")
                break
        passed = {receipt.command for receipt in result.validations if receipt.outcome == "passed"}
        for command in assignment.acceptance.required_validations:
            if command not in passed:
                reasons.append(f"required validation is missing: {command}")
        if (report.assignment_id, report.assignment_hash) != (assignment.assignment_id, assignment.assignment_hash):
            reasons.append("report does not match assignment")
        if (report.context_id, report.context_hash) != (context.context_id, context.context_hash):
            reasons.append("report does not match context")
        if (report.result_id, report.result_hash) != (result.result_id, result.result_hash):
            reasons.append("report does not match result")
        if report.route != assignment.route:
            reasons.append("report route does not match assignment")
        if report.acceptance_status != "accepted":
            reasons.append("report acceptance status is not accepted")
        if report.privacy != assignment.privacy:
            reasons.append("report privacy policy is invalid")
        if report.memory_delta != result.memory_delta:
            reasons.append("report memory delta does not match result")
        return AdmissionDecision.create(report_hash=report.report_hash, admitted=not reasons, reasons=reasons)
