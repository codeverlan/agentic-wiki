from __future__ import annotations

import hashlib
import html
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import PurePosixPath
from typing import Dict, Iterable, Mapping, Optional, Sequence, Tuple

from memwiki.coordinator_assignments import AssignmentEnvelope
from memwiki.coordinator_slice_leases import SliceLease
from memwiki.coordinator_worktrees import WorktreeInspection


class ReportAdmissionError(RuntimeError):
    """A report cannot be represented or checked safely."""


_REPORT_FIELDS = {
    "schema_version",
    "report_id",
    "assignment_id",
    "assignment_hash",
    "slice_id",
    "worker_id",
    "attempt",
    "lease_token",
    "coordinator_fencing_token",
    "base_revision",
    "context_hash",
    "submitted_at",
    "outcome",
    "summary",
    "changed_files",
    "tests",
    "blockers",
    "memory_delta",
    "capability_evidence",
    "worktree",
    "report_hash",
}
_HASH_PREFIX = "sha256:"
_OUTCOMES = {"success", "blocked", "retryable", "supervision", "failed"}


def _digest(content: bytes) -> str:
    return f"{_HASH_PREFIX}{hashlib.sha256(content).hexdigest()}"


def _canonical(value: object) -> bytes:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    except (TypeError, ValueError) as exc:
        raise ReportAdmissionError("report must be JSON serializable") from exc


def _valid_hash(value: object) -> bool:
    if not isinstance(value, str) or not value.startswith(_HASH_PREFIX):
        return False
    digest = value.removeprefix(_HASH_PREFIX)
    return len(digest) == 64 and all(character in "0123456789abcdef" for character in digest)


def _relative_path(value: object) -> Optional[str]:
    if not isinstance(value, str):
        return None
    path = PurePosixPath(value)
    if path.is_absolute() or not path.parts or ".." in path.parts or value in {"", "."}:
        return None
    return path.as_posix().rstrip("/")


def _overlaps(left: str, right: str) -> bool:
    left_parts = tuple(part.casefold() for part in PurePosixPath(left).parts)
    right_parts = tuple(part.casefold() for part in PurePosixPath(right).parts)
    common = min(len(left_parts), len(right_parts))
    return left_parts[:common] == right_parts[:common]


def _aware_timestamp(value: object) -> Optional[datetime]:
    if not isinstance(value, str):
        return None
    try:
        result = datetime.fromisoformat(value)
    except ValueError:
        return None
    if result.tzinfo is None or result.utcoffset() is None:
        return None
    return result


def _redact(value: object, secrets: Sequence[str]) -> object:
    if isinstance(value, Mapping):
        return {str(key): _redact(item, secrets) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact(item, secrets) for item in value]
    if isinstance(value, str):
        result = value
        for secret in sorted((item for item in secrets if item), key=len, reverse=True):
            result = result.replace(secret, "[REDACTED]")
        return result
    return value


def _contains_secret(value: object, secrets: Sequence[str]) -> bool:
    encoded = _canonical(value).decode(errors="replace")
    return any(secret and secret in encoded for secret in secrets)


@dataclass(frozen=True)
class PreservedReportEvidence:
    payload: object
    raw_sha256: str
    report_hash: Optional[str]
    received_at: datetime

    def to_dict(self) -> Dict[str, object]:
        return {
            "payload": self.payload,
            "raw_sha256": self.raw_sha256,
            "report_hash": self.report_hash,
            "received_at": self.received_at.isoformat(),
        }


@dataclass(frozen=True)
class WorkerReportAdmission:
    integrable: bool
    reasons: Tuple[str, ...]
    preserved: PreservedReportEvidence

    def to_dict(self) -> Dict[str, object]:
        return {
            "integrable": self.integrable,
            "reasons": list(self.reasons),
            "preserved": self.preserved.to_dict(),
        }

    def to_html(self) -> str:
        payload = self.preserved.payload
        summary = payload.get("summary", "") if isinstance(payload, Mapping) else ""
        report_id = payload.get("report_id", "unknown") if isinstance(payload, Mapping) else "unknown"
        status = "admitted" if self.integrable else "rejected"
        reasons = "".join(f"<li>{html.escape(reason)}</li>" for reason in self.reasons)
        json_ld = {
            "@context": "https://schema.org",
            "@type": "DigitalDocument",
            "identifier": report_id,
            "dateReceived": self.preserved.received_at.isoformat(),
            "sha256": self.preserved.raw_sha256.removeprefix(_HASH_PREFIX),
            "additionalType": "WorkerReport",
        }
        encoded_ld = html.escape(json.dumps(json_ld, sort_keys=True), quote=False)
        return (
            f'<article class="worker-report" data-admission="{status}">'
            f"<header><h1>Worker report {html.escape(str(report_id))}</h1>"
            f"<p>Status: {status}</p></header>"
            f"<section><h2>Summary</h2><p>{html.escape(str(summary))}</p></section>"
            f"<section><h2>Admission findings</h2><ul>{reasons}</ul></section>"
            f'<script type="application/ld+json">{encoded_ld}</script></article>'
        )


