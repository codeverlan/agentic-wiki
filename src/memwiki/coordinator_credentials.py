from __future__ import annotations

import json
import os
import stat
import subprocess
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence, Set, Tuple

_REDACTED = "[REDACTED]"
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
    "user",
    "username",
    "user_name",
}
_SENSITIVE_SUFFIXES = ("_key", "_password", "_secret", "_token")


class CredentialSource(str, Enum):
    ENV = "env"
    JSON = "json"
    EPHEMERAL = "ephemeral"


@dataclass(frozen=True)
class CredentialReference:
    """Metadata-only pointer to credential material held outside runtime artifacts."""

    identifier: str
    source: CredentialSource
    location: str
    selector: Tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.identifier.strip():
            raise ValueError("credential identifier must be non-empty")
        if not self.location.strip():
            raise ValueError("credential location must be non-empty")
        if any(not isinstance(item, str) or not item for item in self.selector):
            raise ValueError("credential selector segments must be non-empty strings")
        if self.source is not CredentialSource.JSON and self.selector:
            raise ValueError("credential selector is supported only for JSON sources")

    def to_metadata(self) -> Dict[str, object]:
        return {
            "identifier": self.identifier,
            "source": self.source.value,
            "location": self.location,
            "selector": list(self.selector),
        }


@dataclass(frozen=True)
class ResolvedCredential:
    """Process-local result. Callers must not serialize this object."""

    identifier: str
    value: Any
    secret_values: Tuple[str, ...]
    warnings: Tuple[str, ...] = ()


@dataclass(frozen=True)
class ProcessCredentialEnvironment:
    """Secret-bearing child-process environment overlay; never an artifact payload."""

    values: Mapping[str, str]
    secret_values: Tuple[str, ...]
    warnings: Tuple[str, ...] = ()

    def to_metadata(self) -> Dict[str, object]:
        return {
            "variable_names": sorted(self.values),
            "warning_count": len(self.warnings),
        }


def _json_copy(value: object, label: str) -> Any:
    try:
        encoded = json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        return json.loads(encoded)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{label} must be JSON-compatible") from error


def _secret_strings(value: object) -> Tuple[str, ...]:
    found = set()

    def visit(item: object) -> None:
        if isinstance(item, Mapping):
            for nested in item.values():
                visit(nested)
        elif isinstance(item, (list, tuple)):
            for nested in item:
                visit(nested)
        elif isinstance(item, str) and item:
            found.add(item)

    visit(value)
    return tuple(sorted(found, key=lambda item: (-len(item), item)))


def _sensitive_key(key: str) -> bool:
    normalized = key.casefold().replace("-", "_").replace(" ", "_")
    return normalized in _SENSITIVE_KEYS or normalized.endswith(_SENSITIVE_SUFFIXES)


def redact_credentials(value: object, secret_values: Sequence[str] = ()) -> object:
    """Return a JSON-compatible copy with credential keys and canaries removed."""

    secrets = tuple(
        sorted({item for item in secret_values if item}, key=lambda item: (-len(item), item))
    )

    def redact(item: object, key: str = "") -> object:
        if key and _sensitive_key(key):
            return _REDACTED
        if isinstance(item, Mapping):
            if not all(isinstance(name, str) for name in item):
                raise ValueError("redacted object keys must be strings")
            return {name: redact(nested, name) for name, nested in item.items()}
        if isinstance(item, (list, tuple)):
            return [redact(nested) for nested in item]
        if isinstance(item, str):
            result = item
            for secret in secrets:
                result = result.replace(secret, _REDACTED)
            return result
        if item is None or isinstance(item, (bool, int, float)):
            return _json_copy(item, "redacted value")
        raise ValueError("redacted value must be JSON-compatible")

    return redact(value)


