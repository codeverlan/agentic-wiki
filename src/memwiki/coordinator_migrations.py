from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from .coordinator_events import CoordinatorEvent, verify_event_chain
from .coordinator_journal import CoordinatorJournal

JsonObject = Dict[str, object]
Upgrade = Callable[[JsonObject], JsonObject]
Replace = Callable[[Path, Path], None]


class UnsupportedSchemaVersion(ValueError):
    """Raised when an artifact is newer than this coordinator understands."""


class CorruptionKind(str, Enum):
    PROJECTION = "projection"
    TORN_JOURNAL_TAIL = "torn_journal_tail"
    IMMUTABLE_JOURNAL = "immutable_journal"
    CHECKPOINT = "checkpoint"


def _canonical(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError("migration data must be JSON serializable") from exc


def _digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


@dataclass(frozen=True)
class MigrationStep:
    artifact_kind: str
    from_version: int
    to_version: int
    upgrade: Upgrade
    description: str

    def __post_init__(self) -> None:
        if not self.artifact_kind or self.from_version < 1 or self.to_version != self.from_version + 1:
            raise ValueError("migration steps must name an artifact and advance exactly one version")
        if not self.description:
            raise ValueError("migration step description is required")


class MigrationRegistry:
    def __init__(self, *, current_versions: Mapping[str, int]) -> None:
        if not current_versions or any(not kind or version < 1 for kind, version in current_versions.items()):
            raise ValueError("current schema versions must be positive")
        self.current_versions = dict(current_versions)
        self._steps: Dict[Tuple[str, int], MigrationStep] = {}

    def register(self, step: MigrationStep) -> None:
        if step.artifact_kind not in self.current_versions:
            raise ValueError("migration step has unknown artifact kind")
        key = (step.artifact_kind, step.from_version)
        if key in self._steps:
            raise ValueError("duplicate migration step")
        if step.to_version > self.current_versions[step.artifact_kind]:
            raise ValueError("migration step exceeds current schema version")
        self._steps[key] = step

    def plan(self, artifact_kind: str, from_version: int) -> Tuple[MigrationStep, ...]:
        if artifact_kind not in self.current_versions:
            raise ValueError("unknown artifact kind")
        current = self.current_versions[artifact_kind]
        if from_version > current:
            raise UnsupportedSchemaVersion(
                f"future {artifact_kind} schema version {from_version}; current version is {current}"
            )
        steps: List[MigrationStep] = []
        version = from_version
        while version < current:
            step = self._steps.get((artifact_kind, version))
            if step is None:
                raise ValueError(f"missing migration step for {artifact_kind} version {version}")
            steps.append(step)
            version = step.to_version
        return tuple(steps)


@dataclass(frozen=True)
class ArtifactPaths:
    journal: Path
    state: Path
    checkpoint: Path
    projection: Path
    evidence_directory: Path
    backup_directory: Path


@dataclass(frozen=True)
class MigrationAction:
    artifact_kind: str
    path: str
    from_version: int
    to_version: int
    source_digest: str
    descriptions: Tuple[str, ...]


@dataclass(frozen=True)
class MigrationPlan:
    actions: Tuple[MigrationAction, ...]
    plan_hash: str


@dataclass(frozen=True)
class AppliedMigration:
    artifact_kind: str
    path: str
    from_version: int
    to_version: int
    source_digest: str
    result_digest: str
    backup_path: Optional[str]


@dataclass(frozen=True)
class MigrationReceipt:
    plan_hash: str
    occurred_at: str
    actions: Tuple[AppliedMigration, ...]
    receipt_hash: str
    evidence_path: str


@dataclass(frozen=True)
class CorruptionFinding:
    kind: CorruptionKind
    path: str
    repairable: bool
    detail: str
    evidence_digest: str


@dataclass(frozen=True)
class RepairReceipt:
    occurred_at: str
    findings: Tuple[CorruptionFinding, ...]
    actions: Tuple[str, ...]
    receipt_hash: str
    evidence_path: str


class CoordinatorMigrationManager:
    _ORDER = ("events", "state", "checkpoints")

    def __init__(self, paths: ArtifactPaths, registry: MigrationRegistry) -> None:
        self.paths = paths
        self.registry = registry

    def plan(self) -> MigrationPlan:
        actions: List[MigrationAction] = []
        for kind in self._ORDER:
            path = self._path(kind)
            if not path.exists():
                continue
            raw = path.read_bytes()
            values = self._decode(kind, raw)
            versions = {self._version(value, kind) for value in values}
            if len(versions) != 1:
                raise ValueError(f"mixed {kind} schema versions are not supported")
            version = next(iter(versions))
            steps = self.registry.plan(kind, version)
            if steps:
                actions.append(
                    MigrationAction(
                        artifact_kind=kind,
                        path=str(path),
                        from_version=version,
                        to_version=steps[-1].to_version,
                        source_digest=_digest(raw),
                        descriptions=tuple(step.description for step in steps),
                    )
                )
        unsigned = [asdict(action) for action in actions]
        return MigrationPlan(tuple(actions), _digest(_canonical(unsigned)))

    def apply(
        self,
        plan: MigrationPlan,
        *,
        occurred_at: str,
        replace: Replace = os.replace,
    ) -> MigrationReceipt:
        if plan.plan_hash != _digest(_canonical([asdict(action) for action in plan.actions])):
            raise ValueError("migration plan hash is invalid")
        applied: List[AppliedMigration] = []
        for action in plan.actions:
            path = self._path(action.artifact_kind)
            raw = path.read_bytes()
            if str(path) != action.path or _digest(raw) != action.source_digest:
                raise ValueError(f"{action.artifact_kind} changed since migration plan")
            values = self._decode(action.artifact_kind, raw)
            migrated = [self._upgrade(action.artifact_kind, value) for value in values]
            output = self._encode(action.artifact_kind, migrated)
            backup = self._backup(path, raw, action.source_digest)
            self._atomic_write(path, output, replace=replace)
            applied.append(
                AppliedMigration(
                    artifact_kind=action.artifact_kind,
                    path=str(path),
                    from_version=action.from_version,
                    to_version=action.to_version,
                    source_digest=action.source_digest,
                    result_digest=_digest(output),
                    backup_path=str(backup),
                )
            )
        return self._migration_receipt(plan.plan_hash, occurred_at, tuple(applied))

    def verify(self) -> Tuple[CorruptionFinding, ...]:
        findings: List[CorruptionFinding] = []
        if self.paths.journal.exists():
            raw = self.paths.journal.read_bytes()
            journal = CoordinatorJournal(self.paths.journal)
            records, error = journal._scan(raw)
            if error is not None:
                torn = str(error).startswith("torn journal frame")
                findings.append(
                    CorruptionFinding(
                        kind=(CorruptionKind.TORN_JOURNAL_TAIL if torn else CorruptionKind.IMMUTABLE_JOURNAL),
                        path=str(self.paths.journal),
                        repairable=torn,
                        detail=str(error),
                        evidence_digest=_digest(raw),
                    )
                )
            elif records and all("event_hash" in record for record in records):
                try:
                    verify_event_chain(CoordinatorEvent.from_dict(record) for record in records)
                except ValueError as exc:
                    findings.append(
                        CorruptionFinding(
                            CorruptionKind.IMMUTABLE_JOURNAL,
                            str(self.paths.journal),
                            False,
                            f"event chain invalid: {exc}",
                            _digest(raw),
                        )
                    )
        if self.paths.projection.exists():
            raw = self.paths.projection.read_bytes()
            try:
                value = json.loads(raw)
                if not isinstance(value, dict):
                    raise ValueError("projection must contain an object")
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
                findings.append(
                    CorruptionFinding(
                        CorruptionKind.PROJECTION,
                        str(self.paths.projection),
                        True,
                        str(exc),
                        _digest(raw),
                    )
                )
        return tuple(findings)

    def repair(
        self,
        findings: Sequence[CorruptionFinding],
        *,
        occurred_at: str,
    ) -> RepairReceipt:
        if any(finding.kind is CorruptionKind.IMMUTABLE_JOURNAL for finding in findings):
            raise ValueError("immutable journal corruption requires quarantine and user-directed restore")
        actions: List[str] = []
        for finding in findings:
            if not finding.repairable:
                raise ValueError(f"{finding.kind.value} corruption is not automatically repairable")
            if finding.kind is CorruptionKind.TORN_JOURNAL_TAIL:
                if CoordinatorJournal(self.paths.journal).quarantine_torn_tail() is not None:
                    actions.append("quarantine_torn_tail")
            elif finding.kind is CorruptionKind.PROJECTION:
                self.paths.projection.unlink(missing_ok=True)
                actions.append("rebuild_projection")
        return self._repair_receipt(occurred_at, tuple(findings), tuple(actions))

    def restore_backup(self, backup: Path, target: Path) -> None:
        if backup.parent.resolve() != self.paths.backup_directory.resolve():
            raise ValueError("backup is outside the migration backup directory")
        self._atomic_write(target, backup.read_bytes())

    def quarantine(self, path: Path) -> Path:
        if path not in (self.paths.journal, self.paths.state, self.paths.checkpoint, self.paths.projection):
            raise ValueError("artifact is not managed by this coordinator")
        destination = path.with_name(f"{path.name}.quarantine-{_digest(path.read_bytes())[:16]}")
        os.replace(path, destination)
        return destination

    def _upgrade(self, kind: str, value: JsonObject) -> JsonObject:
        result = dict(value)
        for step in self.registry.plan(kind, self._version(value, kind)):
            result = step.upgrade(dict(result))
            if self._version(result, kind) != step.to_version:
                raise ValueError("migration step produced the wrong schema version")
        return result

    def _decode(self, kind: str, raw: bytes) -> List[JsonObject]:
        if kind == "events":
            records, error = CoordinatorJournal(self.paths.journal)._scan(raw)
            if error is not None:
                raise ValueError(f"cannot migrate corrupt event journal: {error}")
            return records
        try:
            value = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"cannot migrate invalid {kind} JSON") from exc
        if not isinstance(value, dict):
            raise ValueError(f"{kind} must contain a JSON object")
        return [value]

    def _encode(self, kind: str, values: Iterable[JsonObject]) -> bytes:
        materialized = list(values)
        if kind == "events":
            return b"".join(self._frame(value) for value in materialized)
        if len(materialized) != 1:
            raise ValueError(f"{kind} must contain exactly one object")
        return _canonical(materialized[0]) + b"\n"

    @staticmethod
    def _frame(value: JsonObject) -> bytes:
        payload = _canonical(value)
        return f"{len(payload):08d}:".encode("ascii") + payload + b"\n"

    @staticmethod
    def _version(value: JsonObject, kind: str) -> int:
        version = value.get("schema_version")
        if not isinstance(version, int) or isinstance(version, bool) or version < 1:
            raise ValueError(f"{kind} schema_version must be a positive integer")
        return version

    def _path(self, kind: str) -> Path:
        return {
            "events": self.paths.journal,
            "state": self.paths.state,
            "checkpoints": self.paths.checkpoint,
        }[kind]

    def _backup(self, path: Path, raw: bytes, source_digest: str) -> Path:
        self.paths.backup_directory.mkdir(parents=True, exist_ok=True)
        destination = self.paths.backup_directory / f"{path.name}-{source_digest}.bak"
        if destination.exists() and destination.read_bytes() != raw:
            raise ValueError("existing migration backup does not match source")
        if not destination.exists():
            self._atomic_write(destination, raw)
        return destination

    @staticmethod
    def _atomic_write(path: Path, content: bytes, *, replace: Replace = os.replace) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + ".tmp")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            offset = 0
            while offset < len(content):
                offset += os.write(descriptor, content[offset:])
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        try:
            replace(temporary, path)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
        parent = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(parent)
        finally:
            os.close(parent)

    def _migration_receipt(
        self,
        plan_hash: str,
        occurred_at: str,
        actions: Tuple[AppliedMigration, ...],
    ) -> MigrationReceipt:
        unsigned = {
            "kind": "migration",
            "plan_hash": plan_hash,
            "occurred_at": occurred_at,
            "actions": [asdict(action) for action in actions],
        }
        receipt_hash = _digest(_canonical(unsigned))
        path = self.paths.evidence_directory / f"migration-{receipt_hash}.json"
        self._atomic_write(path, _canonical({**unsigned, "receipt_hash": receipt_hash}) + b"\n")
        return MigrationReceipt(plan_hash, occurred_at, actions, receipt_hash, str(path))

    def _repair_receipt(
        self,
        occurred_at: str,
        findings: Tuple[CorruptionFinding, ...],
        actions: Tuple[str, ...],
    ) -> RepairReceipt:
        unsigned = {
            "kind": "repair",
            "occurred_at": occurred_at,
            "findings": [asdict(finding) for finding in findings],
            "actions": list(actions),
        }
        receipt_hash = _digest(_canonical(unsigned))
        path = self.paths.evidence_directory / f"repair-{receipt_hash}.json"
        self._atomic_write(path, _canonical({**unsigned, "receipt_hash": receipt_hash}) + b"\n")
        return RepairReceipt(occurred_at, findings, actions, receipt_hash, str(path))
