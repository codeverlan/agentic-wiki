from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Dict, Mapping, Optional, Sequence, Tuple

from memwiki.policy import OperationContext


class DataProfile(str, Enum):
    YES = "yes"
    NO = "no"
    UNKNOWN = "unknown"


class PrivacyAction(str, Enum):
    WRITE_ARTIFACT = "write-artifact"
    READ_PHI = "read-phi"
    MUTATE_PHI = "mutate-phi"
    LOCAL_WORKER = "local-worker"
    LOCAL_VALIDATION = "local-validation"
    REMOTE_ADAPTER = "remote-adapter"
    CLOUD_STORAGE = "cloud-storage"
    TELEMETRY = "telemetry"
    STATIC_EXPORT = "static-export"
    EXTERNAL_INVOCATION = "external-invocation"


class RuntimeSurface(str, Enum):
    PROMPT = "prompt"
    FILE = "file"
    LOG = "log"
    SCREENSHOT = "screenshot"
    REPORT = "report"
    GIT = "git"
    HTML = "html"


@dataclass(frozen=True)
class ScanFinding:
    """Process-local scanner result; evidence is never copied into a violation."""

    category: str
    code: str
    evidence: str = field(repr=False)

    def __post_init__(self) -> None:
        if not self.category.strip() or not self.code.strip() or not self.evidence:
            raise ValueError("scan finding fields must be non-empty")


