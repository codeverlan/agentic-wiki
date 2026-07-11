from __future__ import annotations

import importlib.util
import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import ModuleType

import pytest

from memwiki.coordinator_assignments import AssignmentEnvelope, BoundedContextManifest, ReportContract
from memwiki.coordinator_credentials import CredentialReference, CredentialResolver, CredentialSource
from memwiki.coordinator_discovery import DiscoveryProposal, ProposalProvenance, replenish_queue
from memwiki.coordinator_events import CoordinatorEvent, EventChainError, verify_event_chain
from memwiki.coordinator_lease import CoordinatorLeaseAuthority, LeaseFencedError, LeaseIdentity
from memwiki.coordinator_privacy import DataProfile, PrivacyAction, PrivacyPolicy, PrivacyViolationError
from memwiki.coordinator_reports import WorkerReportAdmitter
from memwiki.coordinator_slice_leases import SliceLease
from memwiki.coordinator_storage import FileArtifactStorage, LogicalArtifact
from memwiki.coordinator_validation import ValidationCommand, ValidationRunner
from memwiki.coordinator_views import render_coordinator_views
from memwiki.coordinator_worktrees import WorkerWorktree, WorktreeInspection

NOW = datetime(2026, 7, 11, 12, tzinfo=timezone.utc)
PLUGIN_HELPER = Path("/Users/tyler-lcsw/plugins/agent-development-coordinator/scripts/coordinator.py")


def _plugin_helper() -> ModuleType:
    spec = importlib.util.spec_from_file_location("security_review_plugin_helper", PLUGIN_HELPER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_path_traversal_and_symlink_escape_are_denied(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="confined"):
        LogicalArtifact("../../outside")
    root = tmp_path / "artifacts"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (root / "runtime").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="symbolic link"):
        FileArtifactStorage(root).write_artifact(
            LogicalArtifact("runtime/state"), {"ok": True}, "<html></html>"
        )
    assert list(outside.iterdir()) == []


def test_validation_uses_argv_without_shell_interpretation(tmp_path: Path) -> None:
    marker = tmp_path / "injected"
    command = ValidationCommand(
        argv=("/usr/bin/python3", "-c", f"print('literal; touch {marker}')"),
        cwd=".",
        timeout_seconds=5,
        max_output_bytes=2048,
        env={},
    )
    receipt = ValidationRunner(tmp_path).run(command)
    assert receipt.outcome == "passed"
    assert not marker.exists()


def test_arbitrary_nested_auth_shapes_resolve_without_leaking(tmp_path: Path) -> None:
    subprocess_value = "synthetic-auth-canary"
    resolver = CredentialResolver(
        tmp_path,
        ephemeral={"custom": {"headers": [{"scheme": "Custom", "material": subprocess_value}]}},
    )
    resolved = resolver.resolve(
        CredentialReference("custom", CredentialSource.EPHEMERAL, "custom")
    )
    assert resolved.value["headers"][0]["material"] == subprocess_value
    metadata = CredentialReference("custom", CredentialSource.EPHEMERAL, "custom").to_metadata()
    assert subprocess_value not in json.dumps(metadata)
    assert subprocess_value in resolved.secret_values


def test_phi_profiles_deny_remote_operations_with_synthetic_canaries() -> None:
    for profile in (DataProfile.YES, DataProfile.UNKNOWN):
        policy = PrivacyPolicy(profile, phi_canaries=("synthetic-phi-canary",))
        for action in (PrivacyAction.REMOTE_ADAPTER, PrivacyAction.CLOUD_STORAGE):
            with pytest.raises(PrivacyViolationError, match="local-only"):
                policy.authorize(action, synthetic=True)


def test_projection_escapes_xss_and_unsafe_links() -> None:
    attack = "<img src=x onerror=alert(1)>"
    projection = {
        "run_id": attack,
        "updated_at": "2026-07-11T12:00:00+00:00",
        "status": attack,
        "slices": [{"id": "AC-X", "status": "blocked", "title": attack}],
        "agents": [],
        "blockers": [],
        "supervision": [],
        "artifacts": [{"category": "review", "title": attack, "path": "javascript:alert(1)"}],
        "branches": [],
        "external_connections": [],
        "resources": {},
    }
    bundle = render_coordinator_views(projection)
    rendered = "\n".join(bundle.html.values())
    assert attack not in rendered
    assert "&lt;img src=x onerror=alert(1)&gt;" in rendered
    assert 'href="#"' in rendered


