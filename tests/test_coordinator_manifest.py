from __future__ import annotations

import copy

import pytest

from memwiki.coordinator_manifest import (
    CoordinatorManifestError,
    build_coordinator_manifest,
    content_digest,
    verify_coordinator_manifest,
)


def _manifest() -> dict[str, object]:
    return build_coordinator_manifest(
        run_id="run-038",
        objective="Prove coordinator completion",
        journal_head={"event_hash": "event-final", "sequence": 38},
        projections={"run_state": {"revision": "commit-final"}},
        reports=[{"report_id": "report-1", "disposition": "integrated"}],
        runtime={"reconciled": True},
        validation={"revision": "commit-final", "passed": True},
        memory={"dispositions": ["applied"]},
        incidents=[],
        branch_sha={"branch": "codex/complete", "head": "commit-final", "remote": "commit-final"},
        authoritative_artifacts={
            "control-index": {"path": "control-index.html", "source_revision": "commit-final"},
            "handoff": {"path": "handoff.html", "source_revision": "commit-final"},
        },
    )


def test_manifest_binds_every_authoritative_completion_surface_deterministically() -> None:
    first = _manifest()
    second = _manifest()
    assert first == second
    assert verify_coordinator_manifest(first) == first
    bindings = first["bindings"]
    assert isinstance(bindings, dict)
    assert set(bindings) == {
        "journal_head",
        "projections",
        "reports",
        "runtime",
        "validation",
        "memory",
        "incidents",
        "branch_sha",
    }
    unsigned = {key: value for key, value in first.items() if key != "manifest_hash"}
    assert first["manifest_hash"] == content_digest(unsigned)


@pytest.mark.parametrize("path", [("bindings", "reports", "value"), ("authoritative_artifacts", 0, "value")])
def test_manifest_rejects_tampered_bound_data(path: tuple[object, ...]) -> None:
    manifest = copy.deepcopy(_manifest())
    target: object = manifest
    for part in path[:-1]:
        if isinstance(part, int):
            assert isinstance(target, list)
        else:
            assert isinstance(target, dict)
        target = target[part]  # type: ignore[index]
    assert isinstance(target, dict)
    target[path[-1]] = {"tampered": True}
    with pytest.raises(CoordinatorManifestError, match="manifest hash mismatch"):
        verify_coordinator_manifest(manifest)


def test_manifest_rejects_missing_or_unsorted_authoritative_artifacts() -> None:
    manifest = _manifest()
    bindings = manifest["bindings"]
    assert isinstance(bindings, dict)
    bindings.pop("memory")
    # Rehashing cannot make an incomplete structure valid.
    unsigned = {key: value for key, value in manifest.items() if key != "manifest_hash"}
    manifest["manifest_hash"] = content_digest(unsigned)
    with pytest.raises(CoordinatorManifestError, match="required completion surfaces"):
        verify_coordinator_manifest(manifest)


def test_manifest_detects_nested_tampering_even_when_the_envelope_is_rehashed() -> None:
    manifest = _manifest()
    bindings = manifest["bindings"]
    assert isinstance(bindings, dict)
    report_binding = bindings["reports"]
    assert isinstance(report_binding, dict)
    report_binding["value"] = [{"report_id": "forged", "disposition": "integrated"}]
    unsigned = {key: value for key, value in manifest.items() if key != "manifest_hash"}
    manifest["manifest_hash"] = content_digest(unsigned)
    with pytest.raises(CoordinatorManifestError, match="reports binding hash mismatch"):
        verify_coordinator_manifest(manifest)
