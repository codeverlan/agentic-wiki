from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any, Dict, Mapping, Sequence, Tuple


class DisclosureLabel(str, Enum):
    PUBLIC = "public"
    INTERNAL = "internal"
    SENSITIVE = "sensitive"
    PHI = "phi"
    CREDENTIAL = "credential"


class AdmissionTarget(str, Enum):
    DRAFT = "draft"
    EVIDENCE = "evidence"
    DECISION = "decision"
    DESIGN = "design"
    CLAIM = "claim"


class AdmissionStatus(str, Enum):
    ADMITTED = "admitted"
    QUARANTINED = "quarantined"
    REJECTED = "rejected"


_CREDENTIAL_KEYS = {
    "access_token",
    "api_key",
    "authorization",
    "credential",
    "credentials",
    "password",
    "private_key",
    "refresh_token",
    "secret",
    "token",
}
_INJECTION_PATTERNS = (
    (
        "ignore-previous-instructions",
        re.compile(r"\bignore\s+(?:all\s+)?previous\s+instructions?\b", re.IGNORECASE),
    ),
    (
        "system-prompt-exfiltration",
        re.compile(r"\b(?:reveal|print|show|expose)\b.{0,48}\bsystem\s+prompt\b", re.IGNORECASE),
    ),
    (
        "role-override",
        re.compile(r"\b(?:you are now|act as|developer message says)\b", re.IGNORECASE),
    ),
    (
        "tool-instruction",
        re.compile(r"\b(?:run|execute|call)\b.{0,32}\b(?:tool|command|shell)\b", re.IGNORECASE),
    ),
)


def _canonical(value: object, label: str) -> bytes:
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise ValueError(f"{label} must be JSON serializable") from error


def _hash(value: object) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value, "hash content")).hexdigest()


