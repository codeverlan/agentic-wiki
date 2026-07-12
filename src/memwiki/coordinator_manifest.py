"""Canonical, content-addressed bindings for coordinator completion evidence."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Dict, Mapping, Tuple, cast

_HASH = re.compile(r"^sha256:[0-9a-f]{64}$")
_REQUIRED_BINDINGS = (
    "journal_head",
    "projections",
    "reports",
    "runtime",
    "validation",
    "memory",
    "incidents",
    "branch_sha",
)


class CoordinatorManifestError(ValueError):
    """A manifest is malformed, incomplete, or no longer content-addressed."""


def canonical_json(value: object) -> bytes:
    """Encode JSON-compatible data into the one accepted manifest representation."""
    try:
        return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False).encode(
            "utf-8"
        )
    except (TypeError, ValueError) as exc:
        raise CoordinatorManifestError("manifest data must be JSON-compatible") from exc


def content_digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(canonical_json(value)).hexdigest()


def _mapping(value: object) -> Mapping[str, Any]:
    return cast(Mapping[str, Any], value) if isinstance(value, Mapping) else {}


def _frozen(value: object) -> object:
    return json.loads(canonical_json(value))


def _binding(value: object) -> Dict[str, object]:
    frozen = _frozen(value)
    return {"sha256": content_digest(frozen), "value": frozen}


def _artifact_bindings(artifacts: Mapping[str, object]) -> Tuple[Dict[str, object], ...]:
    entries = []
    for artifact_id, value in sorted(artifacts.items()):
        if not isinstance(artifact_id, str) or not artifact_id:
            raise CoordinatorManifestError("authoritative artifact IDs must be non-empty strings")
        entries.append({"artifact_id": artifact_id, **_binding(value)})
    return tuple(entries)


def build_coordinator_manifest(
    *,
    run_id: str,
    objective: str,
    journal_head: object,
    projections: object,
    reports: object,
    runtime: object,
    validation: object,
    memory: object,
    incidents: object,
    branch_sha: object,
    authoritative_artifacts: Mapping[str, object],
) -> Dict[str, object]:
    """Bind all completion surfaces into one immutable, independently verifiable record."""
    if not isinstance(run_id, str) or not run_id:
        raise CoordinatorManifestError("run_id must be a non-empty string")
    if not isinstance(objective, str) or not objective:
        raise CoordinatorManifestError("objective must be a non-empty string")
    if not isinstance(authoritative_artifacts, Mapping):
        raise CoordinatorManifestError("authoritative_artifacts must be an object")
    values = {
        "journal_head": journal_head,
        "projections": projections,
        "reports": reports,
        "runtime": runtime,
        "validation": validation,
        "memory": memory,
        "incidents": incidents,
        "branch_sha": branch_sha,
    }
    payload: Dict[str, object] = {
        "schema_version": 1,
        "manifest_type": "memwiki.autonomous-coordinator-evidence",
        "run_id": run_id,
        "objective": objective,
        "bindings": {name: _binding(value) for name, value in values.items()},
        "authoritative_artifacts": list(_artifact_bindings(authoritative_artifacts)),
    }
    payload["manifest_hash"] = content_digest(payload)
    return payload


def verify_coordinator_manifest(manifest: Mapping[str, object]) -> Dict[str, object]:
    """Verify the envelope and every nested content-addressed binding."""
    if not isinstance(manifest, Mapping):
        raise CoordinatorManifestError("manifest must be an object")
    supplied_hash = manifest.get("manifest_hash")
    unsigned = dict(manifest)
    unsigned.pop("manifest_hash", None)
    if not isinstance(supplied_hash, str) or supplied_hash != content_digest(unsigned):
        raise CoordinatorManifestError("manifest hash mismatch")
    if (
        manifest.get("schema_version") != 1
        or manifest.get("manifest_type") != "memwiki.autonomous-coordinator-evidence"
    ):
        raise CoordinatorManifestError("unsupported manifest schema")
    if not isinstance(manifest.get("run_id"), str) or not manifest["run_id"]:
        raise CoordinatorManifestError("manifest run_id is invalid")
    if not isinstance(manifest.get("objective"), str) or not manifest["objective"]:
        raise CoordinatorManifestError("manifest objective is invalid")
    bindings = _mapping(manifest.get("bindings"))
    if set(bindings) != set(_REQUIRED_BINDINGS):
        raise CoordinatorManifestError("manifest bindings do not match the required completion surfaces")
    for name in _REQUIRED_BINDINGS:
        binding = _mapping(bindings[name])
        if set(binding) != {"sha256", "value"}:
            raise CoordinatorManifestError(f"{name} binding is malformed")
        digest = binding.get("sha256")
        if not isinstance(digest, str) or not _HASH.fullmatch(digest) or digest != content_digest(binding.get("value")):
            raise CoordinatorManifestError(f"{name} binding hash mismatch")
    artifacts = manifest.get("authoritative_artifacts")
    if not isinstance(artifacts, list):
        raise CoordinatorManifestError("authoritative_artifacts must be a list")
    artifact_ids = []
    for artifact in artifacts:
        entry = _mapping(artifact)
        if set(entry) != {"artifact_id", "sha256", "value"}:
            raise CoordinatorManifestError("authoritative artifact binding is malformed")
        artifact_id, digest = entry.get("artifact_id"), entry.get("sha256")
        if not isinstance(artifact_id, str) or not artifact_id or not isinstance(digest, str):
            raise CoordinatorManifestError("authoritative artifact identity is invalid")
        if not _HASH.fullmatch(digest) or digest != content_digest(entry.get("value")):
            raise CoordinatorManifestError(f"authoritative artifact hash mismatch: {artifact_id}")
        artifact_ids.append(artifact_id)
    if artifact_ids != sorted(artifact_ids) or len(set(artifact_ids)) != len(artifact_ids):
        raise CoordinatorManifestError("authoritative artifacts must be unique and sorted")
    return dict(manifest)
