from __future__ import annotations

import hashlib
import json
import os
import re
import signal
import subprocess
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import BinaryIO, Callable, Dict, Mapping, Optional, Sequence, Tuple

_ENVIRONMENT_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_BASE_ENVIRONMENT = {
    "LANG": "C.UTF-8",
    "LC_ALL": "C.UTF-8",
    "PATH": os.defpath,
    "TZ": "UTC",
}
_OUTCOMES = {"passed", "failed", "timed_out", "signalled", "cancelled"}


@dataclass(frozen=True)
class ValidationCommand:
    argv: Tuple[str, ...]
    timeout_seconds: float
    max_output_bytes: int
    cwd: str = "."
    env: Optional[Mapping[str, str]] = None

    def __post_init__(self) -> None:
        if not self.argv or not all(isinstance(item, str) and item for item in self.argv):
            raise ValueError("argv must contain non-empty strings")
        if any(character.isspace() for character in self.argv[0]):
            raise ValueError("argv executable must be one token, not a shell command")
        if (
            not isinstance(self.timeout_seconds, (int, float))
            or isinstance(self.timeout_seconds, bool)
            or self.timeout_seconds <= 0
        ):
            raise ValueError("timeout must be positive")
        if (
            not isinstance(self.max_output_bytes, int)
            or isinstance(self.max_output_bytes, bool)
            or self.max_output_bytes <= 0
        ):
            raise ValueError("output limit must be a positive integer")
        if not isinstance(self.cwd, str) or not self.cwd:
            raise ValueError("working directory must be a relative path")
        if self.env is not None:
            for name, value in self.env.items():
                if not _ENVIRONMENT_NAME.fullmatch(name) or not isinstance(value, str):
                    raise ValueError("environment must contain valid string names and values")


@dataclass(frozen=True)
class ValidationResult:
    receipt_id: str
    outcome: str
    exit_code: Optional[int]
    signal_number: Optional[int]
    timed_out: bool
    cancelled: bool
    stdout: str
    stderr: str
    stdout_sha256: str
    stderr_sha256: str
    stdout_truncated: bool
    stderr_truncated: bool
    duration_ms: int
    evidence: Dict[str, object]
    slice_complete: bool = False

    def __post_init__(self) -> None:
        if self.outcome not in _OUTCOMES:
            raise ValueError(f"unknown validation outcome: {self.outcome}")

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)


class _BoundedCollector:
    def __init__(self, limit: int) -> None:
        self._limit = limit
        self._content = bytearray()
        self.truncated = False

    def drain(self, stream: BinaryIO) -> None:
        while True:
            chunk = stream.read(8192)
            if not chunk:
                return
            available = self._limit - len(self._content)
            if available > 0:
                self._content.extend(chunk[:available])
            if len(chunk) > available:
                self.truncated = True

    @property
    def content(self) -> bytes:
        return bytes(self._content)


