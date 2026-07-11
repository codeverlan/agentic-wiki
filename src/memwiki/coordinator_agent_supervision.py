from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, replace
from datetime import datetime
from enum import Enum
from typing import Dict, Sequence, Tuple

from memwiki.coordinator_agents import AgentProduct, AgentVisibility, SurfaceKind
from memwiki.coordinator_supervision import (
    SupervisionCategory,
    SupervisionQueue,
    SupervisionRecord,
)


class SupervisedAction(str, Enum):
    AUTHORIZATION = "authorization"
    LOGIN = "login"
    CONSENT = "consent"
    COMPUTER_USE = "computer_use"
    PUBLICATION = "publication"
    PURCHASE = "purchase"
    DESTRUCTIVE_ACTION = "destructive_action"
    AMBIGUOUS_HANDOFF = "ambiguous_handoff"


_CATEGORY = {
    SupervisedAction.AUTHORIZATION: SupervisionCategory.AUTHORIZATION,
    SupervisedAction.LOGIN: SupervisionCategory.AUTHORIZATION,
    SupervisedAction.CONSENT: SupervisionCategory.AUTHORIZATION,
    SupervisedAction.COMPUTER_USE: SupervisionCategory.SAFETY,
    SupervisedAction.PUBLICATION: SupervisionCategory.EXTERNAL_PUBLICATION,
    SupervisedAction.PURCHASE: SupervisionCategory.FINANCIAL,
    SupervisedAction.DESTRUCTIVE_ACTION: SupervisionCategory.DESTRUCTIVE,
    SupervisedAction.AMBIGUOUS_HANDOFF: SupervisionCategory.AUTHORIZATION,
}
_HASH = re.compile(r"^sha256:[0-9a-f]{64}$")


def _nonempty(value: str, label: str) -> str:
    if not value.strip():
        raise ValueError(f"{label} must be non-empty")
    return value


def _values(values: Sequence[str], label: str) -> Tuple[str, ...]:
    result = tuple(_nonempty(item, label) for item in values)
    if len(result) != len(set(result)):
        raise ValueError(f"duplicate {label}")
    return result


@dataclass(frozen=True)
class AgentSupervisionRequest:
    invocation_key: str
    request_hash: str
    slice_id: str
    agent_id: str
    product: AgentProduct
    visibility: AgentVisibility
    surface_kind: SurfaceKind
    action: SupervisedAction
    operation: str
    evidence_ids: Tuple[str, ...]
    delegated_by: Tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for value, label in (
            (self.invocation_key, "invocation key"),
            (self.slice_id, "slice id"),
            (self.agent_id, "agent id"),
            (self.operation, "operation"),
        ):
            _nonempty(value, label)
        if not _HASH.fullmatch(self.request_hash):
            raise ValueError("request hash must be a sha256 digest")
        _values(self.evidence_ids, "evidence id")
        _values(self.delegated_by, "delegating agent")


@dataclass(frozen=True)
class AgentSupervisionRecord:
    item_id: str
    invocation_key: str
    request_hash: str
    slice_id: str
    agent_id: str
    product: AgentProduct
    visibility: AgentVisibility
    surface_kind: SurfaceKind
    action: SupervisedAction
    delegated_by: Tuple[str, ...]
    resume_instructions: str
    queue_record: SupervisionRecord
    authorized: bool = False


@dataclass(frozen=True)
class AgentPresentationDecision:
    present_now: bool
    pending_item_ids: Tuple[str, ...]
    active_slice_ids: Tuple[str, ...]
    cancel_active_work: bool = False


