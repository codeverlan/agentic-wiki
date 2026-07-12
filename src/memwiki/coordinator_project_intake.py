from __future__ import annotations

import hashlib
import html
import json
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Mapping, MutableMapping, Tuple

from memwiki.coordinator_model_routing import HostCapabilityProfile, ModelRoutingPolicy
from memwiki.coordinator_storage import ArtifactPaths, LogicalArtifact, StorageAdapter
from memwiki.coordinator_storage_bmad import BmadArtifactStorage
from memwiki.coordinator_storage_lightweight import LightweightStorageAdapter

_STATE = LogicalArtifact("project-intake/state")
_CHECKPOINT = LogicalArtifact("project-intake/checkpoint")
_PROJECTIONS = (
    "intent-packet",
    "specification",
    "accepted-decision-matrix",
    "phi-profile",
    "design-memory",
    "external-capabilities",
    "project-overlay",
    "host-routing-policy",
    "readiness",
    "slice-queue",
)
_SENSITIVE = ("password", "secret", "token", "api_key", "private_key", "credential_value")
_ALLOWED_OVERLAY_KEYS = {"terminology", "interview_depth", "delivery", "design", "routing"}


class EntryPath(str, Enum):
    FROM_SCRATCH = "from-scratch"
    EXISTING_SPEC = "existing-specification"
    PARTIAL_RESOURCES = "partial-resources"


class IntakeCheckpointError(ValueError):
    """Raised when current intake state cannot be proven against its checkpoint."""


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value)).hexdigest()