class ValidationRunner:
    """Execute validation commands with bounded, redacted evidence receipts."""

    def __init__(self, workspace_root: Path) -> None:
        root = Path(workspace_root)
        if root.is_symlink():
            raise ValueError("workspace root must not be a symbolic link")
        self._root = root.resolve(strict=True)

    def run(
        self,
        command: ValidationCommand,
        *,
        secret_values: Sequence[str] = (),
        cancelled: Optional[Callable[[], bool]] = None,
    ) -> ValidationResult:
        cwd = self._resolve_cwd(command.cwd)
        environment = dict(_BASE_ENVIRONMENT)
        if command.env:
            environment.update(command.env)
        secrets = tuple(value for value in secret_values if value)
        started = time.monotonic()
        process = subprocess.Popen(
            command.argv,
            cwd=cwd,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=os.name == "posix",
        )
        assert process.stdout is not None
        assert process.stderr is not None
        stdout = _BoundedCollector(command.max_output_bytes)
        stderr = _BoundedCollector(command.max_output_bytes)
        readers = [
            threading.Thread(target=stdout.drain, args=(process.stdout,), daemon=True),
            threading.Thread(target=stderr.drain, args=(process.stderr,), daemon=True),
        ]
        for reader in readers:
            reader.start()

        disposition: Optional[str] = None
        termination_signal: Optional[int] = None
        deadline = started + command.timeout_seconds
        while process.poll() is None:
            if cancelled is not None and cancelled():
                disposition = "cancelled"
                termination_signal = self._terminate(process)
                break
            if time.monotonic() >= deadline:
                disposition = "timed_out"
                termination_signal = self._terminate(process)
                break
            time.sleep(min(0.01, max(0.0, deadline - time.monotonic())))
        process.wait()
        for reader in readers:
            reader.join()

        return_code = process.returncode
        if disposition is None:
            if return_code < 0:
                disposition = "signalled"
                termination_signal = -return_code
            elif return_code == 0:
                disposition = "passed"
            else:
                disposition = "failed"

        stdout_text, stdout_redaction_truncated = self._redacted_bounded_text(
            stdout.content, secrets, command.max_output_bytes
        )
        stderr_text, stderr_redaction_truncated = self._redacted_bounded_text(
            stderr.content, secrets, command.max_output_bytes
        )
        stdout_truncated = stdout.truncated or stdout_redaction_truncated
        stderr_truncated = stderr.truncated or stderr_redaction_truncated
        stdout_hash = hashlib.sha256(stdout_text.encode()).hexdigest()
        stderr_hash = hashlib.sha256(stderr_text.encode()).hexdigest()
        evidence: Dict[str, object] = {
            "argv": [self._redact(item, secrets) for item in command.argv],
            "cwd": command.cwd,
            "environment": {
                key: self._redact(value, secrets) for key, value in sorted(environment.items())
            },
            "outcome": disposition,
            "exit_code": return_code if return_code >= 0 else None,
            "signal_number": termination_signal,
            "stdout_sha256": stdout_hash,
            "stderr_sha256": stderr_hash,
            "stdout_truncated": stdout_truncated,
            "stderr_truncated": stderr_truncated,
        }
        receipt_id = hashlib.sha256(
            json.dumps(evidence, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        return ValidationResult(
            receipt_id=receipt_id,
            outcome=disposition,
            exit_code=return_code if return_code >= 0 else None,
            signal_number=termination_signal,
            timed_out=disposition == "timed_out",
            cancelled=disposition == "cancelled",
            stdout=stdout_text,
            stderr=stderr_text,
            stdout_sha256=stdout_hash,
            stderr_sha256=stderr_hash,
            stdout_truncated=stdout_truncated,
            stderr_truncated=stderr_truncated,
            duration_ms=max(0, round((time.monotonic() - started) * 1000)),
            evidence=evidence,
        )

    def _resolve_cwd(self, relative: str) -> Path:
        logical = PurePosixPath(relative)
        if logical.is_absolute() or ".." in logical.parts or "\\" in relative:
            raise ValueError("working directory must be confined to the workspace root")
        candidate = self._root.joinpath(*logical.parts)
        if not candidate.is_dir() or candidate.is_symlink():
            raise ValueError("working directory must be an existing non-symlink directory")
        resolved = candidate.resolve(strict=True)
        try:
            resolved.relative_to(self._root)
        except ValueError:
            raise ValueError("working directory escapes the workspace root") from None
        return resolved

    @staticmethod
    def _terminate(process: subprocess.Popen[bytes]) -> int:
        requested = signal.SIGTERM
        if os.name == "posix":
            os.killpg(process.pid, requested)
        else:
            process.terminate()
        try:
            process.wait(timeout=0.5)
        except subprocess.TimeoutExpired:
            requested = signal.SIGKILL
            if os.name == "posix":
                os.killpg(process.pid, requested)
            else:
                process.kill()
        return requested

    @staticmethod
    def _redact(value: str, secrets: Sequence[str]) -> str:
        redacted = value
        for secret in sorted(secrets, key=len, reverse=True):
            redacted = redacted.replace(secret, "[REDACTED]")
        return redacted

    @classmethod
    def _redacted_bounded_text(
        cls, content: bytes, secrets: Sequence[str], limit: int
    ) -> Tuple[str, bool]:
        redacted = cls._redact(content.decode("utf-8", errors="replace"), secrets)
        encoded = redacted.encode("utf-8")
        if len(encoded) <= limit:
            return redacted, False
        bounded = encoded[:limit].decode("utf-8", errors="ignore")
        return bounded, True
