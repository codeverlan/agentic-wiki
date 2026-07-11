from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from memwiki.coordinator_assignments import (
    AssignmentAuthority,
    AssignmentEnvelope,
    AssignmentError,
    AssignmentResponse,
    BoundedContextManifest,
    ReportContract,
)

NOW = datetime(2026, 7, 11, 12, 0, tzinfo=timezone.utc)


def assignment(*, assignment_id: str = "assignment-1") -> AssignmentEnvelope:
    context = BoundedContextManifest.create(
        artifacts=("plan.json", "docs/architecture.html"),
        context_hashes=("sha256:plan", "sha256:architecture"),
        token_budget=8_000,
    )
    report = ReportContract(
        destination="reports/AC-011/assignment-1.json",
        required_fields=("changed_files", "tests", "blockers", "memory_delta"),
        schema_version=1,
    )
    return AssignmentEnvelope.create(
        assignment_id=assignment_id,
        slice_id="AC-011",
        worker_id="worker-1",
        context=context,
        owned_paths=("src/memwiki/coordinator_assignments.py", "tests/test_coordinator_assignments.py"),
        forbidden_paths=("wiki", ".git"),
        base_revision="0123456789abcdef",
        required_tests=("uv run pytest tests/test_coordinator_assignments.py -q",),
        permissions=("read_workspace", "write_owned_paths", "run_tests"),
        lease_policy="acknowledge-before-write",
        report_contract=report,
        issued_at=NOW,
        acceptance_deadline=NOW + timedelta(minutes=5),
    )


def test_assignment_is_immutable_and_hash_is_deterministic() -> None:
    first = assignment()
    second = assignment()

    assert first.assignment_hash == second.assignment_hash
    assert first.to_dict() == second.to_dict()
    with pytest.raises(AttributeError):
        first.worker_id = "other"  # type: ignore[misc]


@pytest.mark.parametrize(
    ("field", "value", "match"),
    [
        ("base_revision", "", "base_revision"),
        ("owned_paths", (), "owned_paths"),
        ("required_tests", (), "required_tests"),
        ("permissions", (), "permissions"),
        ("lease_policy", "", "lease_policy"),
    ],
)
def test_assignment_rejects_unbounded_or_incomplete_contracts(
    field: str, value: object, match: str
) -> None:
    values = assignment().creation_values()
    values[field] = value

    with pytest.raises(ValueError, match=match):
        AssignmentEnvelope.create(**values)


def test_assignment_rejects_missing_context_and_stale_deadline() -> None:
    with pytest.raises(ValueError, match="context artifacts"):
        BoundedContextManifest.create(artifacts=(), context_hashes=(), token_budget=100)

    values = assignment().creation_values()
    values["acceptance_deadline"] = NOW
    with pytest.raises(ValueError, match="acceptance_deadline"):
        AssignmentEnvelope.create(**values)


def test_assignment_rejects_overlapping_owned_and_forbidden_paths() -> None:
    values = assignment().creation_values()
    values["forbidden_paths"] = ("src/memwiki",)

    with pytest.raises(ValueError, match="overlap"):
        AssignmentEnvelope.create(**values)


def test_tampered_assignment_hash_is_rejected() -> None:
    payload = assignment().to_dict()
    payload["base_revision"] = "different"

    with pytest.raises(ValueError, match="assignment_hash"):
        AssignmentEnvelope.from_dict(payload)


def test_acknowledgement_must_match_assignment_worker_and_hash() -> None:
    authority = AssignmentAuthority()
    packet = authority.issue(assignment())

    malformed = AssignmentResponse.acknowledge(
        assignment_id=packet.assignment_id,
        worker_id="other-worker",
        assignment_hash=packet.assignment_hash,
        responded_at=NOW + timedelta(seconds=10),
    )
    with pytest.raises(AssignmentError, match="worker"):
        authority.respond(malformed)

    accepted = authority.respond(
        AssignmentResponse.acknowledge(
            assignment_id=packet.assignment_id,
            worker_id=packet.worker_id,
            assignment_hash=packet.assignment_hash,
            responded_at=NOW + timedelta(seconds=10),
        )
    )
    assert accepted.status == "acknowledged"


def test_malformed_response_decision_is_rejected() -> None:
    with pytest.raises(ValueError, match="decision"):
        AssignmentResponse(
            assignment_id="assignment-1",
            worker_id="worker-1",
            assignment_hash="sha256:value",
            decision="maybe",
            responded_at=NOW,
            reason=None,
        )


def test_response_before_assignment_issue_is_rejected() -> None:
    authority = AssignmentAuthority()
    packet = authority.issue(assignment())

    with pytest.raises(AssignmentError, match="before assignment was issued"):
        authority.respond(
            AssignmentResponse.acknowledge(
                assignment_id=packet.assignment_id,
                worker_id=packet.worker_id,
                assignment_hash=packet.assignment_hash,
                responded_at=NOW - timedelta(seconds=1),
            )
        )


def test_worker_can_explicitly_reject_with_reason_and_ownership_is_released() -> None:
    authority = AssignmentAuthority()
    packet = authority.issue(assignment())

    rejected = authority.respond(
        AssignmentResponse.reject(
            assignment_id=packet.assignment_id,
            worker_id=packet.worker_id,
            assignment_hash=packet.assignment_hash,
            responded_at=NOW + timedelta(seconds=5),
            reason="required capability unavailable",
        )
    )

    assert rejected.status == "rejected"
    assert authority.owner_of("src/memwiki/coordinator_assignments.py") is None


def test_late_ack_is_rejected_and_assignment_is_requeued_without_duplicate_ownership() -> None:
    authority = AssignmentAuthority()
    original = authority.issue(assignment())
    expired = authority.expire_pending(now=NOW + timedelta(minutes=6))

    assert expired == (original.assignment_id,)
    assert authority.owner_of("src/memwiki/coordinator_assignments.py") is None

    with pytest.raises(AssignmentError, match="not pending"):
        authority.respond(
            AssignmentResponse.acknowledge(
                assignment_id=original.assignment_id,
                worker_id=original.worker_id,
                assignment_hash=original.assignment_hash,
                responded_at=NOW + timedelta(minutes=6),
            )
        )

    replacement = replace(
        assignment(assignment_id="assignment-2"),
        worker_id="worker-2",
        issued_at=NOW + timedelta(minutes=6),
        acceptance_deadline=NOW + timedelta(minutes=11),
    ).rehash()
    authority.issue(replacement)
    assert authority.owner_of("src/memwiki/coordinator_assignments.py") == "assignment-2"


def test_live_path_ownership_cannot_be_duplicated() -> None:
    authority = AssignmentAuthority()
    authority.issue(assignment())
    duplicate = replace(assignment(assignment_id="assignment-2"), worker_id="worker-2").rehash()

    with pytest.raises(AssignmentError, match="already owned"):
        authority.issue(duplicate)
