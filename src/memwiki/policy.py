from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Optional, Protocol, Tuple

from memwiki.ids import stable_id, utc_now
from memwiki.manifest import append_jsonl


class PolicyError(PermissionError):
    """Raised when workspace policy blocks an operation."""


@dataclass(frozen=True)
class OperationContext:
    actor_id: str
    actor_role: str
    purpose_of_use: str
    session_id: str
    reason: Optional[str] = None
    authority_token: Optional[str] = field(default=None, repr=False)

    def __post_init__(self) -> None:
        for field_name in ["actor_id", "actor_role", "purpose_of_use", "session_id"]:
            if not str(getattr(self, field_name)).strip():
                raise ValueError(f"OperationContext.{field_name} is required")

    def to_dict(self) -> Dict[str, str]:
        return {
            key: str(value)
            for key, value in {
                "actor_id": self.actor_id,
                "actor_role": self.actor_role,
                "purpose_of_use": self.purpose_of_use,
                "session_id": self.session_id,
                "reason": self.reason,
            }.items()
            if value is not None
        }


@dataclass(frozen=True)
class VerifiedAuthority:
    subject_id: str
    roles: Tuple[str, ...]
    issuer: str
    credential_id: str
    allowed_operations: Tuple[str, ...]
    workspace: str
    session_id: str

    def audit_details(self) -> Dict[str, object]:
        return {
            "subject_id": self.subject_id,
            "roles": list(self.roles),
            "issuer": self.issuer,
            "credential_id": self.credential_id,
        }


class AuthorityVerifier(Protocol):
    def verify(
        self,
        token: str,
        *,
        operation: str,
        workspace: Path,
        session_id: str,
    ) -> VerifiedAuthority:
        ...


def verify_coordinator_authority(
    context: Optional[OperationContext],
    verifier: Optional[AuthorityVerifier],
    operation: str,
    workspace: Path,
) -> VerifiedAuthority:
    if context is None or not context.authority_token or verifier is None:
        raise PolicyError("Verified coordinator authority is required")
    try:
        verified = verifier.verify(
            context.authority_token,
            operation=operation,
            workspace=workspace,
            session_id=context.session_id,
        )
    except Exception:
        raise PolicyError("Authority credential verification failed") from None
    if verified.subject_id != context.actor_id:
        raise PolicyError("Verified authority identity does not match operation context")
    if "coordinator" not in verified.roles or operation not in verified.allowed_operations:
        raise PolicyError("Verified coordinator authority does not permit this operation")
    if Path(verified.workspace).resolve() != workspace.resolve() or verified.session_id != context.session_id:
        raise PolicyError("Verified coordinator authority scope does not match this workspace or session")
    return verified


def normalize_profile(profile: str) -> str:
    normalized = profile.strip().lower().replace("-", "_")
    if normalized in {"standard", "clinical_phi"}:
        return normalized
    raise ValueError(f"Unsupported workspace profile: {profile}")


def parse_config(path: Path) -> Dict[str, Dict[str, object]]:
    if not path.exists():
        return {}
    parsed: Dict[str, Dict[str, object]] = {}
    section = ""
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("[") and stripped.endswith("]"):
            section = stripped[1:-1]
            parsed.setdefault(section, {})
            continue
        if "=" not in stripped:
            continue
        key, raw_value = [part.strip() for part in stripped.split("=", 1)]
        if raw_value in {"true", "false"}:
            value: object = raw_value == "true"
        elif raw_value.startswith('"') and raw_value.endswith('"'):
            value = raw_value[1:-1]
        else:
            value = raw_value
        parsed.setdefault(section, {})[key] = value
    return parsed


def workspace_profile(config_path: Path) -> str:
    config = parse_config(config_path)
    value = config.get("workspace", {}).get("profile", "standard")
    return normalize_profile(str(value))


def client_record_id(config_path: Path) -> Optional[str]:
    config = parse_config(config_path)
    value = config.get("workspace", {}).get("client_record_id")
    if isinstance(value, str) and value:
        return value
    return None


def is_clinical_phi(config_path: Path) -> bool:
    return workspace_profile(config_path) == "clinical_phi"


def config_bool(config_path: Path, section: str, key: str, default: bool) -> bool:
    value = parse_config(config_path).get(section, {}).get(key, default)
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() == "true"


def sensitivity(config_path: Path) -> str:
    return "phi" if is_clinical_phi(config_path) else "general"


def require_operation_context(config_path: Path, operation: str, context: Optional[OperationContext]) -> None:
    if not is_clinical_phi(config_path):
        return
    if config_bool(config_path, "models", "allow_remote", False):
        raise PolicyError("Remote model adapters are blocked for clinical PHI workspaces")
    if not config_bool(config_path, "privacy", "local_encrypted_storage_attested", False):
        raise PolicyError("Clinical PHI workspaces require local encrypted storage attestation")
    if config_bool(config_path, "privacy", "require_operation_context", True) and context is None:
        raise PolicyError(f"Operation context is required for clinical PHI workspaces: {operation}")


def require_static_export_allowed(config_path: Path, context: Optional[OperationContext], deidentified: bool) -> None:
    require_operation_context(config_path, "export_static", context)
    if is_clinical_phi(config_path) and not deidentified:
        raise PolicyError("Static export is blocked for clinical PHI workspaces unless marked deidentified")


def context_event_details(context: Optional[OperationContext]) -> Dict[str, str]:
    return context.to_dict() if context is not None else {}


def append_event(
    workspace_root: Path,
    event_type: str,
    details: Dict[str, object],
    context: Optional[OperationContext] = None,
) -> None:
    created_at = utc_now()
    safe_details: Dict[str, object] = {"operation": event_type, **details}
    safe_details.update(context_event_details(context))
    append_jsonl(
        workspace_root / "manifests/events.jsonl",
        {
            "event_id": stable_id("evt", event_type, created_at),
            "event_type": event_type,
            "created_at": created_at,
            "details": safe_details,
        },
    )
