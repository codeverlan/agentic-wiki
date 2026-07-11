from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone

import pytest

from memwiki.coordinator_assignments import (
    AssignmentEnvelope,
    BoundedContextManifest,
    ReportContract,
)
from memwiki.coordinator_capabilities import Availability, CapabilityKind
from memwiki.coordinator_codex import (
    CodexCapabilityAdapter,
    CodexWorkerExecutor,
    DeterministicCodexHost,
    HostCapability,
    HostCapabilityRequest,
)
from memwiki.coordinator_slice_leases import SliceLeaseAuthority
from memwiki.coordinator_workers import WorkerDispatch, WorkerError, WorkerOutcome

NOW = datetime(2026, 7, 11, 16, tzinfo=timezone.utc)


def dispatch(number: int = 1) -> WorkerDispatch:
    assignment = AssignmentEnvelope.create(
        assignment_id=f"assignment-{number}",
        slice_id=f"AC-{number:03d}",
        worker_id=f"worker-{number}",
        context=BoundedContextManifest.create(
            artifacts=("context/spec.html",),
            context_hashes=(f"sha256:{hashlib.sha256(b'spec').hexdigest()}",),
            token_budget=500,
        ),
        owned_paths=(f"src/owned_{number}.py",),
        forbidden_paths=("src/shared.py",),
        base_revision="abc123",
        required_tests=("pytest",),
        permissions=("read", "write"),
        lease_policy="heartbeat",
        report_contract=ReportContract(
            destination=f"reports/worker-{number}.json",
            required_fields=("status", "summary"),
            schema_version=1,
        ),
        issued_at=NOW,
        acceptance_deadline=NOW + timedelta(minutes=1),
    )
    authority = SliceLeaseAuthority()
    offered = authority.acquire(
        assignment,
        attempt=1,
        coordinator_fencing_token=3,
        now=NOW,
        ttl=timedelta(minutes=5),
    )
    lease = authority.acknowledge(
        assignment.assignment_id,
        worker_id=assignment.worker_id,
        lease_token=offered.lease_token,
        coordinator_fencing_token=3,
        now=NOW,
    )
    return WorkerDispatch(assignment, lease, NOW + timedelta(minutes=2))


def test_codex_worker_maps_lifecycle_to_host_callbacks() -> None:
    host = DeterministicCodexHost(
        worker_results={"assignment-1": WorkerOutcome.success({"summary": "done"})}
    )
    adapter = CodexWorkerExecutor(host)

    handle = adapter.dispatch(dispatch(), now=NOW)
    assert host.worker_requests[0].assignment["assignment_id"] == "assignment-1"
    assert adapter.poll(handle, now=NOW).state == "finished"
    assert adapter.collect(handle, now=NOW).report == {"summary": "done"}
    assert adapter.collect(handle, now=NOW).kind == "success"


def test_codex_worker_enforces_six_live_handles_and_releases_capacity() -> None:
    host = DeterministicCodexHost()
    adapter = CodexWorkerExecutor(host, max_workers=6)
    handles = [adapter.dispatch(dispatch(index), now=NOW) for index in range(1, 7)]

    with pytest.raises(WorkerError, match="capacity"):
        adapter.dispatch(dispatch(7), now=NOW)

    adapter.cancel(handles[0], now=NOW, reason="stop")
    replacement = adapter.dispatch(dispatch(7), now=NOW)
    assert replacement.assignment_id == "assignment-7"


def test_codex_worker_rejects_invalid_capacity_and_unknown_handles() -> None:
    with pytest.raises(ValueError, match="between one and six"):
        CodexWorkerExecutor(DeterministicCodexHost(), max_workers=7)
    adapter = CodexWorkerExecutor(DeterministicCodexHost())
    from memwiki.coordinator_workers import WorkerHandle

    with pytest.raises(WorkerError, match="unknown"):
        adapter.poll(WorkerHandle("missing", "assignment-1"), now=NOW)