def test_stale_coordinator_writer_is_fenced() -> None:
    authority = CoordinatorLeaseAuthority()
    first = LeaseIdentity("host", 1, "first")
    second = LeaseIdentity("host", 2, "second")
    old = authority.acquire(first, now=NOW, ttl=timedelta(seconds=1))
    authority.takeover(second, now=NOW + timedelta(seconds=2), ttl=timedelta(minutes=1))
    with pytest.raises(LeaseFencedError):
        authority.assert_writer(first, old.fencing_token, now=NOW + timedelta(seconds=3))


def test_journal_chain_detects_tampered_payload() -> None:
    first = CoordinatorEvent.create(
        run_id="run-security", sequence=1, projection_revision=1, event_type="run.started",
        payload={"safe": True}, actor={"type": "coordinator", "id": "one"},
        idempotency_key="one", prior_hash=None,
    )
    second = CoordinatorEvent.create(
        run_id="run-security", sequence=2, projection_revision=2, event_type="slice.ready",
        payload={"slice": "AC-047"}, actor={"type": "coordinator", "id": "one"},
        idempotency_key="two", prior_hash=first.event_hash,
    )
    with pytest.raises(EventChainError, match="payload hash"):
        verify_event_chain([first, replace(second, payload={"slice": "AC-EVIL"})])


def test_plugin_mutation_requires_supervision_and_snapshot_detects_changes(tmp_path: Path) -> None:
    proposal = DiscoveryProposal.create(
        proposal_id="proposal:plugin", slice_id="DISC-PLUGIN", title="Mutate plugin",
        depends_on=(), owned_paths=("plugins/coordinator/SKILL.md",),
        acceptance_criteria=("change",), required_tests=("test",), reversible=True,
        plugin_scoped=False,
        provenance=ProposalProvenance("review", "AC-047", "sha256:synthetic"),
    )
    result = replenish_queue((), (proposal,))
    assert result.decisions[0].reason == "plugin-mutation-not-authorized"
    helper = _plugin_helper()
    plugin = tmp_path / "plugin"
    plugin.mkdir()
    source = plugin / "SKILL.md"
    source.write_text("before", encoding="utf-8")
    snapshot = helper.plugin_snapshot(plugin)
    source.write_text("after", encoding="utf-8")
    with pytest.raises(RuntimeError, match="plugin source changed"):
        helper.assert_plugin_unchanged(plugin, snapshot)


def test_malicious_report_is_rejected_and_secret_is_redacted(tmp_path: Path) -> None:
    owned = "src/owned.py"
    path = tmp_path / owned
    path.parent.mkdir(parents=True)
    path.write_text("safe", encoding="utf-8")
    assignment = AssignmentEnvelope.create(
        assignment_id="assignment-security", slice_id="AC-047", worker_id="worker-1",
        context=BoundedContextManifest.create(artifacts=["plan.json"], context_hashes=["sha256:ctx"], token_budget=100),
        owned_paths=[owned], forbidden_paths=["wiki"], base_revision="a" * 40,
        required_tests=["pytest"], permissions=["write"], lease_policy="exclusive",
        report_contract=ReportContract("reports/AC-047.json", ["summary", "changed_files", "tests"], 1),
        issued_at=NOW, acceptance_deadline=NOW + timedelta(minutes=5),
    )
    lease = SliceLease(
        assignment.assignment_id, assignment.assignment_hash, assignment.slice_id,
        assignment.worker_id, assignment.owned_paths, assignment.base_revision, 1, 9, 1,
        "sha256:lease", "active", NOW, NOW, NOW, NOW + timedelta(minutes=10),
    )
    inspection = WorktreeInspection(
        WorkerWorktree(tmp_path, "codex/security", assignment.base_revision, "b" * 40),
        True, True, assignment.owned_paths, False,
    )
    canary = "synthetic-secret-canary"
    report = {"report_id": "evil", "summary": canary, "changed_files": [{"path": "../escape"}]}
    result = WorkerReportAdmitter().admit(
        report, assignment=assignment, lease=lease, inspection=inspection,
        current_coordinator_fencing_token=10, received_at=NOW + timedelta(minutes=1),
        secret_values=(canary,),
    )
    assert result.integrable is False
    encoded = json.dumps(result.to_dict())
    assert canary not in encoded
    assert "[REDACTED]" in encoded
    assert any("stale coordinator fencing token" in reason for reason in result.reasons)