class AgentSupervisionGate:
    """Binds mandatory human supervision to exact cross-agent invocations."""

    def __init__(self, queue: SupervisionQueue) -> None:
        self.queue = queue
        self._records: Dict[str, AgentSupervisionRecord] = {}
        self._by_invocation: Dict[str, str] = {}

    def require(
        self, request: AgentSupervisionRequest, *, recorded_at: datetime
    ) -> AgentSupervisionRecord:
        existing_id = self._by_invocation.get(request.invocation_key)
        if existing_id is not None:
            existing = self._records[existing_id]
            if not self._matches(existing, request):
                raise ValueError("invocation key is bound to another request")
            return existing

        item_id = self._item_id(request.invocation_key)
        evidence = _values(
            (*request.evidence_ids, request.request_hash), "evidence id"
        )
        delegation = ""
        if request.delegated_by:
            delegation = "; delegated through " + ", ".join(request.delegated_by)
        summary = (
            f"Supervise {request.action.value} for {request.agent_id} "
            f"on {request.product.value}{delegation}"
        )
        queue_record = self.queue.open(
            item_id=item_id,
            category=_CATEGORY[request.action],
            summary=summary,
            choices=self._choices(request.action),
            evidence_ids=evidence,
            affected_slice_ids=(request.slice_id,),
            resumable=True,
            created_at=recorded_at,
        )
        record = AgentSupervisionRecord(
            item_id=item_id,
            invocation_key=request.invocation_key,
            request_hash=request.request_hash,
            slice_id=request.slice_id,
            agent_id=request.agent_id,
            product=request.product,
            visibility=request.visibility,
            surface_kind=request.surface_kind,
            action=request.action,
            delegated_by=request.delegated_by,
            resume_instructions=self._resume_instructions(request),
            queue_record=queue_record,
        )
        self._records[item_id] = record
        self._by_invocation[request.invocation_key] = item_id
        return record

    def authorize(
        self,
        item_id: str,
        *,
        invocation_key: str,
        disposition: str,
        evidence_ids: Sequence[str],
        recorded_at: datetime,
    ) -> AgentSupervisionRecord:
        try:
            record = self._records[item_id]
        except KeyError:
            raise ValueError(f"unknown agent supervision item: {item_id}") from None
        if record.invocation_key != invocation_key:
            raise ValueError("authorization must match the exact invocation")
        queue_record = self.queue.resolve(
            item_id,
            disposition=disposition,
            evidence_ids=evidence_ids,
            recorded_at=recorded_at,
        )
        updated = replace(record, queue_record=queue_record, authorized=True)
        self._records[item_id] = updated
        return updated

    def is_authorized(self, invocation_key: str, request_hash: str) -> bool:
        item_id = self._by_invocation.get(invocation_key)
        if item_id is None:
            return False
        record = self._records[item_id]
        return record.authorized and record.request_hash == request_hash

    def user_returned(
        self,
        *,
        recorded_at: datetime,
        active_slice_ids: Sequence[str],
        at_safe_boundary: bool,
    ) -> AgentPresentationDecision:
        active = _values(active_slice_ids, "active slice id")
        self.queue.record_user_return(
            recorded_at=recorded_at, active_slice_ids=active
        )
        return self.presentation_decision(
            recorded_at=recorded_at,
            at_safe_boundary=at_safe_boundary,
            active_slice_ids=active,
        )

    def presentation_decision(
        self,
        *,
        recorded_at: datetime,
        at_safe_boundary: bool,
        active_slice_ids: Sequence[str] = (),
    ) -> AgentPresentationDecision:
        active = _values(active_slice_ids, "active slice id")
        pending = tuple(
            item.item_id for item in self.queue.pending_for_presentation(recorded_at)
        )
        return AgentPresentationDecision(
            present_now=bool(pending) and self.queue.presentation_due and at_safe_boundary,
            pending_item_ids=pending,
            active_slice_ids=active,
            cancel_active_work=False,
        )

    @staticmethod
    def _item_id(invocation_key: str) -> str:
        digest = hashlib.sha256(invocation_key.encode()).hexdigest()[:20]
        return f"agent-supervision-{digest}"

    @staticmethod
    def _choices(action: SupervisedAction) -> Tuple[str, ...]:
        if action is SupervisedAction.AMBIGUOUS_HANDOFF:
            return ("complete handoff", "clarify handoff", "cancel handoff")
        return ("authorize once", "decline")

    @staticmethod
    def _resume_instructions(request: AgentSupervisionRequest) -> str:
        scope = request.visibility.value
        if request.product is AgentProduct.CHATGPT:
            if request.surface_kind is SurfaceKind.CONVERSATIONAL_HANDOFF:
                return (
                    f"Open the {scope} ChatGPT agent conversation, complete or clarify "
                    "the handoff, then return with its result and this invocation key."
                )
            return (
                f"Open ChatGPT and resume the {scope} agent surface for this exact "
                "invocation; return after the requested consent or action is complete."
            )
        if request.product is AgentProduct.CODEX:
            return (
                f"Resume the {scope} Codex agent surface for this exact invocation at "
                "the next safe boundary and record the user's decision."
            )
        return (
            f"Resume the {scope} external agent surface for this exact invocation and "
            "record evidence of the user's decision."
        )

    @staticmethod
    def _matches(
        record: AgentSupervisionRecord, request: AgentSupervisionRequest
    ) -> bool:
        return (
            record.request_hash == request.request_hash
            and record.slice_id == request.slice_id
            and record.agent_id == request.agent_id
            and record.product is request.product
            and record.visibility is request.visibility
            and record.surface_kind is request.surface_kind
            and record.action is request.action
            and record.delegated_by == request.delegated_by
        )