def capabilities() -> tuple[HostCapability, ...]:
    return (
        HostCapability("skill.test", "Test skill", "skill", ("test",), "available"),
        HostCapability("plugin.web", "Web plugin", "plugin", ("research",), "unavailable"),
        HostCapability("mcp.future", "Future MCP", "mcp", ("lookup",), "nonexistent"),
        HostCapability("browser.chrome", "Chrome", "browser", ("computer_use",), "available"),
        HostCapability("image.generate", "Image generation", "image", ("generate",), "available"),
        HostCapability("api.issue", "Issue API", "api", ("create_issue",), "available"),
    )


def test_capability_discovery_maps_all_codex_host_kinds_and_availability() -> None:
    host = DeterministicCodexHost(capabilities=capabilities())
    adapter = CodexCapabilityAdapter(host)
    discovered = adapter.discover()

    assert host.capability_invocations == []
    assert {item.kind for item in discovered} == {
        CapabilityKind.SKILL,
        CapabilityKind.PLUGIN,
        CapabilityKind.MCP,
        CapabilityKind.BROWSER,
        CapabilityKind.IMAGE_TOOL,
        CapabilityKind.API,
    }
    states = {item.identifier: item.availability for item in discovered}
    assert states["plugin.web"] is Availability.EXPECTED_UNAVAILABLE
    assert states["mcp.future"] is Availability.DOES_NOT_EXIST


def test_computer_use_requires_supervision_without_invoking_host() -> None:
    host = DeterministicCodexHost(capabilities=capabilities())
    adapter = CodexCapabilityAdapter(host)
    request = HostCapabilityRequest("browser.chrome", "computer_use", {"url": "https://example.test"})

    outcome = adapter.invoke(request, supervised=False)
    assert outcome.kind == "supervision"
    assert host.capability_invocations == []

    allowed = adapter.invoke(request, supervised=True)
    assert allowed.kind == "success"
    assert len(host.capability_invocations) == 1


def test_unavailable_and_nonexistent_have_distinct_noninvoking_outcomes() -> None:
    host = DeterministicCodexHost(capabilities=capabilities())
    adapter = CodexCapabilityAdapter(host)

    unavailable = adapter.invoke(HostCapabilityRequest("plugin.web", "research", {}))
    nonexistent = adapter.invoke(HostCapabilityRequest("mcp.future", "lookup", {}))
    assert unavailable.kind == "unavailable"
    assert unavailable.details["availability"] == "expected_unavailable"
    assert nonexistent.kind == "blocked"
    assert nonexistent.details["availability"] == "does_not_exist"
    assert nonexistent.details["future_opportunity"] is True
    assert host.capability_invocations == []


def test_capability_request_and_metadata_never_accept_credentials() -> None:
    host = DeterministicCodexHost(capabilities=capabilities())
    adapter = CodexCapabilityAdapter(host)
    with pytest.raises(ValueError, match="credential"):
        adapter.invoke(HostCapabilityRequest("api.issue", "create_issue", {"api_key": "secret"}))
    with pytest.raises(ValueError, match="credential"):
        HostCapability("api.bad", "Bad", "api", ("x",), "available", metadata={"token": "x"})


def test_fake_host_is_deterministic_and_callback_errors_are_typed() -> None:
    host = DeterministicCodexHost(
        capabilities=capabilities(),
        capability_results={"api.issue:create_issue": WorkerOutcome("retryable", "temporary", None)},
    )
    adapter = CodexCapabilityAdapter(host)
    request = HostCapabilityRequest("api.issue", "create_issue", {"title": "One"})
    assert adapter.invoke(request) == adapter.invoke(request)

    malformed = DeterministicCodexHost(
        capabilities=capabilities(), capability_results={"api.issue:create_issue": RuntimeError("boom")}
    )
    outcome = CodexCapabilityAdapter(malformed).invoke(request)
    assert outcome.kind == "retryable"
    assert outcome.reason == "Codex host capability callback failed"