class WorkerReportAdmitter:
    """Verify untrusted worker reports before serial integration."""

    @staticmethod
    def report_hash(report: Mapping[str, object]) -> str:
        if not isinstance(report, Mapping):
            raise ReportAdmissionError("report must be an object")
        existing = report.get("report_hash")
        if existing is not None and not isinstance(existing, str):
            raise ReportAdmissionError("report_hash must be a string")
        content = dict(report)
        content.pop("report_hash", None)
        return _digest(_canonical(content))

    def admit(
        self,
        report: Mapping[str, object],
        *,
        assignment: AssignmentEnvelope,
        lease: SliceLease,
        inspection: WorktreeInspection,
        current_coordinator_fencing_token: int,
        received_at: datetime,
        secret_values: Sequence[str] = (),
    ) -> WorkerReportAdmission:
        if received_at.tzinfo is None or received_at.utcoffset() is None:
            raise ValueError("received_at must be timezone-aware")
        reasons: list[str] = []
        try:
            raw = _canonical(report)
            payload: object = _redact(dict(report), secret_values)
        except ReportAdmissionError:
            raw = repr(report).encode("utf-8", errors="replace")
            safe_repr = repr(report)
            for secret in sorted((item for item in secret_values if item), key=len, reverse=True):
                safe_repr = safe_repr.replace(secret, "[REDACTED]")
            payload = {"malformed_repr": safe_repr}
            return self._result(
                payload,
                raw,
                None,
                received_at,
                ["malformed report: report must be JSON serializable"],
            )

        raw_report_hash = report.get("report_hash")
        report_hash = raw_report_hash if isinstance(raw_report_hash, str) else None
        if set(report) != _REPORT_FIELDS:
            missing = sorted(_REPORT_FIELDS - set(report))
            extra = sorted(set(report) - _REPORT_FIELDS)
            reasons.extend(f"report field is missing: {field}" for field in missing)
            reasons.extend(f"unexpected report field: {field}" for field in extra)
        for required in assignment.report_contract.required_fields:
            if required not in report:
                reasons.append(f"required report field is missing: {required}")

        self._check_identity(report, assignment, lease, current_coordinator_fencing_token, reasons)
        self._check_liveness(report, lease, received_at, reasons)
        self._check_schema(report, assignment, reasons)
        self._check_worktree(report, assignment, inspection, reasons)
        self._check_changed_files(report, assignment, inspection, reasons)
        self._check_evidence(report, reasons)

        try:
            expected_hash = self.report_hash(report)
        except ReportAdmissionError as exc:
            reasons.append(str(exc))
        else:
            if report_hash != expected_hash:
                reasons.append("report hash mismatch")
        if secret_values and _contains_secret(report, secret_values):
            reasons.append("report contains a credential value")
        return self._result(payload, raw, report_hash, received_at, reasons)

    @staticmethod
    def _check_identity(
        report: Mapping[str, object],
        assignment: AssignmentEnvelope,
        lease: SliceLease,
        current_fence: int,
        reasons: list[str],
    ) -> None:
        identity = ("assignment_id", "assignment_hash", "slice_id", "worker_id")
        for field in identity:
            if report.get(field) != getattr(assignment, field):
                reasons.append("assignment identity mismatch")
                break
        if report.get("attempt") != lease.attempt:
            reasons.append("attempt mismatch")
        if report.get("lease_token") != lease.lease_token:
            reasons.append("lease token mismatch")
        if report.get("coordinator_fencing_token") != lease.coordinator_fencing_token:
            reasons.append("lease fencing token mismatch")
        if lease.coordinator_fencing_token < current_fence:
            reasons.append("stale coordinator fencing token")
        if report.get("base_revision") != assignment.base_revision:
            reasons.append("base revision mismatch")
        expected_context = _digest("\n".join(assignment.context.context_hashes).encode())
        if report.get("context_hash") != expected_context:
            reasons.append("context hash mismatch")

    @staticmethod
    def _check_liveness(
        report: Mapping[str, object],
        lease: SliceLease,
        received_at: datetime,
        reasons: list[str],
    ) -> None:
        if lease.status != "active" or lease.acknowledged_at is None:
            reasons.append("lease is not active")
        if received_at >= lease.expires_at:
            reasons.append("report arrived after lease expiry")
        submitted = _aware_timestamp(report.get("submitted_at"))
        if submitted is None:
            reasons.append("submitted_at must be a timezone-aware timestamp")
        elif submitted < lease.acquired_at or submitted > received_at:
            reasons.append("submitted_at is outside the admissible interval")

    @staticmethod
    def _check_schema(report: Mapping[str, object], assignment: AssignmentEnvelope, reasons: list[str]) -> None:
        if report.get("schema_version") != assignment.report_contract.schema_version:
            reasons.append("report schema version mismatch")
        if report.get("outcome") not in _OUTCOMES:
            reasons.append("report outcome is invalid")
        for field in ("report_id", "summary"):
            if field in report and (not isinstance(report[field], str) or not report[field]):
                reasons.append(f"{field} must be a non-empty string")
        for field in ("changed_files", "tests", "blockers", "memory_delta", "capability_evidence"):
            if field in report and not isinstance(report[field], list):
                reasons.append(f"{field} must be a list")

    @staticmethod
    def _check_worktree(
        report: Mapping[str, object],
        assignment: AssignmentEnvelope,
        inspection: WorktreeInspection,
        reasons: list[str],
    ) -> None:
        if not inspection.exists or not inspection.registered:
            reasons.append("worker worktree is unavailable")
        if inspection.stale:
            reasons.append("worker worktree is stale")
        if inspection.worktree.base_revision != assignment.base_revision:
            reasons.append("worktree base revision mismatch")
        value = report.get("worktree")
        if not isinstance(value, Mapping):
            reasons.append("worktree report must be an object")
            return
        if value.get("branch") != inspection.worktree.branch:
            reasons.append("worktree branch mismatch")
        if value.get("head_revision") != inspection.worktree.head_revision:
            reasons.append("worktree head mismatch")
        if value.get("conflicted") is not False:
            reasons.append("worktree reports conflicts")
        dirty = value.get("dirty_paths")
        if not isinstance(dirty, list) or tuple(sorted(dirty)) != tuple(sorted(inspection.dirty_paths)):
            reasons.append("worktree dirty paths mismatch")

    @staticmethod
    def _check_changed_files(
        report: Mapping[str, object],
        assignment: AssignmentEnvelope,
        inspection: WorktreeInspection,
        reasons: list[str],
    ) -> None:
        changed = report.get("changed_files")
        if not isinstance(changed, list):
            return
        reported_paths = []
        for item in changed:
            if not isinstance(item, Mapping):
                reasons.append("changed file entry must be an object")
                continue
            path = _relative_path(item.get("path"))
            if path is None:
                reasons.append("changed file path is invalid")
                continue
            reported_paths.append(path)
            if not any(_overlaps(path, owned) for owned in assignment.owned_paths):
                reasons.append("changed path is outside assignment ownership")
            if any(_overlaps(path, denied) for denied in assignment.forbidden_paths):
                reasons.append("changed path overlaps a forbidden path")
            candidate = inspection.worktree.path.joinpath(*PurePosixPath(path).parts)
            try:
                confined = candidate.resolve(strict=True).relative_to(inspection.worktree.path.resolve(strict=True))
            except (FileNotFoundError, ValueError):
                reasons.append("changed file is missing or escapes the worktree")
                continue
            if not confined.parts or candidate.is_symlink() or not candidate.is_file():
                reasons.append("changed file is not a regular confined file")
                continue
            if item.get("sha256") != _digest(candidate.read_bytes()):
                reasons.append("changed file hash mismatch")
        if len(reported_paths) != len(set(reported_paths)):
            reasons.append("changed file paths contain duplicates")
        if tuple(sorted(reported_paths)) != tuple(sorted(inspection.dirty_paths)):
            reasons.append("report changed files do not match worktree changes")

    @staticmethod
    def _check_evidence(report: Mapping[str, object], reasons: list[str]) -> None:
        tests = report.get("tests")
        if isinstance(tests, list):
            for item in tests:
                if not isinstance(item, Mapping):
                    reasons.append("test evidence entry must be an object")
                    continue
                if item.get("status") not in {"passed", "failed", "skipped"}:
                    reasons.append("test evidence status is invalid")
                if not _valid_hash(item.get("evidence_sha256")):
                    reasons.append("test evidence hash is invalid")
        capabilities = report.get("capability_evidence")
        if isinstance(capabilities, list):
            for item in capabilities:
                if not isinstance(item, Mapping) or not _valid_hash(item.get("evidence_sha256")):
                    reasons.append("capability evidence hash is invalid")

    @staticmethod
    def _result(
        payload: object,
        raw: bytes,
        report_hash: Optional[str],
        received_at: datetime,
        reasons: Iterable[str],
    ) -> WorkerReportAdmission:
        unique = tuple(dict.fromkeys(reasons))
        evidence = PreservedReportEvidence(payload, _digest(raw), report_hash, received_at)
        return WorkerReportAdmission(not unique, unique, evidence)
