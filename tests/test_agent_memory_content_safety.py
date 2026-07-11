from pathlib import Path

import pytest

from memwiki.content_safety import validate_safe_memory_content


@pytest.mark.parametrize(
    ("value", "rule_id"),
    [
        ({"summary": "password=actual-secret-value"}, "credential_assignment"),
        ({"summary": "Authorization: Bearer actual-secret-value"}, "bearer_token"),
        ({"summary": "token eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjMifQ.signaturevalue"}, "jwt"),
        ({"nested": [{"summary": "Patient SSN 123-45-6789"}]}, "ssn"),
        ({"summary": "-----BEGIN PRIVATE KEY-----"}, "private_key"),
    ],
)
def test_content_safety_rejects_high_confidence_sensitive_content(value: object, rule_id: str) -> None:
    with pytest.raises(ValueError, match=rule_id) as error:
        validate_safe_memory_content(value)

    assert "actual-secret-value" not in str(error.value)
    assert "123-45-6789" not in str(error.value)


@pytest.mark.parametrize(
    "value",
    [
        {"summary": "Synthetic clinical workflow validates medication reconciliation."},
        {"summary": "Use ${API_KEY} or <TOKEN> in local configuration examples."},
        {"summary": "Synthetic patient uses SSN 000-00-0000."},
        {"record_id": "client-synthetic-001", "sha256": "a" * 64},
        {"revision": "550e8400-e29b-41d4-a716-446655440000"},
        {"email": "developer@example.com"},
    ],
)
def test_content_safety_allows_placeholders_and_nonidentifying_development_content(value: object) -> None:
    validate_safe_memory_content(value)


def test_content_safety_rejects_oversized_strings_without_echoing_content() -> None:
    with pytest.raises(ValueError, match="content_size_limit"):
        validate_safe_memory_content({"summary": "x" * 100_001})


def test_proposal_sensitive_summary_is_rejected_before_workspace_writes(tmp_path: Path) -> None:
    from agentic_wiki import AgenticWikiWorkspace

    proposal = tmp_path / "proposal.json"
    proposal.write_text(
        '{"schema_version":1,"proposal_id":"p1","proposed_at":"2026-07-11T02:00:00Z",'
        '"records":[{"record_id":"r1","kind":"decision","summary":"api_key: actual-secret-value"}]}',
        encoding="utf-8",
    )
    wiki = AgenticWikiWorkspace(tmp_path / "wiki")
    wiki.init()

    with pytest.raises(ValueError, match="credential_assignment"):
        wiki.propose_agent_memory(proposal)

    assert not any((wiki.root / "raw").iterdir())
    assert not any((wiki.root / "drafts").iterdir())