class CredentialResolver:
    """Resolve local credential references without retaining values in metadata."""

    def __init__(
        self,
        project_root: Path,
        *,
        environment: Mapping[str, str] | None = None,
        ephemeral: Mapping[str, object] | None = None,
    ) -> None:
        root = project_root.expanduser().resolve(strict=True)
        if not root.is_dir():
            raise ValueError("project root must be a directory")
        self._root = root
        self._environment = dict(os.environ if environment is None else environment)
        self._ephemeral = _json_copy(dict(ephemeral or {}), "ephemeral credentials")

    def resolve(self, reference: CredentialReference) -> ResolvedCredential:
        if reference.source is CredentialSource.ENV:
            value = self._environment.get(reference.location)
            if value is None or value == "":
                raise KeyError(f"credential {reference.identifier} is unavailable")
            return self._result(reference.identifier, value)
        if reference.source is CredentialSource.EPHEMERAL:
            if reference.location not in self._ephemeral:
                raise KeyError(f"credential {reference.identifier} is unavailable")
            return self._result(
                reference.identifier,
                _json_copy(self._ephemeral[reference.location], "credential value"),
            )
        return self._resolve_json(reference)

    def resolve_environment(
        self, bindings: Mapping[str, CredentialReference]
    ) -> ProcessCredentialEnvironment:
        """Build a process-local environment overlay from scalar references."""

        values: Dict[str, str] = {}
        secrets: Set[str] = set()
        warnings: List[str] = []
        for variable in sorted(bindings):
            if not variable or "=" in variable or "\x00" in variable:
                raise ValueError("credential environment variable name is invalid")
            resolved = self.resolve(bindings[variable])
            if not isinstance(resolved.value, str):
                raise ValueError("credential environment values must resolve to strings")
            if "\x00" in resolved.value:
                raise ValueError("credential environment values cannot contain NUL")
            values[variable] = resolved.value
            secrets.update(resolved.secret_values)
            warnings.extend(resolved.warnings)
        return ProcessCredentialEnvironment(
            values=values,
            secret_values=tuple(sorted(secrets, key=lambda item: (-len(item), item))),
            warnings=tuple(dict.fromkeys(warnings)),
        )

    @staticmethod
    def _result(
        identifier: str, value: object, warnings: Tuple[str, ...] = ()
    ) -> ResolvedCredential:
        return ResolvedCredential(identifier, value, _secret_strings(value), warnings)

    def _resolve_json(self, reference: CredentialReference) -> ResolvedCredential:
        path = self._confined_path(reference.location)
        if not path.is_file():
            raise KeyError(f"credential {reference.identifier} is unavailable")
        if not self._is_git_ignored(path):
            raise ValueError("credential JSON file must be ignored by Git")
        warnings = self._permission_warnings(path)
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise ValueError("credential JSON file is not readable valid JSON") from error
        for segment in reference.selector:
            if not isinstance(value, dict) or segment not in value:
                raise KeyError(f"credential {reference.identifier} selector is unavailable")
            value = value[segment]
        value = _json_copy(value, "credential value")
        return self._result(reference.identifier, value, warnings)

    def _confined_path(self, location: str) -> Path:
        relative = Path(location)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("credential path must remain inside the project")
        if relative.parts and relative.parts[0] == ".git":
            raise ValueError("credential path cannot use Git metadata")
        candidate = self._root / relative
        current = self._root
        for part in relative.parts:
            current = current / part
            if current.is_symlink():
                raise ValueError("credential path cannot contain a symlink")
        try:
            resolved = candidate.resolve(strict=True)
            resolved.relative_to(self._root)
        except (FileNotFoundError, ValueError) as error:
            raise ValueError("credential path must remain inside the project") from error
        return resolved

    def _is_git_ignored(self, path: Path) -> bool:
        relative = path.relative_to(self._root)
        try:
            result = subprocess.run(
                ["git", "check-ignore", "--quiet", "--", str(relative)],
                cwd=self._root,
                check=False,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise ValueError("unable to verify credential file Git-ignore status") from error
        if result.returncode not in (0, 1):
            raise ValueError("unable to verify credential file Git-ignore status")
        return result.returncode == 0

    @staticmethod
    def _permission_warnings(path: Path) -> Tuple[str, ...]:
        mode = stat.S_IMODE(path.stat().st_mode)
        if mode & 0o077:
            return (
                "credential file permissions are broader than owner-only "
                "(recommended: 0600)",
            )
        return ()
