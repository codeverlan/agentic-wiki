from __future__ import annotations

import json
from pathlib import Path

import pytest

from memwiki.coordinator_journal import CoordinatorJournal
from memwiki.coordinator_migrations import (
    ArtifactPaths,
    CoordinatorMigrationManager,
    CorruptionKind,
    MigrationRegistry,
    MigrationStep,
    UnsupportedSchemaVersion,
)


def _upgrade(value: dict[str, object]) -> dict[str, object]:
    return {**value, "schema_version": 2, "migrated": True}


def _manager(tmp_path: Path) -> CoordinatorMigrationManager:
    registry = MigrationRegistry(current_versions={"events": 2, "state": 2, "checkpoints": 2})
    for kind in ("events", "state", "checkpoints"):
        registry.register(MigrationStep(kind, 1, 2, _upgrade, "add migrated marker"))
    return CoordinatorMigrationManager(
        ArtifactPaths(
            journal=tmp_path / "events.journal",
            state=tmp_path / "state.json",
            checkpoint=tmp_path / "checkpoint.json",
            projection=tmp_path / "projection.json",
            evidence_directory=tmp_path / "evidence",
            backup_directory=tmp_path / "backups",
        ),
        registry,
    )


def test_registry_requires_contiguous_explicit_steps() -> None:
    registry = MigrationRegistry(current_versions={"state": 3})
    registry.register(MigrationStep("state", 1, 2, _upgrade, "one"))

    with pytest.raises(ValueError, match="missing migration step"):
        registry.plan("state", 1)


def test_dry_run_is_deterministic_and_does_not_write(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    manager.paths.state.write_text('{"schema_version":1,"value":7}\n', encoding="utf-8")

    first = manager.plan()
    second = manager.plan()

    assert first == second
    assert first.actions[0].artifact_kind == "state"
    assert first.actions[0].from_version == 1
    assert first.actions[0].to_version == 2
    assert json.loads(manager.paths.state.read_text())["schema_version"] == 1
    assert not manager.paths.backup_directory.exists()


def test_apply_backs_up_before_atomic_migration_and_is_idempotent(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    original = b'{"schema_version":1,"value":7}\n'
    manager.paths.state.write_bytes(original)

    receipt = manager.apply(manager.plan(), occurred_at="2026-07-11T12:00:00+00:00")
    repeated = manager.apply(manager.plan(), occurred_at="2026-07-11T12:01:00+00:00")

    assert json.loads(manager.paths.state.read_text()) == {
        "migrated": True,
        "schema_version": 2,
        "value": 7,
    }
    backup = Path(receipt.actions[0].backup_path or "")
    assert backup.read_bytes() == original
    assert repeated.actions == ()
    assert json.loads(Path(receipt.evidence_path).read_text())["receipt_hash"] == receipt.receipt_hash


def test_apply_refuses_stale_plan_and_preserves_source(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    manager.paths.state.write_text('{"schema_version":1,"value":7}\n', encoding="utf-8")
    plan = manager.plan()
    manager.paths.state.write_text('{"schema_version":1,"value":8}\n', encoding="utf-8")

    with pytest.raises(ValueError, match="changed since migration plan"):
        manager.apply(plan, occurred_at="2026-07-11T12:00:00+00:00")

    assert json.loads(manager.paths.state.read_text())["value"] == 8


def test_unknown_future_version_is_refused(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    manager.paths.checkpoint.write_text('{"schema_version":99}\n', encoding="utf-8")

    with pytest.raises(UnsupportedSchemaVersion, match="future checkpoints schema"):
        manager.plan()


def test_framed_event_records_migrate_and_preserve_record_order(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    journal = CoordinatorJournal(manager.paths.journal)
    journal.append_record({"schema_version": 1, "sequence": 1})
    journal.append_record({"schema_version": 1, "sequence": 2})

    receipt = manager.apply(manager.plan(), occurred_at="2026-07-11T12:00:00+00:00")

    assert journal.read_records() == [
        {"migrated": True, "schema_version": 2, "sequence": 1},
        {"migrated": True, "schema_version": 2, "sequence": 2},
    ]
    assert receipt.actions[0].artifact_kind == "events"


def test_inspection_distinguishes_projection_and_torn_tail_from_journal_corruption(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    journal = CoordinatorJournal(manager.paths.journal)
    journal.append_record({"schema_version": 2, "sequence": 1})
    with manager.paths.journal.open("ab") as stream:
        stream.write(b'00000020:{"incomplete":')
    manager.paths.projection.write_text("not json", encoding="utf-8")

    findings = manager.verify()

    assert {finding.kind for finding in findings} == {
        CorruptionKind.TORN_JOURNAL_TAIL,
        CorruptionKind.PROJECTION,
    }
    assert all(finding.repairable for finding in findings)

    receipt = manager.repair(findings, occurred_at="2026-07-11T12:00:00+00:00")
    assert journal.read_records() == [{"schema_version": 2, "sequence": 1}]
    assert not manager.paths.projection.exists()
    assert receipt.actions == ("quarantine_torn_tail", "rebuild_projection")


def test_complete_record_corruption_is_immutable_and_never_repaired(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    manager.paths.journal.write_bytes(b"00000005:abcde\n")

    findings = manager.verify()

    assert findings[0].kind is CorruptionKind.IMMUTABLE_JOURNAL
    assert findings[0].repairable is False
    with pytest.raises(ValueError, match="immutable journal corruption"):
        manager.repair(findings, occurred_at="2026-07-11T12:00:00+00:00")
    assert manager.paths.journal.read_bytes() == b"00000005:abcde\n"


def test_interrupted_replace_leaves_backup_and_original_recoverable(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    original = b'{"schema_version":1,"value":7}\n'
    manager.paths.state.write_bytes(original)

    def fail_replace(_source: Path, _target: Path) -> None:
        raise OSError("interrupted")

    with pytest.raises(OSError, match="interrupted"):
        manager.apply(
            manager.plan(),
            occurred_at="2026-07-11T12:00:00+00:00",
            replace=fail_replace,
        )

    assert manager.paths.state.read_bytes() == original
    assert next(manager.paths.backup_directory.glob("*.bak")).read_bytes() == original


def test_interrupted_multi_artifact_apply_resumes_and_backup_supports_rollback(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    manager.paths.state.write_text('{"schema_version":1,"value":"state"}\n', encoding="utf-8")
    manager.paths.checkpoint.write_text(
        '{"schema_version":1,"value":"checkpoint"}\n', encoding="utf-8"
    )
    calls = 0

    def fail_second_replace(source: Path, target: Path) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("interrupted")
        source.replace(target)

    with pytest.raises(OSError, match="interrupted"):
        manager.apply(
            manager.plan(),
            occurred_at="2026-07-11T12:00:00+00:00",
            replace=fail_second_replace,
        )

    assert json.loads(manager.paths.state.read_text())["schema_version"] == 2
    assert json.loads(manager.paths.checkpoint.read_text())["schema_version"] == 1
    resumed = manager.apply(manager.plan(), occurred_at="2026-07-11T12:01:00+00:00")
    assert [action.artifact_kind for action in resumed.actions] == ["checkpoints"]

    state_backup = next(manager.paths.backup_directory.glob("state.json-*.bak"))
    manager.restore_backup(state_backup, manager.paths.state)
    assert json.loads(manager.paths.state.read_text()) == {"schema_version": 1, "value": "state"}
