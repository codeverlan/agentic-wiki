import json
from dataclasses import replace
from pathlib import Path

import pytest

from agentic_wiki import AgenticWikiWorkspace, OperationContext
from memwiki.manifest import read_jsonl
from memwiki.policy import PolicyError, VerifiedAuthority, verify_coordinator_authority


class FakeAuthorityVerifier:
    def verify(
        self,
        token: str,
        *,
        operation: str,
        workspace: Path,
        session_id: str,
    ) -> VerifiedAuthority:
        if token != "host-secret-token":
            raise PolicyError("Authority credential verification failed")
        return VerifiedAuthority(
            subject_id="coordinator-001",
            roles=("coordinator",),
            issuer="test-host",
            credential_id="credential-001",
            allowed_operations=("promote_agent_memory",),
            workspace=str(workspace),
            session_id=session_id,
        )


def _agent_memory_draft(wiki: AgenticWikiWorkspace, root: Path) -> str:
    event = root / "event.json"
    event.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "event_id": "event-001",
                "event_type": "observation",
                "occurred_at": "2026-07-11T02:00:00-04:00",
                "record": {"record_id": "record-001", "kind": "decision", "summary": "Synthetic decision."},
            }
        ),
        encoding="utf-8",
    )
    return wiki.observe_agent_memory(event).draft_id


def test_self_asserted_coordinator_cannot_promote_agent_memory(tmp_path: Path) -> None:
    wiki = AgenticWikiWorkspace(tmp_path)
    wiki.init()
    draft_id = _agent_memory_draft(wiki, tmp_path)
    context = OperationContext(
        actor_id="coordinator-001",
        actor_role="coordinator",
        purpose_of_use="agent-memory-promotion",
        session_id="session-001",
    )

    with pytest.raises(PolicyError, match="Verified coordinator authority is required"):
        wiki.promote(draft_id, context=context)


def test_host_verified_coordinator_can_promote_without_persisting_token(tmp_path: Path) -> None:
    wiki = AgenticWikiWorkspace(tmp_path, authority_verifier=FakeAuthorityVerifier())
    wiki.init()
    draft_id = _agent_memory_draft(wiki, tmp_path)
    context = OperationContext(
        actor_id="coordinator-001",
        actor_role="coordinator",
        purpose_of_use="agent-memory-promotion",
        session_id="session-001",
        authority_token="host-secret-token",
    )

    result = wiki.promote(draft_id, context=context)

    assert result.promoted_pages == 1
    events_text = (tmp_path / "manifests/events.jsonl").read_text(encoding="utf-8")
    assert "host-secret-token" not in events_text
    promotion = [
        event
        for event in read_jsonl(tmp_path / "manifests/events.jsonl")
        if event["event_type"] == "promote"
    ][-1]
    assert promotion["details"]["verified_authority"]["issuer"] == "test-host"
    assert promotion["details"]["verified_authority"]["credential_id"] == "credential-001"


def test_verified_identity_must_match_context_actor(tmp_path: Path) -> None:
    wiki = AgenticWikiWorkspace(tmp_path, authority_verifier=FakeAuthorityVerifier())
    wiki.init()
    draft_id = _agent_memory_draft(wiki, tmp_path)
    context = OperationContext(
        actor_id="different-actor",
        actor_role="coordinator",
        purpose_of_use="agent-memory-promotion",
        session_id="session-001",
        authority_token="host-secret-token",
    )

    with pytest.raises(PolicyError, match="Verified authority identity does not match operation context"):
        wiki.promote(draft_id, context=context)


def test_invalid_authority_token_is_not_disclosed(tmp_path: Path) -> None:
    wiki = AgenticWikiWorkspace(tmp_path, authority_verifier=FakeAuthorityVerifier())
    wiki.init()
    draft_id = _agent_memory_draft(wiki, tmp_path)
    context = OperationContext(
        actor_id="coordinator-001",
        actor_role="coordinator",
        purpose_of_use="agent-memory-promotion",
        session_id="session-001",
        authority_token="invalid-sensitive-token",
    )

    with pytest.raises(PolicyError) as error:
        wiki.promote(draft_id, context=context)

    assert "invalid-sensitive-token" not in str(error.value)
    assert "authority_token" not in context.to_dict()
    assert "invalid-sensitive-token" not in repr(context)


def test_agent_memory_authority_cannot_be_bypassed_by_relabeling_draft(tmp_path: Path) -> None:
    wiki = AgenticWikiWorkspace(tmp_path)
    wiki.init()
    draft_id = _agent_memory_draft(wiki, tmp_path)
    draft_metadata = tmp_path / "drafts" / draft_id / "draft.json"
    draft_metadata.write_text(json.dumps({"draft_id": draft_id, "kind": "ordinary"}), encoding="utf-8")

    with pytest.raises(PolicyError, match="Verified coordinator authority is required"):
        wiki.promote(draft_id)


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"roles": ("worker",)}, "does not permit"),
        ({"allowed_operations": ("other",)}, "does not permit"),
        ({"workspace": "/different/workspace"}, "scope does not match"),
        ({"session_id": "different-session"}, "scope does not match"),
    ],
)
def test_verified_authority_scope_is_enforced(tmp_path: Path, change: dict[str, object], message: str) -> None:
    context = OperationContext(
        actor_id="coordinator-001",
        actor_role="coordinator",
        purpose_of_use="agent-memory-promotion",
        session_id="session-001",
        authority_token="host-secret-token",
    )
    authority = VerifiedAuthority(
        subject_id="coordinator-001",
        roles=("coordinator",),
        issuer="test-host",
        credential_id="credential-001",
        allowed_operations=("promote_agent_memory",),
        workspace=str(tmp_path),
        session_id="session-001",
    )

    class StaticVerifier:
        def verify(self, *args: object, **kwargs: object) -> VerifiedAuthority:
            return replace(authority, **change)

    with pytest.raises(PolicyError, match=message):
        verify_coordinator_authority(context, StaticVerifier(), "promote_agent_memory", tmp_path)
