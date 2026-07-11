from __future__ import annotations

import hashlib
import json
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from memwiki.coordinator_receipts import CommandReceipt, CommandReceiptStore

_CREDENTIAL_KEYS = {
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


def _canonical_json(value: object, label: str) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=True,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise ValueError(f"{label} must be JSON serializable") from error


def _redact_text(value: str, secrets: Sequence[str]) -> str:
    result = value
    for secret in sorted((item for item in secrets if item), key=len, reverse=True):
        result = result.replace(secret, "[REDACTED]")
    return result


def _safe_metadata(value: object, secrets: Sequence[str], key: str = "") -> object:
    normalized = key.lower().replace("-", "_")
    if normalized in _CREDENTIAL_KEYS:
        return "[REDACTED]"
    if isinstance(value, Mapping):
        if not all(isinstance(item, str) for item in value):
            raise ValueError("metadata keys must be strings")
        return {
            item: _safe_metadata(value[item], secrets, item)
            for item in sorted(value)
        }
    if isinstance(value, (list, tuple)):
        return [_safe_metadata(item, secrets) for item in value]
    if isinstance(value, str):
        return _redact_text(value, secrets)
    if value is None or isinstance(value, (bool, int, float)):
        _canonical_json(value, "metadata")
        return value
    raise ValueError("metadata must be JSON serializable")


@dataclass(frozen=True)
class AuthorityDecision:
    allowed: bool
    authority: str
    reason: str

    def __post_init__(self) -> None:
        if not self.authority.strip() or not self.reason.strip():
            raise ValueError("authority and reason must be non-empty")


@dataclass(frozen=True)
class InvocationRequest:
    """A bounded invocation envelope. Payload contents remain process-local."""

    idempotency_key: str
    capability_id: str
    capability_version: str
    operation: str
    payload: Mapping[str, Any]
    metadata: Mapping[str, Any]
    timeout_seconds: float
    max_output_bytes: int
    max_input_bytes: int = 1_048_576
    retry_limit: int = 0

    def __post_init__(self) -> None:
        for name in (
            "idempotency_key",
            "capability_id",
            "capability_version",
            "operation",
        ):
            if not getattr(self, name).strip():
                raise ValueError(f"{name} must be non-empty")
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
        if (
            not isinstance(self.max_input_bytes, int)
            or isinstance(self.max_input_bytes, bool)
            or self.max_input_bytes <= 0
        ):
            raise ValueError("input limit must be a positive integer")
        if (
            not isinstance(self.retry_limit, int)
            or isinstance(self.retry_limit, bool)
            or self.retry_limit < 0
        ):
            raise ValueError("retry limit must be a non-negative integer")
        encoded_payload = _canonical_json(self.payload, "payload")
        if len(encoded_payload) > self.max_input_bytes:
            raise ValueError("payload exceeds the input limit")
        _canonical_json(self.metadata, "metadata")

    @property
    def input_sha256(self) -> str:
        return hashlib.sha256(_canonical_json(self.payload, "payload")).hexdigest()


@dataclass(frozen=True)
class InvocationResultPacket:
    receipt: CommandReceipt
    outcome: str
    output: str
    output_sha256: str
    output_truncated: bool
    timed_out: bool = False
    cancelled: bool = False
    error: Optional[str] = None


class RetryableInvocationError(RuntimeError):
    """A provider rejected work before accepting any external effect."""


Executor = Callable[[Mapping[str, Any]], object]
OutputValidator = Callable[[object], bool]


class InvocationProtocol:
    """Durable external invocation lifecycle built on command receipts."""

    def __init__(self, receipts: CommandReceiptStore) -> None:
        self._receipts = receipts

    def plan(
        self,
        request: InvocationRequest,
        authority: AuthorityDecision,
        *,
        secret_values: Sequence[str] = (),
    ) -> CommandReceipt:
        if not authority.allowed:
            raise PermissionError(authority.reason)
        metadata = _safe_metadata(request.metadata, secret_values)
        assert isinstance(metadata, dict)
        arguments: Dict[str, Any] = {
            "authority": authority.authority,
            "capability_id": request.capability_id,
            "capability_version": request.capability_version,
            "input_sha256": request.input_sha256,
            "max_input_bytes": request.max_input_bytes,
            "max_output_bytes": request.max_output_bytes,
            "metadata": metadata,
            "operation": request.operation,
            "retry_limit": request.retry_limit,
            "timeout_seconds": request.timeout_seconds,
        }
        return self._receipts.plan(
            request.idempotency_key,
            "external-capability-invocation",
            arguments,
        )

    def start(self, request: InvocationRequest) -> CommandReceipt:
        return self._receipts.transition(request.idempotency_key, "started")

    def reconcile_interrupted(self) -> List[CommandReceipt]:
        return self._receipts.reconcile_interrupted()

    def invoke(
        self,
        request: InvocationRequest,
        authority: AuthorityDecision,
        executor: Executor,
        *,
        validate_output: Optional[OutputValidator] = None,
        cancelled: Optional[Callable[[], bool]] = None,
        secret_values: Sequence[str] = (),
    ) -> InvocationResultPacket:
        receipt = self.plan(request, authority, secret_values=secret_values)
        if receipt.status != "planned":
            return self._packet_from_receipt(receipt)
        if cancelled is not None and cancelled():
            receipt = self._receipts.transition(request.idempotency_key, "cancelled")
            return InvocationResultPacket(receipt, "cancelled", "", "", False, cancelled=True)

        self.start(request)
        output, error, timed_out = self._execute(request, executor, cancelled)
        if timed_out:
            receipt = self._receipts.transition(
                request.idempotency_key,
                "outcome_unknown",
                error="capability invocation timed out; external outcome is unknown",
            )
            return InvocationResultPacket(
                receipt,
                "outcome_unknown",
                "",
                "",
                False,
                timed_out=True,
                error=receipt.error,
            )
        if error == "cancelled":
            receipt = self._receipts.transition(request.idempotency_key, "cancelled")
            return InvocationResultPacket(receipt, "cancelled", "", "", False, cancelled=True)
        if error is not None:
            receipt = self._receipts.transition(
                request.idempotency_key,
                "outcome_unknown",
                error=_redact_text(error, secret_values),
            )
            return InvocationResultPacket(
                receipt, "outcome_unknown", "", "", False, error=receipt.error
            )

        encoded = _canonical_json(output, "capability output")
        redacted = _redact_text(encoded.decode("utf-8"), secret_values).encode("utf-8")
        digest = hashlib.sha256(redacted).hexdigest()
        bounded = redacted[: request.max_output_bytes].decode("utf-8", errors="ignore")
        truncated = len(redacted) > request.max_output_bytes
        schema_valid = validate_output is None or validate_output(output)
        result: Dict[str, Any] = {
            "output": bounded,
            "output_sha256": digest,
            "output_truncated": truncated,
        }
        if schema_valid:
            receipt = self._receipts.transition(
                request.idempotency_key, "succeeded", result=result
            )
            return InvocationResultPacket(receipt, "succeeded", bounded, digest, truncated)
        message = "capability output failed schema validation"
        receipt = self._receipts.transition(
            request.idempotency_key, "failed", result=result, error=message
        )
        return InvocationResultPacket(
            receipt, "failed", bounded, digest, truncated, error=message
        )

    @staticmethod
    def _execute(
        request: InvocationRequest,
        executor: Executor,
        cancelled: Optional[Callable[[], bool]],
    ) -> Tuple[object, Optional[str], bool]:
        result: List[object] = []
        errors: List[BaseException] = []

        def run() -> None:
            attempts = 0
            while True:
                try:
                    result.append(executor(request.payload))
                    return
                except RetryableInvocationError as error:
                    if attempts >= request.retry_limit:
                        errors.append(error)
                        return
                    attempts += 1
                except BaseException as error:
                    errors.append(error)
                    return

        worker = threading.Thread(target=run, daemon=True)
        worker.start()
        deadline = time.monotonic() + request.timeout_seconds
        while worker.is_alive():
            if cancelled is not None and cancelled():
                return None, "cancelled", False
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None, None, True
            worker.join(min(0.01, remaining))
        if errors:
            return None, f"{type(errors[0]).__name__}: {errors[0]}", False
        return result[0], None, False

    @staticmethod
    def _packet_from_receipt(receipt: CommandReceipt) -> InvocationResultPacket:
        result = receipt.result or {}
        return InvocationResultPacket(
            receipt=receipt,
            outcome=receipt.status,
            output=str(result.get("output", "")),
            output_sha256=str(result.get("output_sha256", "")),
            output_truncated=bool(result.get("output_truncated", False)),
            timed_out=receipt.status == "outcome_unknown" and bool(receipt.error),
            cancelled=receipt.status == "cancelled",
            error=receipt.error,
        )
