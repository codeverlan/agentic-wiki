from __future__ import annotations

from dataclasses import replace

import pytest

from memwiki.coordinator_worker_packets import (
    AcceptanceContract,
    AssignmentPacket,
    ContextEntry,
    ContextPacket,
    MemoryDelta,
    PacketAdmission,
    PacketValidationError,
    PrivacyAttestation,
    ResultPacket,
    Route,
    ValidationReceipt,
    WorkerReportPacket,
)


def _sha(value: str) -> str:
    return "sha256:" + "a" * 64


def _assignment() -> AssignmentPacket:
    return AssignmentPacket.create(
        assignment_id="assignment-1",
        worker_id="worker-1",
        owned_paths=("src/memwiki/example.py", "tests/test_example.py"),
        acceptance=AcceptanceContract.create(
            criteria=("Focused tests pass.",),
            required_validations=("uv run pytest tests/test_example.py",),
        ),
        route=Route.create(channel="coordinator", destination="worker-reports/assignment-1.json"),
        base_revision="a" * 40,
        privacy=PrivacyAttestation.create(data_profile="no", local_only=True, synthetic_only=False),
    )


def _context(assignment: AssignmentPacket) -> ContextPacket:
    return ContextPacket.create(
        context_id="context-1",
        assignment_id=assignment.assignment_id,
        assignment_hash=assignment.assignment_hash,
        entries=(ContextEntry.create(source_id="plan", revision="plan-rev-1", content_hash=_sha("plan")),),
        privacy=assignment.privacy,
    )


def _result(assignment: AssignmentPacket, context: ContextPacket) -> ResultPacket:
    return ResultPacket.create(
        result_id="result-1",
        assignment_id=assignment.assignment_id,
        assignment_hash=assignment.assignment_hash,
        context_id=context.context_id,
        context_hash=context.context_hash,
        base_revision=assignment.base_revision,
        result_revision="b" * 40,
        changed_paths={"src/memwiki/example.py": _sha("module")},
        validations=(
            ValidationReceipt.create(
                command="uv run pytest tests/test_example.py",
                outcome="passed",
                evidence_hash=_sha("test evidence"),
            ),
        ),
        privacy=assignment.privacy,
        memory_delta=MemoryDelta.not_applicable("No durable memory learned."),
    )


def _report(assignment: AssignmentPacket, context: ContextPacket, result: ResultPacket) -> WorkerReportPacket:
    return WorkerReportPacket.create(
        report_id="report-1",
        assignment_id=assignment.assignment_id,
        assignment_hash=assignment.assignment_hash,
        context_id=context.context_id,
        context_hash=context.context_hash,
        result_id=result.result_id,
        result_hash=result.result_hash,
        route=assignment.route,
        acceptance_status="accepted",
        privacy=assignment.privacy,
        memory_delta=result.memory_delta,
        summary="Completed the assigned change.",
    )


def test_packets_round_trip_strictly_and_are_content_addressed() -> None:
    assignment = _assignment()
    context = _context(assignment)
    result = _result(assignment, context)
    report = _report(assignment, context, result)

    for packet in (assignment, context, result, report):
        assert type(packet).from_dict(packet.to_dict()) == packet
        packet_hash = packet.to_dict()[packet.hash_field]
        assert isinstance(packet_hash, str) and packet_hash.startswith("sha256:")

    payload = assignment.to_dict()
    payload["extra"] = "not allowed"
    with pytest.raises(PacketValidationError, match="fields do not match"):
        AssignmentPacket.from_dict(payload)

    tampered = context.to_dict()
    tampered["context_hash"] = _sha("other")
    with pytest.raises(PacketValidationError, match="does not match contents"):
        ContextPacket.from_dict(tampered)


def test_admission_accepts_linked_owned_and_validated_packets() -> None:
    assignment = _assignment()
    context = _context(assignment)
    result = _result(assignment, context)
    report = _report(assignment, context, result)

    decision = PacketAdmission().admit(assignment, context, result, report)

    assert decision.admitted is True
    assert decision.reasons == ()
    assert decision.report_hash == report.report_hash
    assert type(decision).from_dict(decision.to_dict()) == decision


def test_admission_recomputes_packet_hashes_before_accepting() -> None:
    assignment = _assignment()
    context = _context(assignment)
    result = _result(assignment, context)
    report = replace(_report(assignment, context, result), summary="Tampered after hashing.")

    decision = PacketAdmission().admit(assignment, context, result, report)

    assert decision.admitted is False
    assert "report hash does not match contents" in decision.reasons


@pytest.mark.parametrize(
    ("result", "report", "reason"),
    [
        (
            lambda assignment, context, result, report: replace(
                result, changed_paths=(("README.md", _sha("outside")),)
            ).rehash(),
            lambda assignment, context, result, report: report,
            "changed path is outside assignment ownership",
        ),
        (
            lambda assignment, context, result, report: replace(result, base_revision="c" * 40).rehash(),
            lambda assignment, context, result, report: report,
            "result base revision does not match assignment",
        ),
        (
            lambda assignment, context, result, report: result,
            lambda assignment, context, result, report: replace(
                report, route=Route.create(channel="coordinator", destination="other/report.json")
            ).rehash(),
            "report route does not match assignment",
        ),
        (
            lambda assignment, context, result, report: result,
            lambda assignment, context, result, report: replace(report, acceptance_status="rejected").rehash(),
            "report acceptance status is not accepted",
        ),
    ],
)
def test_admission_rejects_scope_revision_route_and_acceptance_violations(
    result: object, report: object, reason: str
) -> None:
    assignment = _assignment()
    context = _context(assignment)
    original_result = _result(assignment, context)
    original_report = _report(assignment, context, original_result)
    mutated_result = result(assignment, context, original_result, original_report)  # type: ignore[operator]
    mutated_report = report(assignment, context, mutated_result, original_report)  # type: ignore[operator]

    decision = PacketAdmission().admit(assignment, context, mutated_result, mutated_report)

    assert decision.admitted is False
    assert reason in decision.reasons


def test_admission_rejects_missing_required_validation_privacy_and_memory_mismatch() -> None:
    assignment = _assignment()
    context = _context(assignment)
    result = replace(
        _result(assignment, context),
        validations=(),
        privacy=PrivacyAttestation.create(data_profile="no", local_only=False, synthetic_only=False),
        memory_delta=MemoryDelta.delta(({"claim": "durable fact", "source_hash": _sha("source")},)),
    ).rehash()
    report = replace(
        _report(assignment, context, result),
        memory_delta=MemoryDelta.not_applicable("incorrect"),
    ).rehash()

    decision = PacketAdmission().admit(assignment, context, result, report)

    assert decision.admitted is False
    assert "required validation is missing: uv run pytest tests/test_example.py" in decision.reasons
    assert "result privacy policy is invalid" in decision.reasons
    assert "report memory delta does not match result" in decision.reasons


def test_contract_rejects_unconfined_paths_and_invalid_memory_delta_shape() -> None:
    with pytest.raises(PacketValidationError, match="confined relative path"):
        AssignmentPacket.create(
            assignment_id="assignment-1",
            worker_id="worker-1",
            owned_paths=("../outside",),
            acceptance=AcceptanceContract.create(criteria=("x",), required_validations=("test",)),
            route=Route.create(channel="coordinator", destination="reports/result.json"),
            base_revision="a" * 40,
            privacy=PrivacyAttestation.create(data_profile="no", local_only=True, synthetic_only=False),
        )
    with pytest.raises(PacketValidationError, match="not_applicable memory delta requires a reason"):
        MemoryDelta.create(status="not_applicable", entries=(), reason="")