def _nonempty(value: str, label: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be non-empty")


def _tokens(value: object) -> int:
    return max(1, math.ceil(len(_canonical(value, "context content")) / 4))


def _normalized_key(value: str) -> str:
    return value.casefold().replace("-", "_").replace(" ", "_")


def _contains_credential_key(value: object) -> bool:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            normalized = _normalized_key(str(key))
            if normalized in _CREDENTIAL_KEYS or normalized.endswith(
                ("_key", "_password", "_secret", "_token")
            ):
                return True
            if _contains_credential_key(nested):
                return True
    elif isinstance(value, (list, tuple)):
        return any(_contains_credential_key(item) for item in value)
    return False


def _contains_value(value: object, canaries: Sequence[str]) -> bool:
    encoded = _canonical(value, "content").decode("utf-8")
    return any(canary and canary in encoded for canary in canaries)


def _strings(value: object) -> Tuple[str, ...]:
    found = []

    def visit(item: object) -> None:
        if isinstance(item, str):
            found.append(item)
        elif isinstance(item, Mapping):
            for nested in item.values():
                visit(nested)
        elif isinstance(item, (list, tuple)):
            for nested in item:
                visit(nested)

    visit(value)
    return tuple(found)


@dataclass(frozen=True)
class AgentContextQualification:
    agent_id: str
    stable_identity: str
    qualification_receipt_id: str
    qualified: bool
    local_only: bool
    phi_eligible: bool

    def __post_init__(self) -> None:
        _nonempty(self.agent_id, "agent id")
        _nonempty(self.stable_identity, "stable identity")
        _nonempty(self.qualification_receipt_id, "qualification receipt id")


@dataclass(frozen=True)
class ContextSource:
    source_id: str
    content: object
    revision: str
    disclosure: DisclosureLabel = DisclosureLabel.INTERNAL
    synthetic: bool = False

    def __post_init__(self) -> None:
        _nonempty(self.source_id, "source id")
        _nonempty(self.revision, "source revision")
        _canonical(self.content, "source content")
        if not isinstance(self.disclosure, DisclosureLabel):
            raise ValueError("invalid disclosure label")

    @property
    def source_hash(self) -> str:
        return _hash(
            {
                "source_id": self.source_id,
                "content": self.content,
                "revision": self.revision,
                "disclosure": self.disclosure.value,
                "synthetic": self.synthetic,
            }
        )

    @property
    def token_count(self) -> int:
        return _tokens(self.content)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source_id": self.source_id,
            "content": self.content,
            "revision": self.revision,
            "disclosure": self.disclosure.value,
            "synthetic": self.synthetic,
            "source_hash": self.source_hash,
            "token_count": self.token_count,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ContextSource":
        required = {
            "source_id",
            "content",
            "revision",
            "disclosure",
            "synthetic",
            "source_hash",
            "token_count",
        }
        if set(value) != required:
            raise ValueError("context source fields do not match schema")
        result = cls(
            source_id=str(value["source_id"]),
            content=value["content"],
            revision=str(value["revision"]),
            disclosure=DisclosureLabel(str(value["disclosure"])),
            synthetic=bool(value["synthetic"]),
        )
        if result.source_hash != value["source_hash"]:
            raise ValueError("source hash does not match content")
        if result.token_count != value["token_count"]:
            raise ValueError("source token count does not match content")
        return result


@dataclass(frozen=True)
class ContextRequest:
    purpose: str
    source_ids: Tuple[str, ...]
    max_tokens: int

    def __post_init__(self) -> None:
        _nonempty(self.purpose, "context purpose")
        if not self.source_ids or any(not item.strip() for item in self.source_ids):
            raise ValueError("context requires explicit source identifiers")
        if "*" in self.source_ids or len(set(self.source_ids)) != len(self.source_ids):
            raise ValueError("context requires unique explicit source identifiers")
        if isinstance(self.max_tokens, bool) or self.max_tokens <= 0:
            raise ValueError("max tokens must be a positive integer")


@dataclass(frozen=True)
class AgentContextBundle:
    bundle_id: str
    bundle_hash: str
    purpose: str
    agent_id: str
    stable_identity: str
    qualification_receipt_id: str
    max_tokens: int
    token_count: int
    sources: Tuple[ContextSource, ...]

    @property
    def source_hashes(self) -> Tuple[str, ...]:
        return tuple(item.source_hash for item in self.sources)

    def _payload(self) -> Dict[str, Any]:
        return {
            "purpose": self.purpose,
            "agent_id": self.agent_id,
            "stable_identity": self.stable_identity,
            "qualification_receipt_id": self.qualification_receipt_id,
            "max_tokens": self.max_tokens,
            "token_count": self.token_count,
            "sources": [item.to_dict() for item in self.sources],
        }

    def __post_init__(self) -> None:
        expected_hash = _hash(self._payload())
        if self.bundle_hash != expected_hash:
            raise ValueError("bundle hash does not match content")
        if self.bundle_id != "context-" + expected_hash.removeprefix("sha256:")[:24]:
            raise ValueError("bundle id does not match content")
        if self.token_count != sum(item.token_count for item in self.sources):
            raise ValueError("bundle token count does not match sources")
        if self.token_count > self.max_tokens:
            raise ValueError("bundle exceeds token bound")

    def to_dict(self) -> Dict[str, Any]:
        return {"bundle_id": self.bundle_id, "bundle_hash": self.bundle_hash, **self._payload()}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "AgentContextBundle":
        required = {
            "bundle_id",
            "bundle_hash",
            "purpose",
            "agent_id",
            "stable_identity",
            "qualification_receipt_id",
            "max_tokens",
            "token_count",
            "sources",
        }
        if set(value) != required:
            raise ValueError("context bundle fields do not match schema")
        raw_sources = value["sources"]
        if not isinstance(raw_sources, list):
            raise ValueError("bundle sources must be a list")
        return cls(
            bundle_id=str(value["bundle_id"]),
            bundle_hash=str(value["bundle_hash"]),
            purpose=str(value["purpose"]),
            agent_id=str(value["agent_id"]),
            stable_identity=str(value["stable_identity"]),
            qualification_receipt_id=str(value["qualification_receipt_id"]),
            max_tokens=int(value["max_tokens"]),
            token_count=int(value["token_count"]),
            sources=tuple(ContextSource.from_dict(item) for item in raw_sources),
        )


class ContextPackager:
    def package(
        self,
        sources: Sequence[ContextSource],
        request: ContextRequest,
        qualification: AgentContextQualification,
        *,
        supplemental_source_ids: Sequence[str] = (),
    ) -> AgentContextBundle:
        if not qualification.qualified:
            raise ValueError("context may only be packaged for a qualified agent")
        if supplemental_source_ids:
            raise ValueError("supplemental context was not explicitly requested")
        by_id = {item.source_id: item for item in sources}
        if len(by_id) != len(sources):
            raise ValueError("context source identifiers must be unique")
        missing = [item for item in request.source_ids if item not in by_id]
        if missing:
            raise ValueError("requested context source is unavailable: " + ",".join(missing))
        selected = tuple(by_id[item] for item in request.source_ids)
        for item in selected:
            if item.disclosure is DisclosureLabel.CREDENTIAL:
                raise ValueError("credential context cannot be disclosed")
            if _contains_credential_key(item.content):
                raise ValueError("credential content cannot be disclosed")
            if item.disclosure is DisclosureLabel.PHI:
                if not qualification.phi_eligible:
                    raise ValueError("agent is not PHI-eligible")
                if not qualification.local_only:
                    raise ValueError("PHI context requires a local-only agent")
                if not item.synthetic:
                    raise ValueError("only synthetic PHI context may be packaged")
        token_count = sum(item.token_count for item in selected)
        if token_count > request.max_tokens:
            raise ValueError("selected context exceeds token bound")
        payload = {
            "purpose": request.purpose,
            "agent_id": qualification.agent_id,
            "stable_identity": qualification.stable_identity,
            "qualification_receipt_id": qualification.qualification_receipt_id,
            "max_tokens": request.max_tokens,
            "token_count": token_count,
            "sources": [item.to_dict() for item in selected],
        }
        bundle_hash = _hash(payload)
        return AgentContextBundle(
            bundle_id="context-" + bundle_hash.removeprefix("sha256:")[:24],
            bundle_hash=bundle_hash,
            purpose=request.purpose,
            agent_id=qualification.agent_id,
            stable_identity=qualification.stable_identity,
            qualification_receipt_id=qualification.qualification_receipt_id,
            max_tokens=request.max_tokens,
            token_count=token_count,
            sources=selected,
        )


@dataclass(frozen=True)
class AgentOutput:
    output_id: str
    content: object
    context_bundle_id: str
    context_bundle_hash: str
    contains_phi: bool = False
    synthetic: bool = False

    def __post_init__(self) -> None:
        _nonempty(self.output_id, "output id")
        _nonempty(self.context_bundle_id, "context bundle id")
        _nonempty(self.context_bundle_hash, "context bundle hash")
        _canonical(self.content, "agent output")


@dataclass(frozen=True)
class AdmissionProvenance:
    agent_id: str
    stable_identity: str
    qualification_receipt_id: str
    context_bundle_id: str
    context_bundle_hash: str
    source_ids: Tuple[str, ...]
    source_hashes: Tuple[str, ...]
    output_hash: str


@dataclass(frozen=True)
class AdmissionDecision:
    decision_id: str
    output_id: str
    target: AdmissionTarget
    status: AdmissionStatus
    destination: str
    reasons: Tuple[str, ...]
    prompt_injection_indicators: Tuple[str, ...]
    provenance: AdmissionProvenance

    def to_dict(self) -> Dict[str, Any]:
        value = asdict(self)
        value["target"] = self.target.value
        value["status"] = self.status.value
        return value


class MemoryAdmission:
    """Admit generated output to noncanonical memory or quarantine it for review."""

    def admit(
        self,
        output: AgentOutput,
        *,
        target: AdmissionTarget,
        bundle: AgentContextBundle,
        qualification: AgentContextQualification,
        current_source_hashes: Mapping[str, str],
        secret_values: Sequence[str] = (),
        phi_values: Sequence[str] = (),
    ) -> AdmissionDecision:
        self._validate_binding(output, bundle, qualification)
        stale = tuple(
            f"stale-context:{item.source_id}"
            for item in bundle.sources
            if current_source_hashes.get(item.source_id) != item.source_hash
        )
        credential_content = _contains_credential_key(output.content) or _contains_value(
            output.content, secret_values
        )
        phi_content = output.contains_phi or _contains_value(output.content, phi_values)
        indicators = self._injection_indicators(output.content)
        if stale:
            status, reasons = AdmissionStatus.REJECTED, stale
        elif credential_content:
            status, reasons = AdmissionStatus.REJECTED, ("credential-content",)
        elif phi_content and (
            not qualification.phi_eligible or not qualification.local_only or not output.synthetic
        ):
            status, reasons = AdmissionStatus.REJECTED, ("phi-boundary",)
        elif indicators:
            status, reasons = AdmissionStatus.QUARANTINED, ("prompt-injection-indicators",)
        else:
            status, reasons = AdmissionStatus.ADMITTED, ()
        output_hash = _hash(output.content)
        provenance = AdmissionProvenance(
            agent_id=qualification.agent_id,
            stable_identity=qualification.stable_identity,
            qualification_receipt_id=qualification.qualification_receipt_id,
            context_bundle_id=bundle.bundle_id,
            context_bundle_hash=bundle.bundle_hash,
            source_ids=tuple(item.source_id for item in bundle.sources),
            source_hashes=bundle.source_hashes,
            output_hash=output_hash,
        )
        destination = self._destination(status, target, output.output_id)
        decision_payload = {
            "output_id": output.output_id,
            "target": target.value,
            "status": status.value,
            "destination": destination,
            "reasons": list(reasons),
            "prompt_injection_indicators": list(indicators),
            "provenance": asdict(provenance),
        }
        return AdmissionDecision(
            decision_id="admission-" + _hash(decision_payload).removeprefix("sha256:")[:24],
            output_id=output.output_id,
            target=target,
            status=status,
            destination=destination,
            reasons=reasons,
            prompt_injection_indicators=indicators,
            provenance=provenance,
        )

    @staticmethod
    def _validate_binding(
        output: AgentOutput,
        bundle: AgentContextBundle,
        qualification: AgentContextQualification,
    ) -> None:
        if output.context_bundle_id != bundle.bundle_id:
            raise ValueError("output context bundle id does not match")
        if output.context_bundle_hash != bundle.bundle_hash:
            raise ValueError("output context bundle hash does not match")
        if not qualification.qualified or qualification.agent_id != bundle.agent_id:
            raise ValueError("output does not belong to the qualified agent")
        if qualification.stable_identity != bundle.stable_identity:
            raise ValueError("qualified agent identity does not match context")
        if qualification.qualification_receipt_id != bundle.qualification_receipt_id:
            raise ValueError("qualification receipt does not match context")

    @staticmethod
    def _injection_indicators(content: object) -> Tuple[str, ...]:
        matches = {
            name
            for text in _strings(content)
            for name, pattern in _INJECTION_PATTERNS
            if pattern.search(text)
        }
        return tuple(sorted(matches))

    @staticmethod
    def _destination(
        status: AdmissionStatus, target: AdmissionTarget, output_id: str
    ) -> str:
        if status is AdmissionStatus.QUARANTINED:
            return f"quarantine/{output_id}.json"
        if status is AdmissionStatus.REJECTED:
            return ""
        if target is AdmissionTarget.EVIDENCE:
            return f"evidence/{output_id}.json"
        return f"drafts/{target.value}/{output_id}.json"
