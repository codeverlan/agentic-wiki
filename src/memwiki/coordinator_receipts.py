from __future__ import annotations

import fcntl
import hashlib
import json
import os
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any, Dict, List, Optional


class ReceiptConflictError(ValueError):
    pass


TERMINAL_RECEIPT_STATUSES = {"succeeded", "failed", "cancelled", "outcome_unknown"}


@dataclass(frozen=True)
class CommandReceipt:
    idempotency_key: str
    command: str
    arguments: Dict[str, Any]
    command_hash: str
    status: str
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None

    @classmethod
    def from_dict(cls, value: Dict[str, Any]) -> "CommandReceipt":
        return cls(**value)


def _command_hash(command: str, arguments: Dict[str, Any]) -> str:
    try:
        encoded = json.dumps(
            {"arguments": arguments, "command": command},
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError("command arguments must be JSON serializable") from exc
    return hashlib.sha256(encoded).hexdigest()


class CommandReceiptStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.lock_path = path.with_suffix(path.suffix + ".lock")

    def plan(self, idempotency_key: str, command: str, arguments: Dict[str, Any]) -> CommandReceipt:
        if not idempotency_key or not command:
            raise ValueError("idempotency_key and command are required")
        expected_hash = _command_hash(command, arguments)
        with self._locked():
            receipts = self._read()
            existing = receipts.get(idempotency_key)
            if existing is not None:
                if existing.command_hash != expected_hash:
                    raise ReceiptConflictError("idempotency key is already bound to another command")
                return existing
            receipt = CommandReceipt(
                idempotency_key=idempotency_key,
                command=command,
                arguments=json.loads(json.dumps(arguments)),
                command_hash=expected_hash,
                status="planned",
            )
            receipts[idempotency_key] = receipt
            self._write(receipts)
            return receipt

    def transition(
        self,
        idempotency_key: str,
        status: str,
        *,
        result: Optional[Dict[str, Any]] = None,
        error: Optional[str] = None,
    ) -> CommandReceipt:
        allowed = {
            "planned": {"started", "cancelled"},
            "started": {"succeeded", "failed", "cancelled", "outcome_unknown"},
        }
        with self._locked():
            receipts = self._read()
            try:
                current = receipts[idempotency_key]
            except KeyError:
                raise KeyError(f"unknown command receipt: {idempotency_key}") from None
            if current.status in TERMINAL_RECEIPT_STATUSES:
                raise ReceiptConflictError("terminal command receipt cannot transition")
            if status not in allowed[current.status]:
                raise ReceiptConflictError(f"illegal command receipt transition: {current.status} -> {status}")
            updated = replace(current, status=status, result=result, error=error)
            receipts[idempotency_key] = updated
            self._write(receipts)
            return updated

    def get(self, idempotency_key: str) -> CommandReceipt:
        with self._locked():
            return self._read()[idempotency_key]

    def should_execute(self, idempotency_key: str) -> bool:
        with self._locked():
            receipt = self._read().get(idempotency_key)
            return receipt is None or receipt.status == "planned"

    def reconcile_interrupted(self) -> List[CommandReceipt]:
        with self._locked():
            receipts = self._read()
            changed = []
            for key in sorted(receipts):
                if receipts[key].status == "started":
                    receipts[key] = replace(receipts[key], status="outcome_unknown")
                    changed.append(receipts[key])
            if changed:
                self._write(receipts)
            return changed

    def _read(self) -> Dict[str, CommandReceipt]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        if not isinstance(raw, dict):
            raise ValueError("receipt store must contain an object")
        return {key: CommandReceipt.from_dict(value) for key, value in raw.items()}

    def _write(self, receipts: Dict[str, CommandReceipt]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        content = (
            json.dumps(
                {key: asdict(value) for key, value in sorted(receipts.items())},
                ensure_ascii=True,
                indent=2,
                sort_keys=True,
            ).encode("utf-8")
            + b"\n"
        )
        temporary = self.path.with_name(self.path.name + ".tmp")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(descriptor, content)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        os.replace(temporary, self.path)
        parent = os.open(self.path.parent, os.O_RDONLY)
        try:
            os.fsync(parent)
        finally:
            os.close(parent)

    class _Lock:
        def __init__(self, path: Path) -> None:
            path.parent.mkdir(parents=True, exist_ok=True)
            self.stream = path.open("a+b")

        def __enter__(self) -> None:
            fcntl.flock(self.stream.fileno(), fcntl.LOCK_EX)

        def __exit__(self, *args: object) -> None:
            fcntl.flock(self.stream.fileno(), fcntl.LOCK_UN)
            self.stream.close()

    def _locked(self) -> "CommandReceiptStore._Lock":
        return self._Lock(self.lock_path)