def _reject_sensitive(value: object, path: str = "update") -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized = str(key).casefold().replace("-", "_")
            if any(part in normalized for part in _SENSITIVE):
                raise ValueError(f"sensitive value field is not allowed at {path}.{key}")
            _reject_sensitive(item, f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _reject_sensitive(item, f"{path}[{index}]")


def _merge(target: MutableMapping[str, Any], patch: Mapping[str, object]) -> None:
    for key, value in patch.items():
        current = target.get(key)
        if isinstance(current, dict) and isinstance(value, Mapping):
            _merge(current, value)
        else:
            target[key] = value


def _strings(value: object) -> Tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(item.strip() for item in value if isinstance(item, str) and item.strip())


@dataclass(frozen=True)
class ReadinessResult:
    ready: bool
    blocking_issues: Tuple[str, ...]
    advisory_issues: Tuple[str, ...]
    synthetic_only: bool

    def to_dict(self) -> Dict[str, object]:
        return {
            "ready": self.ready,
            "blocking_issues": list(self.blocking_issues),
            "advisory_issues": list(self.advisory_issues),
            "synthetic_only": self.synthetic_only,
        }


@dataclass(frozen=True)
class IntakeResult:
    adapter: str
    revision: int
    state: Mapping[str, Any]
    state_path: Path
    checkpoint_path: Path
    projection_paths: Mapping[str, ArtifactPaths]


class ProjectIntakeManager:
    """Transactional, project-local intake state with reproducible HTML projections."""

    def __init__(self, project_root: Path) -> None:
        root = Path(project_root)
        if root.is_symlink() or not root.is_dir():
            raise ValueError("project root must be an existing non-symlink directory")
        self.project_root = root.resolve(strict=True)
        if (self.project_root / "_bmad").is_dir():
            self.adapter = "bmad"
            self.storage: StorageAdapter = BmadArtifactStorage(self.project_root)
        else:
            self.adapter = "lightweight"
            self.storage = LightweightStorageAdapter(self.project_root)

    @staticmethod
    def artifact(name: str) -> LogicalArtifact:
        if name not in _PROJECTIONS:
            raise ValueError("unknown intake projection")
        return LogicalArtifact(f"project-intake/{name}")

    def initialize(
        self,
        *,
        project_id: str,
        entry_path: EntryPath,
        project_type: str,
        phi_answer: str,
        recorded_at: str,
    ) -> IntakeResult:
        if self.storage.exists(_STATE):
            existing = self.storage.read_json(_STATE)
            if existing.get("project_id") != project_id:
                raise ValueError("intake is already initialized for a different project")
            return self.resume()
        if not project_id.strip() or not project_type.strip() or not recorded_at.strip():
            raise ValueError("project identity, type, and recorded time are required")
        if not isinstance(entry_path, EntryPath):
            raise ValueError("entry path is invalid")
        if phi_answer not in {"yes", "no", "unknown"}:
            raise ValueError("PHI answer must be yes, no, or unknown")
        state: Dict[str, Any] = {
            "schema_version": 1,
            "project_id": project_id,
            "entry_path": entry_path.value,
            "project_type": project_type,
            "phi_answer": phi_answer,
            "synthetic_only": phi_answer in {"yes", "unknown"},
            "product": {"purpose": "", "users": [], "outcomes": [], "scope": []},
            "sources": [],
            "decisions": [],
            "acceptance_criteria": [],
            "design": {"material": False, "authority": ""},
            "external_dependencies": [],
            "credential_sources": [],
            "open_questions": [],
            "contradictions": [],
            "supervision": [],
            "overlay": {},
            "host_routing_policy": {},
            "host_capability_profile": {},
            "revision": 1,
            "recorded_at": recorded_at,
            "transaction_id": "",
        }
        state["transaction_id"] = _digest({key: value for key, value in state.items() if key != "transaction_id"})
        self._validate_state(state)
        return self._commit(state)

    def apply(
        self,
        update: Mapping[str, object],
        *,
        expected_revision: int,
        recorded_at: str,
    ) -> IntakeResult:
        _reject_sensitive(update)
        state = self.storage.read_json(_STATE)
        self._validate_state(state)
        self._verify_checkpoint(state)
        if state.get("revision") != expected_revision:
            raise ValueError("intake revision conflict")
        allowed = {
            "phi_answer",
            "product",
            "sources",
            "decisions",
            "acceptance_criteria",
            "design",
            "external_dependencies",
            "credential_sources",
            "open_questions",
            "contradictions",
            "supervision",
            "overlay",
            "host_routing_policy",
            "host_capability_profile",
        }
        unknown = set(update) - allowed
        if unknown:
            raise ValueError("unsupported intake update fields: " + ", ".join(sorted(unknown)))
        if "overlay" in update:
            overlay = update["overlay"]
            if not isinstance(overlay, Mapping) or set(overlay) - _ALLOWED_OVERLAY_KEYS:
                raise ValueError("overlay contains prohibited project customization")
        _merge(state, update)
        state["synthetic_only"] = state["phi_answer"] in {"yes", "unknown"}
        state["revision"] = expected_revision + 1
        state["recorded_at"] = recorded_at
        state["transaction_id"] = ""
        state["transaction_id"] = _digest({key: value for key, value in state.items() if key != "transaction_id"})
        self._validate_state(state)
        return self._commit(state)

    def inspect(self) -> IntakeResult:
        state = self.storage.read_json(_STATE)
        self._validate_state(state)
        self._verify_checkpoint(state)
        return self._result(state)

    def resume(self) -> IntakeResult:
        state = self.storage.read_json(_STATE)
        self._validate_state(state)
        if self.storage.exists(_CHECKPOINT):
            checkpoint = self.storage.read_json(_CHECKPOINT)
            checkpoint_revision = checkpoint.get("revision")
            if not isinstance(checkpoint_revision, int) or checkpoint_revision > state["revision"]:
                raise IntakeCheckpointError("checkpoint revision is invalid")
            if checkpoint_revision == state["revision"] and checkpoint.get("state_digest") != _digest(state):
                raise IntakeCheckpointError("checkpoint digest does not match current state")
        return self._commit(state, rewrite_state=False)

    def readiness(self) -> ReadinessResult:
        state = self.storage.read_json(_STATE)
        self._validate_state(state)
        return self._evaluate_readiness(state)

    def _commit(self, state: Mapping[str, Any], *, rewrite_state: bool = True) -> IntakeResult:
        if rewrite_state:
            self.storage.write_artifact(_STATE, state, self._render("Project intake state", state))
        projections = self._projection_values(state)
        for name, value in projections.items():
            self.storage.write_artifact(self.artifact(name), value, self._render(name, value))
        checkpoint = {
            "schema_version": 1,
            "project_id": state["project_id"],
            "revision": state["revision"],
            "transaction_id": state["transaction_id"],
            "state_digest": _digest(state),
            "projection_digests": {name: _digest(value) for name, value in sorted(projections.items())},
        }
        self.storage.write_artifact(_CHECKPOINT, checkpoint, self._render("Intake checkpoint", checkpoint))
        return self._result(state)

    def _result(self, state: Mapping[str, Any]) -> IntakeResult:
        return IntakeResult(
            self.adapter,
            int(state["revision"]),
            state,
            self.storage.paths_for(_STATE).json_path,
            self.storage.paths_for(_CHECKPOINT).json_path,
            {name: self.storage.paths_for(self.artifact(name)) for name in _PROJECTIONS},
        )

    def _verify_checkpoint(self, state: Mapping[str, Any]) -> None:
        if not self.storage.exists(_CHECKPOINT):
            raise IntakeCheckpointError("checkpoint is missing")
        checkpoint = self.storage.read_json(_CHECKPOINT)
        if checkpoint.get("revision") != state["revision"] or checkpoint.get("state_digest") != _digest(state):
            raise IntakeCheckpointError("checkpoint digest or revision does not match current state")

    def _projection_values(self, state: Mapping[str, Any]) -> Dict[str, Mapping[str, object]]:
        readiness = self._evaluate_readiness(state)
        ready_slice = {
            "id": "INTAKE-IMPLEMENTATION-001",
            "status": "ready",
            "description": "Decompose the accepted specification into evaluated implementation slices",
            "dependencies": [],
        }
        return {
            "intent-packet": {
                key: state[key]
                for key in ("schema_version", "project_id", "entry_path", "project_type", "phi_answer", "revision")
            },
            "specification": {
                key: state[key]
                for key in (
                    "project_id",
                    "product",
                    "acceptance_criteria",
                    "design",
                    "external_dependencies",
                    "open_questions",
                    "contradictions",
                    "revision",
                )
            },
            "accepted-decision-matrix": {"project_id": state["project_id"], "decisions": state["decisions"]},
            "phi-profile": {
                "project_id": state["project_id"],
                "phi_answer": state["phi_answer"],
                "synthetic_only": state["synthetic_only"],
            },
            "design-memory": {"project_id": state["project_id"], **dict(state["design"])},
            "external-capabilities": {
                "project_id": state["project_id"],
                "dependencies": state["external_dependencies"],
                "credential_sources": state["credential_sources"],
            },
            "project-overlay": {"project_id": state["project_id"], "overlay": state["overlay"]},
            "host-routing-policy": {
                "project_id": state["project_id"],
                "policy": state["host_routing_policy"],
                "host_profile": state["host_capability_profile"],
            },
            "readiness": {"project_id": state["project_id"], **readiness.to_dict()},
            "slice-queue": {
                "project_id": state["project_id"],
                "source_revision": state["revision"],
                "slices": [ready_slice] if readiness.ready else [],
            },
        }

    @staticmethod
    def _evaluate_readiness(state: Mapping[str, Any]) -> ReadinessResult:
        blocking = []
        advisory = []
        product = state["product"]
        assert isinstance(product, Mapping)
        if state["phi_answer"] == "unknown":
            blocking.append("phi-status-unresolved")
        if not str(product.get("purpose", "")).strip():
            blocking.append("product-purpose-missing")
        if not _strings(product.get("users")):
            blocking.append("target-users-missing")
        if not _strings(product.get("outcomes")):
            blocking.append("observable-outcomes-missing")
        if not _strings(product.get("scope")):
            blocking.append("scope-missing")
        if not state["acceptance_criteria"]:
            blocking.append("acceptance-criteria-missing")
        elif any(
            not isinstance(item, Mapping)
            or not str(item.get("id", "")).strip()
            or not str(item.get("statement", "")).strip()
            for item in state["acceptance_criteria"]
        ):
            blocking.append("acceptance-criteria-invalid")
        if state["entry_path"] != EntryPath.FROM_SCRATCH.value and not state["sources"]:
            blocking.append("source-authority-missing")
        elif state["entry_path"] != EntryPath.FROM_SCRATCH.value and any(
            not isinstance(item, Mapping)
            or not str(item.get("id", "")).strip()
            or not str(item.get("authority", "")).strip()
            for item in state["sources"]
        ):
            blocking.append("source-authority-invalid")
        design = state["design"]
        assert isinstance(design, Mapping)
        if design.get("material") is True and not str(design.get("authority", "")).strip():
            blocking.append("design-authority-missing")
        if state["contradictions"]:
            blocking.append("requirements-contradictory")
        if state["open_questions"]:
            blocking.append("material-questions-open")
        for dependency in state["external_dependencies"]:
            if (
                not isinstance(dependency, Mapping)
                or not str(dependency.get("id", "")).strip()
                or dependency.get("availability") not in {"available", "unavailable", "expected-missing"}
            ):
                blocking.append("external-dependency-unclassified")
                break
        if not state["decisions"]:
            advisory.append("no-accepted-decisions-recorded")
        return ReadinessResult(
            not blocking,
            tuple(sorted(set(blocking))),
            tuple(sorted(set(advisory))),
            bool(state["synthetic_only"]),
        )

    @staticmethod
    def _validate_state(state: Mapping[str, Any]) -> None:
        required = {
            "schema_version",
            "project_id",
            "entry_path",
            "project_type",
            "phi_answer",
            "synthetic_only",
            "product",
            "sources",
            "decisions",
            "acceptance_criteria",
            "design",
            "external_dependencies",
            "credential_sources",
            "open_questions",
            "contradictions",
            "supervision",
            "overlay",
            "host_routing_policy",
            "host_capability_profile",
            "revision",
            "recorded_at",
            "transaction_id",
        }
        if set(state) != required or state.get("schema_version") != 1:
            raise ValueError("intake state fields or schema version are invalid")
        if state.get("entry_path") not in {item.value for item in EntryPath}:
            raise ValueError("intake entry path is invalid")
        if state.get("phi_answer") not in {"yes", "no", "unknown"}:
            raise ValueError("intake PHI answer is invalid")
        if not isinstance(state.get("revision"), int) or isinstance(state.get("revision"), bool):
            raise ValueError("intake revision is invalid")
        if not isinstance(state.get("overlay"), Mapping) or set(state["overlay"]) - _ALLOWED_OVERLAY_KEYS:
            raise ValueError("overlay contains prohibited project customization")
        policy = state["host_routing_policy"]
        profile = state["host_capability_profile"]
        if not isinstance(policy, Mapping) or not isinstance(profile, Mapping):
            raise ValueError("host routing policy and profile must be objects")
        if policy:
            ModelRoutingPolicy.from_dict(policy)
        if profile:
            HostCapabilityProfile.from_dict(profile)
        _reject_sensitive(state, "state")

    @staticmethod
    def _render(title: str, value: Mapping[str, object]) -> str:
        jsonld = json.dumps(value, sort_keys=True).replace("<", "\\u003c")
        rows = "".join(
            f"<tr><th>{html.escape(str(key))}</th>"
            f"<td><pre>{html.escape(json.dumps(item, sort_keys=True))}</pre></td></tr>"
            for key, item in sorted(value.items())
        )
        return (
            "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
            f"<title>{html.escape(title)}</title><script type=\"application/ld+json\">{jsonld}</script>"
            "</head><body><main>"
            f"<h1>{html.escape(title)}</h1><table><tbody>{rows}</tbody></table>"
            "</main></body></html>"
        )