@dataclass(frozen=True)
class PrivacyViolation:
    category: str
    code: str
    surface: RuntimeSurface
    location: str
    evidence_sha256: str

    def __post_init__(self) -> None:
        if not self.category.strip() or not self.code.strip():
            raise ValueError("violation category and code must be non-empty")
        if len(self.evidence_sha256) != 64 or any(
            character not in "0123456789abcdef" for character in self.evidence_sha256
        ):
            raise ValueError("violation evidence hash must be lowercase SHA-256")

    def to_dict(self) -> Dict[str, str]:
        return {
            "category": self.category,
            "code": self.code,
            "surface": self.surface.value,
            "location": self.location,
            "evidence_sha256": self.evidence_sha256,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> PrivacyViolation:
        expected = {"category", "code", "surface", "location", "evidence_sha256"}
        if set(value) != expected or not all(isinstance(value[key], str) for key in expected):
            raise ValueError("invalid privacy violation record")
        return cls(
            category=str(value["category"]),
            code=str(value["code"]),
            surface=RuntimeSurface(str(value["surface"])),
            location=str(value["location"]),
            evidence_sha256=str(value["evidence_sha256"]),
        )


class PrivacyViolationError(PermissionError):
    def __init__(self, violations: Sequence[PrivacyViolation]) -> None:
        if not violations:
            raise ValueError("privacy violation error requires at least one violation")
        self.violations = tuple(violations)
        codes = ", ".join(sorted({item.code for item in self.violations}))
        super().__init__(f"privacy policy denied operation: {codes}")

    def to_dict(self) -> Dict[str, object]:
        return {
            "status": "denied",
            "violations": [item.to_dict() for item in self.violations],
        }


ScanHook = Callable[[RuntimeSurface, object], Sequence[ScanFinding]]

_NONLOCAL_ACTIONS = {
    PrivacyAction.REMOTE_ADAPTER,
    PrivacyAction.CLOUD_STORAGE,
    PrivacyAction.TELEMETRY,
    PrivacyAction.STATIC_EXPORT,
    PrivacyAction.EXTERNAL_INVOCATION,
}
_PHI_ACTIONS = {PrivacyAction.READ_PHI, PrivacyAction.MUTATE_PHI}
_SENSITIVE_KEYS = {
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
_SENSITIVE_SUFFIXES = ("_key", "_password", "_secret", "_token")


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _normalized_key(value: str) -> str:
    return value.casefold().replace("-", "_").replace(" ", "_")


def _is_sensitive_key(value: str) -> bool:
    normalized = _normalized_key(value)
    return normalized in _SENSITIVE_KEYS or normalized.endswith(_SENSITIVE_SUFFIXES)


class PrivacyPolicy:
    """Coordinator runtime guard for PHI posture and sensitive artifacts."""

    def __init__(
        self,
        profile: DataProfile,
        *,
        phi_canaries: Sequence[str] = (),
        secret_canaries: Sequence[str] = (),
        scan_hooks: Sequence[ScanHook] = (),
    ) -> None:
        if not isinstance(profile, DataProfile):
            raise ValueError("profile must be yes, no, or unknown")
        self.profile = profile
        self._phi_canaries = self._validate_canaries(phi_canaries, "PHI")
        self._secret_canaries = self._validate_canaries(secret_canaries, "secret")
        self._scan_hooks = tuple(scan_hooks)

    @property
    def synthetic_only(self) -> bool:
        return self.profile in {DataProfile.YES, DataProfile.UNKNOWN}

    def authorize(
        self,
        action: PrivacyAction,
        *,
        context: Optional[OperationContext] = None,
        synthetic: bool = False,
    ) -> None:
        if not isinstance(action, PrivacyAction):
            raise ValueError("unsupported privacy action")
        if self.synthetic_only and action in _NONLOCAL_ACTIONS:
            raise self._policy_error("local-only", action.value)
        if self.profile is DataProfile.UNKNOWN and action in _PHI_ACTIONS and not synthetic:
            raise self._policy_error("synthetic-only", action.value)
        if self.profile is DataProfile.YES and action in _PHI_ACTIONS and context is None:
            raise self._policy_error("operation-context-required", "operation context")
        if self.synthetic_only and action is PrivacyAction.WRITE_ARTIFACT and not synthetic:
            raise self._policy_error("synthetic-only", action.value)

    def scan(
        self,
        surface: RuntimeSurface,
        value: object,
        *,
        location: str = "runtime",
    ) -> Tuple[PrivacyViolation, ...]:
        if not isinstance(surface, RuntimeSurface):
            raise ValueError("unsupported runtime surface")
        findings = list(self._scan_value(value))
        for hook in self._scan_hooks:
            results = hook(surface, value)
            for result in results:
                if not isinstance(result, ScanFinding):
                    raise TypeError("scan hooks must return ScanFinding values")
                findings.append(result)
        safe_location = location
        if any(canary in location for canary in self._phi_canaries + self._secret_canaries):
            safe_location = "[REDACTED]"
        return tuple(self._finding_to_violation(surface, item, safe_location) for item in findings)

    def inspect(
        self,
        surface: RuntimeSurface,
        value: object,
        *,
        location: str = "runtime",
    ) -> None:
        violations = self.scan(surface, value, location=location)
        if violations:
            raise PrivacyViolationError(violations)

    def _scan_value(self, value: object) -> Tuple[ScanFinding, ...]:
        findings = []

        def visit(item: object) -> None:
            if isinstance(item, Mapping):
                for key, nested in item.items():
                    if not isinstance(key, str):
                        raise ValueError("scanned mapping keys must be strings")
                    if _is_sensitive_key(key):
                        findings.append(ScanFinding("secret", "sensitive-key", key))
                    visit(nested)
                return
            if isinstance(item, (list, tuple)):
                for nested in item:
                    visit(nested)
                return
            if isinstance(item, str):
                for canary in self._phi_canaries:
                    if canary in item:
                        findings.append(ScanFinding("phi", "canary", canary))
                for canary in self._secret_canaries:
                    if canary in item:
                        findings.append(ScanFinding("secret", "canary", canary))
                return
            if isinstance(item, bytes):
                for canary in self._phi_canaries:
                    if canary.encode("utf-8") in item:
                        findings.append(ScanFinding("phi", "canary", canary))
                for canary in self._secret_canaries:
                    if canary.encode("utf-8") in item:
                        findings.append(ScanFinding("secret", "canary", canary))
                return
            if item is not None and not isinstance(item, (bool, int, float)):
                raise ValueError("scanned values must be JSON-compatible")

        visit(value)
        return tuple(findings)

    @staticmethod
    def _validate_canaries(values: Sequence[str], label: str) -> Tuple[str, ...]:
        if any(not isinstance(item, str) or not item for item in values):
            raise ValueError(f"{label} canaries must be non-empty strings")
        return tuple(sorted(set(values), key=lambda item: (-len(item), item)))

    @staticmethod
    def _finding_to_violation(
        surface: RuntimeSurface,
        finding: ScanFinding,
        location: str,
    ) -> PrivacyViolation:
        return PrivacyViolation(
            category=finding.category,
            code=finding.code,
            surface=surface,
            location=location,
            evidence_sha256=_hash(finding.evidence),
        )

    @staticmethod
    def _policy_error(code: str, evidence: str) -> PrivacyViolationError:
        violation = PrivacyViolation(
            category="policy",
            code=code,
            surface=RuntimeSurface.REPORT,
            location="authorization",
            evidence_sha256=_hash(evidence),
        )
        if code == "operation-context-required":
            error = PrivacyViolationError((violation,))
            error.args = ("privacy policy denied operation: operation context required",)
            return error
        return PrivacyViolationError((violation,))
