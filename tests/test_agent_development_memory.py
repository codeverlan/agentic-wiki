import json
from pathlib import Path

from typer.testing import CliRunner

from agentic_wiki import AgenticWikiWorkspace
from memwiki.cli import app
from memwiki.html import extract_jsonld
from memwiki.manifest import read_jsonl

runner = CliRunner()


def invoke(workspace: Path, *args: str):
    return runner.invoke(app, ["--workspace", str(workspace), *args])


def write_slice_queue(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "generated_by": "test",
                "updated_at": "2026-07-07T12:00:00-04:00",
                "source_spec": "SPEC.md",
                "source_of_truth": True,
                "human_artifacts": {
                    "intent_packet_html_template": "_bmad-output/implementation-artifacts/intent-packet/template.html",
                    "archetype_registry_html_template": (
                        "_bmad-output/implementation-artifacts/archetype-registry/template.html"
                    ),
                    "supervision_queue": "_bmad-output/implementation-artifacts/supervision-queue.html",
                    "external_capabilities_html_template": (
                        "_bmad-output/implementation-artifacts/external-capabilities/template.html"
                    ),
                },
                "status_lanes": [
                    {
                        "lane_id": "internal_agent_work",
                        "label": "Internal agent work",
                        "current_summary": "One active renderer slice.",
                        "blocked_summary": "No internal blocker.",
                        "completed_summary": "No completed internal work.",
                        "last_updated": "2026-07-07T12:00:00-04:00",
                        "source_artifacts": ["slice-queue.json"],
                    },
                    {
                        "lane_id": "external_capability_activity",
                        "label": "External capability activity",
                        "current_summary": "No external capability in use.",
                        "blocked_summary": "No external blocker.",
                        "completed_summary": "No completed external invocation.",
                        "last_updated": "2026-07-07T12:00:00-04:00",
                        "source_artifacts": [],
                    },
                    {
                        "lane_id": "validation_evidence",
                        "label": "Validation/evidence",
                        "current_summary": "Renderer test expected.",
                        "blocked_summary": "No validation blocker.",
                        "completed_summary": "No validation completed yet.",
                        "last_updated": "2026-07-07T12:00:00-04:00",
                        "source_artifacts": ["tests/test_agent_development_memory.py"],
                    },
                    {
                        "lane_id": "supervision_required",
                        "label": "Supervision-required items",
                        "current_summary": "No supervision item.",
                        "blocked_summary": "No supervision blocker.",
                        "completed_summary": "No supervision item completed.",
                        "last_updated": "2026-07-07T12:00:00-04:00",
                        "source_artifacts": [],
                    },
                ],
                "status_values": [
                    "active",
                    "blocked",
                    "waiting-on-dependent",
                    "validated",
                    "merged",
                    "superseded",
                ],
                "slices": [
                    {
                        "id": "SLICE-001",
                        "status": "active",
                        "branch": "agent/queue-renderer",
                        "description": "Render run-state HTML from queue JSON.",
                        "dependencies": [],
                        "blocked_by": [],
                        "proposed_by": "coordinator",
                        "assigned_to": "worker-a",
                        "worker_lease": {
                            "lease_id": "LEASE-001",
                            "worker_id": "worker-a",
                            "branch": "agent/queue-renderer",
                            "assigned_paths": ["src/"],
                            "heartbeat_at": "2026-07-07T12:00:00-04:00",
                            "status": "active",
                            "stale_after": "2026-07-07T13:00:00-04:00",
                            "stale_handling": (
                                "Coordinator inspects and preserves slice-owned work before reassignment."
                            ),
                            "reassignment_or_supersession": "",
                        },
                        "allowed_paths": ["src/"],
                        "forbidden_paths": ["raw/"],
                        "validation": ["uv run pytest tests/test_agent_development_memory.py"],
                        "next_action": "Implement renderer.",
                        "updated_at": "2026-07-07T12:00:00-04:00",
                    },
                    {
                        "id": "SLICE-002",
                        "status": "blocked",
                        "branch": "agent/development-memory",
                        "description": "Draft accepted run memory pages.",
                        "dependencies": ["SLICE-001"],
                        "blocked_by": ["renderer missing"],
                        "proposed_by": "worker-b",
                        "assigned_to": "worker-b",
                        "allowed_paths": ["src/"],
                        "forbidden_paths": ["wiki/"],
                        "validation": ["uv run pytest"],
                        "next_action": "Resume after renderer exists.",
                        "updated_at": "2026-07-07T12:05:00-04:00",
                    },
                ],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def write_incident_log(path: Path, *, handles_phi: bool = False) -> None:
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "generated_by": "test",
                "updated_at": "2026-07-08T04:00:00-04:00",
                "source_spec": "SPEC.md",
                "source_of_truth": True,
                "append_only": True,
                "phi_profile": {
                    "handles_phi": handles_phi,
                    "hipaa_log_variant": handles_phi,
                    "no_phi_or_secret_values": True,
                    "compliance_note": "Metadata-only log; not legal certification.",
                    "best_practice_awareness": [
                        "least privilege",
                        "audit logging",
                        "regular security scans",
                    ],
                },
                "monitoring_phases": [
                    {
                        "phase_id": "recurring-security-scan",
                        "cadence": "monthly or before release",
                        "reminder": "Run dependency, secret, static analysis, and access-review checks.",
                        "evidence": "scan report path",
                        "user_action": "Review abnormal behavior and possible information leakage.",
                    }
                ],
                "incidents": [
                    {
                        "id": "INC-001",
                        "timestamp": "2026-07-08T04:00:00-04:00",
                        "severity": "high",
                        "category": "expected_resource_unavailable",
                        "summary": "Expected scanner was unavailable during validation.",
                        "trigger": "preflight",
                        "related_slice": "SLICE-001",
                        "status": "open",
                        "evidence": ["scanner unavailable message"],
                        "monitoring_phase": "recurring-security-scan",
                        "next_action": "Notify user and continue independent slices.",
                    }
                ],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def test_agent_run_state_can_be_drafted_with_source_provenance(tmp_path: Path) -> None:
    wiki = AgenticWikiWorkspace(tmp_path)
    wiki.init()
    queue = tmp_path / "slice-queue.json"
    write_slice_queue(queue)

    draft = wiki.draft_agent_run_state(
        queue,
        objective="Create reusable agent-development memory.",
        stop_reason="queue drain",
        active_branch="agent/queue-renderer",
        commit="abc1234",
        remote_pr="none",
    )

    assert draft.slice_count == 2
    assert draft.status_counts == {"active": 1, "blocked": 1}
    page_path = tmp_path / "drafts" / draft.draft_id / "wiki" / f"{draft.page_id}.html"
    assert page_path.exists()
    metadata = extract_jsonld(page_path)
    assert metadata["memwiki:pageType"] == "agent_run_state"
    assert metadata["memwiki:sliceStatusCounts"] == {"active": 1, "blocked": 1}
    assert metadata["memwiki:humanArtifacts"]["archetype_registry_html_template"].endswith(
        "archetype-registry/template.html"
    )

    source_records = read_jsonl(tmp_path / "manifests/sources.jsonl")
    assert source_records[0]["source_category"] == "agent_run_queue"
    draft_claims = read_jsonl(tmp_path / "drafts" / draft.draft_id / "manifests" / "claims.jsonl")
    assert draft_claims[0]["source_id"] == source_records[0]["source_id"]
    assert draft_claims[0]["provenance"]["source_locator"]["type"] == "json"

    check = wiki.promote(draft.draft_id, check_only=True)
    assert check.check_only is True


def test_agent_cli_renders_standalone_run_state_html(tmp_path: Path) -> None:
    assert invoke(tmp_path, "init").exit_code == 0
    queue = tmp_path / "slice-queue.json"
    output = tmp_path / "run-state.html"
    write_slice_queue(queue)

    result = invoke(
        tmp_path,
        "agent",
        "render-run-state",
        str(queue),
        "--output",
        str(output),
        "--objective",
        "Create reusable agent-development memory.",
        "--stop-reason",
        "queue drain",
    )

    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["slice_count"] == 2
    assert data["status_counts"] == {"active": 1, "blocked": 1}
    assert output.exists()
    html = output.read_text(encoding="utf-8")
    assert "SLICE-001" in html
    assert "renderer missing" in html
    assert "Status Lanes" in html
    assert "internal_agent_work" in html
    assert "One active renderer slice." in html
    assert "validation_evidence" in html
    assert "Worker Lease" in html
    assert "LEASE-001" in html
    assert "Coordinator inspects and preserves slice-owned work before reassignment." in html
    assert "archetype-registry/template.html" in html
    assert extract_jsonld(output)["memwiki:pageType"] == "agent_run_state"


def test_agent_cli_renders_standalone_handoff_digest_html(tmp_path: Path) -> None:
    assert invoke(tmp_path, "init").exit_code == 0
    queue = tmp_path / "slice-queue.json"
    output = tmp_path / "handoff-digest.html"
    write_slice_queue(queue)

    result = invoke(
        tmp_path,
        "agent",
        "render-handoff-digest",
        str(queue),
        "--output",
        str(output),
        "--objective",
        "Create reusable agent-development memory.",
        "--trigger",
        "user-return",
        "--active-authority",
        "slice-queue.json",
        "--resume-command",
        "uv run agentic-wiki agent render-handoff-digest slice-queue.json --output handoff.html",
        "--resume-path",
        str(queue),
    )

    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["slice_count"] == 2
    assert data["status_counts"] == {"active": 1, "blocked": 1}
    assert data["trigger"] == "user-return"
    assert output.exists()
    html = output.read_text(encoding="utf-8")
    assert "Handoff Digest" in html
    assert "Create reusable agent-development memory." in html
    assert "slice-queue.json" in html
    assert "Queue State" in html
    assert "Worker Leases" in html
    assert "LEASE-001" in html
    assert "Supervision And Resume" in html
    assert "supervision-queue.html" in html
    assert "external-capabilities/template.html" in html
    assert "uv run agentic-wiki agent render-handoff-digest" in html
    metadata = extract_jsonld(output)
    assert metadata["memwiki:pageType"] == "agent_handoff_digest"
    assert metadata["memwiki:agentMemoryType"] == "agent_development_handoff_digest"
    assert metadata["memwiki:handoffTrigger"] == "user-return"


def test_agent_cli_renders_phi_aware_incident_log_html(tmp_path: Path) -> None:
    assert invoke(tmp_path, "init").exit_code == 0
    incident_log = tmp_path / "incident-log.json"
    output = tmp_path / "incident-log.html"
    write_incident_log(incident_log, handles_phi=True)

    result = invoke(
        tmp_path,
        "agent",
        "render-incident-log",
        str(incident_log),
        "--output",
        str(output),
    )

    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["incident_count"] == 1
    assert data["handles_phi"] is True
    assert data["hipaa_log_variant"] is True
    assert data["severity_counts"] == {"high": 1}
    html = output.read_text(encoding="utf-8")
    assert "Incident And Exception Log" in html
    assert "PHI/HIPAA Safeguards" in html
    assert "Metadata-only log; not legal certification." in html
    assert "regular security scans" in html
    assert "Run dependency, secret, static analysis, and access-review checks." in html
    assert "abnormal behavior and possible information leakage" in html
    assert "Expected scanner was unavailable during validation." in html
    metadata = extract_jsonld(output)
    assert metadata["memwiki:pageType"] == "agent_incident_log"
    assert metadata["memwiki:agentMemoryType"] == "agent_development_incident_log"
    assert metadata["memwiki:handlesPHI"] is True
    assert metadata["memwiki:hipaaLogVariant"] is True


def test_agent_cli_rejects_incident_log_with_secret_or_phi_value_fields(tmp_path: Path) -> None:
    assert invoke(tmp_path, "init").exit_code == 0
    incident_log = tmp_path / "incident-log.json"
    write_incident_log(incident_log, handles_phi=True)
    data = json.loads(incident_log.read_text(encoding="utf-8"))
    data["incidents"][0]["credential_value"] = "do-not-store"
    incident_log.write_text(json.dumps(data), encoding="utf-8")

    result = invoke(
        tmp_path,
        "agent",
        "render-incident-log",
        str(incident_log),
        "--output",
        str(tmp_path / "bad.html"),
    )

    assert result.exit_code != 0
    assert "must not store PHI, credentials, or secret values" in result.output


def test_agent_cli_rejects_unknown_slice_status(tmp_path: Path) -> None:
    assert invoke(tmp_path, "init").exit_code == 0
    queue = tmp_path / "slice-queue.json"
    write_slice_queue(queue)
    data = json.loads(queue.read_text(encoding="utf-8"))
    data["slices"][0]["status"] = "mystery"
    queue.write_text(json.dumps(data), encoding="utf-8")

    result = invoke(tmp_path, "agent", "render-run-state", str(queue), "--output", str(tmp_path / "bad.html"))

    assert result.exit_code != 0
    assert "Unknown slice status" in result.output


def test_agent_cli_rejects_invalid_worker_lease_paths(tmp_path: Path) -> None:
    assert invoke(tmp_path, "init").exit_code == 0
    queue = tmp_path / "slice-queue.json"
    write_slice_queue(queue)
    data = json.loads(queue.read_text(encoding="utf-8"))
    data["slices"][0]["worker_lease"]["assigned_paths"] = {"src": True}
    queue.write_text(json.dumps(data), encoding="utf-8")

    result = invoke(tmp_path, "agent", "render-run-state", str(queue), "--output", str(tmp_path / "bad.html"))

    assert result.exit_code != 0
    assert "assigned_paths" in result.output


def test_renderer_rejects_output_outside_workspace(tmp_path: Path) -> None:
    assert invoke(tmp_path, "init").exit_code == 0
    queue = tmp_path / "slice-queue.json"
    write_slice_queue(queue)
    outside = tmp_path.parent / "outside-agent-state.html"

    result = invoke(tmp_path, "agent", "render-run-state", str(queue), "--output", str(outside))

    assert result.exit_code == 1
    assert "Output path must remain inside the workspace" in result.output
    assert not outside.exists()


def test_renderer_rejects_protected_and_symlinked_outputs(tmp_path: Path) -> None:
    assert invoke(tmp_path, "init").exit_code == 0
    queue = tmp_path / "slice-queue.json"
    write_slice_queue(queue)
    protected = invoke(
        tmp_path,
        "agent",
        "render-run-state",
        str(queue),
        "--output",
        str(tmp_path / "wiki" / "state.html"),
    )
    assert protected.exit_code == 1
    assert "protected workspace storage" in protected.output

    outside = tmp_path.parent / "renderer-symlink-target"
    outside.mkdir(exist_ok=True)
    symlink = tmp_path / "artifacts-link"
    symlink.symlink_to(outside, target_is_directory=True)
    linked = invoke(
        tmp_path,
        "agent",
        "render-run-state",
        str(queue),
        "--output",
        str(symlink / "state.html"),
    )
    assert linked.exit_code == 1
    assert "symlink" in linked.output.lower() or "remain inside" in linked.output
    assert not (outside / "state.html").exists()
