from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from memwiki.coordinator_credentials import (
    CredentialReference,
    CredentialResolver,
    CredentialSource,
    redact_credentials,
)


def _git(project: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args], cwd=project, check=True, capture_output=True, text=True
    )


def _project(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    project.mkdir()
    _git(project, "init", "-q")
    (project / ".gitignore").write_text(".credentials.json\n", encoding="utf-8")
    return project


def test_env_reference_resolves_without_exposing_value_in_metadata(tmp_path: Path) -> None:
    project = _project(tmp_path)
    resolver = CredentialResolver(project, environment={"SERVICE_TOKEN": "canary-token"})
    reference = CredentialReference(
        identifier="service-auth", source=CredentialSource.ENV, location="SERVICE_TOKEN"
    )

    resolved = resolver.resolve(reference)

    assert resolved.value == "canary-token"
    assert resolved.secret_values == ("canary-token",)
    assert resolved.warnings == ()
    assert reference.to_metadata() == {
        "identifier": "service-auth",
        "source": "env",
        "location": "SERVICE_TOKEN",
        "selector": [],
    }
    assert "canary-token" not in json.dumps(reference.to_metadata())


def test_json_reference_supports_arbitrary_nested_auth_shapes(tmp_path: Path) -> None:
    project = _project(tmp_path)
    credentials = project / ".credentials.json"
    credentials.write_text(
        json.dumps(
            {
                "vendor": {
                    "auth": {
                        "username": "developer",
                        "password": "canary-password",
                        "headers": {"X-Custom": "canary-header"},
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    credentials.chmod(0o600)
    resolver = CredentialResolver(project)
    reference = CredentialReference(
        identifier="vendor-auth",
        source=CredentialSource.JSON,
        location=".credentials.json",
        selector=("vendor", "auth"),
    )

    resolved = resolver.resolve(reference)

    assert resolved.value == {
        "username": "developer",
        "password": "canary-password",
        "headers": {"X-Custom": "canary-header"},
    }
    assert set(resolved.secret_values) == {
        "developer",
        "canary-password",
        "canary-header",
    }


def test_ephemeral_reference_is_process_local_and_metadata_only(tmp_path: Path) -> None:
    project = _project(tmp_path)
    resolver = CredentialResolver(
        project, ephemeral={"chat-auth": {"token": "chat-canary", "mode": "dev"}}
    )
    reference = CredentialReference(
        identifier="chat-auth",
        source=CredentialSource.EPHEMERAL,
        location="chat-auth",
    )

    resolved = resolver.resolve(reference)

    assert resolved.value == {"token": "chat-canary", "mode": "dev"}
    assert "chat-canary" not in json.dumps(reference.to_metadata())
    assert not list(project.glob("**/*chat-canary*"))


def test_missing_credential_blocks_only_requested_resolution(tmp_path: Path) -> None:
    resolver = CredentialResolver(_project(tmp_path), environment={"PRESENT": "ok"})

    with pytest.raises(KeyError, match="credential missing is unavailable"):
        resolver.resolve(
            CredentialReference("missing", CredentialSource.ENV, "MISSING")
        )

    assert resolver.resolve(
        CredentialReference("present", CredentialSource.ENV, "PRESENT")
    ).value == "ok"


@pytest.mark.parametrize(
    "location",
    ["../outside.json", "/tmp/outside.json", ".git/config"],
)
def test_json_source_must_be_confined_and_outside_git_metadata(
    tmp_path: Path, location: str
) -> None:
    resolver = CredentialResolver(_project(tmp_path))
    with pytest.raises(ValueError, match="credential path"):
        resolver.resolve(
            CredentialReference("unsafe", CredentialSource.JSON, location)
        )


def test_json_source_rejects_symlinks(tmp_path: Path) -> None:
    project = _project(tmp_path)
    outside = tmp_path / "outside.json"
    outside.write_text('{"token":"canary"}', encoding="utf-8")
    (project / ".credentials.json").symlink_to(outside)

    with pytest.raises(ValueError, match="symlink"):
        CredentialResolver(project).resolve(
            CredentialReference("unsafe", CredentialSource.JSON, ".credentials.json")
        )


def test_json_source_must_be_git_ignored(tmp_path: Path) -> None:
    project = _project(tmp_path)
    path = project / "visible.json"
    path.write_text('{"token":"canary"}', encoding="utf-8")
    path.chmod(0o600)

    with pytest.raises(ValueError, match="must be ignored by Git"):
        CredentialResolver(project).resolve(
            CredentialReference("unsafe", CredentialSource.JSON, "visible.json")
        )


def test_json_source_warns_for_group_or_world_permissions(tmp_path: Path) -> None:
    project = _project(tmp_path)
    path = project / ".credentials.json"
    path.write_text('{"token":"canary"}', encoding="utf-8")
    path.chmod(0o644)

    resolved = CredentialResolver(project).resolve(
        CredentialReference("auth", CredentialSource.JSON, ".credentials.json")
    )

    assert resolved.warnings == (
        "credential file permissions are broader than owner-only (recommended: 0600)",
    )


def test_selector_rejects_missing_or_non_container_segments(tmp_path: Path) -> None:
    project = _project(tmp_path)
    path = project / ".credentials.json"
    path.write_text('{"auth":{"token":"canary"}}', encoding="utf-8")
    path.chmod(0o600)
    resolver = CredentialResolver(project)

    with pytest.raises(KeyError, match="selector"):
        resolver.resolve(
            CredentialReference(
                "auth", CredentialSource.JSON, ".credentials.json", ("missing",)
            )
        )


def test_recursive_redaction_covers_keys_values_and_embedded_canaries() -> None:
    value = {
        "Authorization": "Bearer canary-token",
        "nested": [
            {"password": "canary-password"},
            "request failed for canary-token",
            {"safe": "development"},
        ],
        "tuple": ("canary-password", 3),
    }

    redacted = redact_credentials(value, ("canary-token", "canary-password"))

    assert redacted == {
        "Authorization": "[REDACTED]",
        "nested": [
            {"password": "[REDACTED]"},
            "request failed for [REDACTED]",
            {"safe": "development"},
        ],
        "tuple": ["[REDACTED]", 3],
    }
    encoded = json.dumps(redacted)
    assert "canary-token" not in encoded
    assert "canary-password" not in encoded


def test_reference_validation_rejects_secret_bearing_or_invalid_metadata() -> None:
    with pytest.raises(ValueError, match="identifier"):
        CredentialReference("", CredentialSource.ENV, "TOKEN")
    with pytest.raises(ValueError, match="location"):
        CredentialReference("auth", CredentialSource.ENV, "")
    with pytest.raises(ValueError, match="selector"):
        CredentialReference("auth", CredentialSource.JSON, "auth.json", ("",))


def test_environment_snapshot_does_not_follow_later_process_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SNAPSHOT_TOKEN", "first")
    resolver = CredentialResolver(_project(tmp_path), environment=os.environ)
    monkeypatch.setenv("SNAPSHOT_TOKEN", "second")

    resolved = resolver.resolve(
        CredentialReference("auth", CredentialSource.ENV, "SNAPSHOT_TOKEN")
    )

    assert resolved.value == "first"


def test_process_environment_injection_is_explicit_and_redactable(tmp_path: Path) -> None:
    project = _project(tmp_path)
    resolver = CredentialResolver(project, environment={"SOURCE_TOKEN": "process-canary"})
    injected = resolver.resolve_environment(
        {
            "VENDOR_AUTH": CredentialReference(
                "vendor-auth", CredentialSource.ENV, "SOURCE_TOKEN"
            )
        }
    )

    result = subprocess.run(
        ["sh", "-c", 'printf "%s" "$VENDOR_AUTH"'],
        check=True,
        capture_output=True,
        text=True,
        env={"PATH": os.environ["PATH"], **injected.values},
    )

    assert result.stdout == "process-canary"
    assert redact_credentials(result.stdout, injected.secret_values) == "[REDACTED]"
    assert "VENDOR_AUTH" not in os.environ
    assert "process-canary" not in json.dumps(injected.to_metadata())
