from __future__ import annotations

import hashlib
import html
import json
import os
import tempfile
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Mapping, Optional, Protocol, cast

CURRENT_SCHEMA_VERSION = 1


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value)).hexdigest()


def _atomic_json(path: Path, value: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(_canonical(value) + b"\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


@dataclass(frozen=True)
class RuntimeTick:
    state: Dict[str, object]
    intents_applied: int
    terminal: bool = False

    def __post_init__(self) -> None:
        if self.intents_applied < 0:
            raise ValueError("intents_applied must be non-negative")


class CoordinatorRuntime(ABC):
    """Provider-neutral reconciliation boundary used by the public API."""

    @abstractmethod
    def tick(self, state: Dict[str, object]) -> RuntimeTick:
        """Perform one reconciliation tick without owning lifecycle persistence."""


class WorkspaceRoot(Protocol):
    @property
    def root(self) -> Path:
        """Return the workspace root."""


def _stored_int(value: object) -> int:
    return cast(int, value)


class CoordinatorAPI:
    """Durable public control surface for an autonomous coordinator run."""

    def __init__(
        self,
        workspace: Path | str | WorkspaceRoot,
        *,
        runtime: Optional[CoordinatorRuntime] = None,
    ) -> None:
        root = workspace if isinstance(workspace, (str, os.PathLike)) else workspace.root
        self.workspace = Path(str(root)).resolve()
        self.directory = self.workspace / ".memwiki" / "coordinator"
        self.control_path = self.directory / "control.json"
        self.view_path = self.directory / "status.html"
        self.runtime = runtime

    def initialize(self, *, run_id: str, max_workers: int = 6) -> Dict[str, object]:
        if not run_id.strip():
            raise ValueError("run_id must be a non-empty string")
        if isinstance(max_workers, bool) or not 1 <= max_workers <= 6:
            raise ValueError("max_workers must be from 1 through 6")
        if self.control_path.exists():
            current = self.status()
            if current["run_id"] == run_id and current["max_workers"] == max_workers:
                return current
            raise ValueError("coordinator is already initialized with different settings")
        state: Dict[str, object] = {
            "desired_state": "running",
            "intents_applied": 0,
            "max_workers": max_workers,
            "revision": 0,
            "run_id": run_id,
            "schema_version": CURRENT_SCHEMA_VERSION,
            "status": "ready",
            "stop_reason": "",
            "ticks": 0,
        }
        self._write(state)
        return state

    def status(self) -> Dict[str, object]:
        if not self.control_path.exists():
            raise FileNotFoundError("coordinator is not initialized")
        value = json.loads(self.control_path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("coordinator control state must be an object")
        self._validate(value)
        return dict(value)

    def tick(self) -> Dict[str, object]:
        runtime = self._require_runtime()
        current = self.status()
        if current["desired_state"] == "stopped":
            return current
        result = runtime.tick(current)
        updated = dict(result.state)
        updated.update(
            {
                "intents_applied": _stored_int(current["intents_applied"]) + result.intents_applied,
                "revision": _stored_int(current["revision"]) + 1,
                "ticks": _stored_int(current["ticks"]) + 1,
            }
        )
        self._write(updated)
        return updated

    def run(self, *, max_ticks: Optional[int] = None) -> Dict[str, object]:
        self._require_runtime()
        if max_ticks is not None and (isinstance(max_ticks, bool) or max_ticks < 1):
            raise ValueError("max_ticks must be a positive integer or null")
        started_ticks = _stored_int(self.status()["ticks"])
        started_intents = _stored_int(self.status()["intents_applied"])
        while True:
            state = self.tick()
            elapsed = _stored_int(state["ticks"]) - started_ticks
            if state["status"] in {"completed", "failed", "blocked"} or state["desired_state"] == "stopped":
                break
            if max_ticks is not None and elapsed >= max_ticks:
                break
        return {
            **state,
            "intents_applied": _stored_int(state["intents_applied"]) - started_intents,
            "ticks": _stored_int(state["ticks"]) - started_ticks,
        }

    def stop(self, *, reason: str) -> Dict[str, object]:
        state = self.status()
        state.update({"desired_state": "stopped", "stop_reason": reason, "status": "stopped"})
        self._write(state)
        return state

    def resume(self) -> Dict[str, object]:
        state = self.status()
        state.update({"desired_state": "running", "stop_reason": "", "status": "ready"})
        self._write(state)
        return state

    def verify(self) -> Dict[str, object]:
        errors = []
        try:
            state = self.status()
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            errors.append(str(exc))
            state = None
        return {
            "errors": sorted(errors),
            "state_digest": _digest(state) if state is not None else None,
            "valid": not errors,
        }

    def migrate(self) -> Dict[str, object]:
        state = self.status()
        version = _stored_int(state["schema_version"])
        if version > CURRENT_SCHEMA_VERSION:
            raise ValueError(f"future coordinator schema version {version}")
        return {
            "actions": [],
            "from_version": version,
            "migrated": False,
            "to_version": CURRENT_SCHEMA_VERSION,
        }

    def render(self, *, output: Optional[Path | str] = None) -> Dict[str, object]:
        state = self.status()
        target = Path(output).resolve() if output is not None else self.view_path
        payload = json.dumps(state, sort_keys=True).replace("<", "\\u003c")
        rows = "".join(
            f"<tr><th>{html.escape(str(key))}</th><td>{html.escape(str(value))}</td></tr>"
            for key, value in sorted(state.items())
        )
        document = (
            "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
            "<title>Coordinator status</title>"
            f"<script type=\"application/ld+json\">{payload}</script></head>"
            f"<body><main><h1>Coordinator status</h1><table><tbody>{rows}</tbody></table></main></body></html>\n"
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(document, encoding="utf-8")
        return {"path": str(target), "source_digest": _digest(state), "status": state["status"]}

    def _require_runtime(self) -> CoordinatorRuntime:
        if self.runtime is None:
            raise RuntimeError("coordinator runtime adapter is not configured")
        return self.runtime

    def _write(self, state: Dict[str, object]) -> None:
        self._validate(state)
        _atomic_json(self.control_path, state)

    @staticmethod
    def _validate(state: Mapping[str, object]) -> None:
        required = {
            "desired_state",
            "intents_applied",
            "max_workers",
            "revision",
            "run_id",
            "schema_version",
            "status",
            "stop_reason",
            "ticks",
        }
        if set(state) != required:
            raise ValueError("coordinator control state fields do not match schema")
        if state["schema_version"] != CURRENT_SCHEMA_VERSION:
            raise ValueError("unsupported coordinator control schema version")
        if not isinstance(state["run_id"], str) or not state["run_id"]:
            raise ValueError("run_id must be a non-empty string")
        workers = state["max_workers"]
        if isinstance(workers, bool) or not isinstance(workers, int) or not 1 <= workers <= 6:
            raise ValueError("max_workers must be from 1 through 6")
        for field in ("revision", "ticks", "intents_applied"):
            value = state[field]
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{field} must be a non-negative integer")
        if state["desired_state"] not in {"running", "stopped"}:
            raise ValueError("desired_state is invalid")
        if state["status"] not in {"ready", "running", "stopped", "blocked", "completed", "failed"}:
            raise ValueError("status is invalid")
        if not isinstance(state["stop_reason"], str):
            raise ValueError("stop_reason must be a string")


__all__ = ["CoordinatorAPI", "CoordinatorRuntime", "RuntimeTick"]
