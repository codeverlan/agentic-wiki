from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, List

MAX_STRING_LENGTH = 100_000


@dataclass(frozen=True)
class ContentSafetyFinding:
    rule_id: str
    path: str
    classification: str
    severity: str = "block"


_PRIVATE_KEY = re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----", re.IGNORECASE)
_JWT = re.compile(r"(?<![A-Za-z0-9_-])eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")
_PROVIDER_TOKEN = re.compile(r"(?<![A-Za-z0-9])(?:AKIA[0-9A-Z]{16}|gh[pousr]_[A-Za-z0-9]{20,})")
_BEARER = re.compile(r"\bBearer\s+([^\s,;]+)", re.IGNORECASE)
_ASSIGNMENT = re.compile(
    r"\b(?:password|passwd|api[_-]?key|secret|access[_-]?token|auth[_-]?token)\s*[:=]\s*([^\s,;]+)",
    re.IGNORECASE,
)
_SSN = re.compile(r"(?<!\d)(\d{3})-(\d{2})-(\d{4})(?!\d)")
_EMAIL = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
_PHONE = re.compile(r"(?<!\d)(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]\d{3}[-.\s]\d{4}(?!\d)")
_IDENTIFYING_CONTEXT = re.compile(r"\b(?:patient|client|member|consumer)\b", re.IGNORECASE)


def _is_placeholder(value: str) -> bool:
    normalized = value.strip().lower()
    return (
        not normalized
        or normalized in {"token", "secret", "password", "changeme", "example"}
        or "${" in value
        or ("<" in value and ">" in value)
        or "example.com" in normalized
        or set(normalized) <= {"x", "*", "_", "-"}
    )


def _scan_string(value: str, path: str) -> List[ContentSafetyFinding]:
    if len(value) > MAX_STRING_LENGTH:
        return [ContentSafetyFinding("content_size_limit", path, "oversized_content")]
    findings: List[ContentSafetyFinding] = []
    if _PRIVATE_KEY.search(value):
        findings.append(ContentSafetyFinding("private_key", path, "credential"))
    if _JWT.search(value):
        findings.append(ContentSafetyFinding("jwt", path, "credential"))
    if _PROVIDER_TOKEN.search(value):
        findings.append(ContentSafetyFinding("provider_token", path, "credential"))
    for match in _BEARER.finditer(value):
        if not _is_placeholder(match.group(1)):
            findings.append(ContentSafetyFinding("bearer_token", path, "credential"))
    for match in _ASSIGNMENT.finditer(value):
        if not _is_placeholder(match.group(1)):
            findings.append(ContentSafetyFinding("credential_assignment", path, "credential"))
    for match in _SSN.finditer(value):
        area, group, serial = match.groups()
        if area not in {"000", "666"} and not area.startswith("9") and group != "00" and serial != "0000":
            findings.append(ContentSafetyFinding("ssn", path, "direct_identifier"))
    if _IDENTIFYING_CONTEXT.search(value) and _EMAIL.search(value) and "example.com" not in value.lower():
        findings.append(ContentSafetyFinding("contextual_email", path, "direct_identifier"))
    if _IDENTIFYING_CONTEXT.search(value) and _PHONE.search(value) and "555" not in value:
        findings.append(ContentSafetyFinding("contextual_phone", path, "direct_identifier"))
    return findings


def scan_memory_content(value: Any, path: str = "$") -> List[ContentSafetyFinding]:
    findings: List[ContentSafetyFinding] = []
    if isinstance(value, dict):
        for key, item in value.items():
            key_text = str(key)
            findings.extend(_scan_string(key_text, f"{path}.<key>"))
            findings.extend(scan_memory_content(item, f"{path}.{key_text}"))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            findings.extend(scan_memory_content(item, f"{path}[{index}]"))
    elif isinstance(value, str):
        findings.extend(_scan_string(value, path))
    return findings


def validate_safe_memory_content(value: Any) -> None:
    findings = scan_memory_content(value)
    if findings:
        finding = findings[0]
        raise ValueError(
            f"Agent memory prohibited content detected at {finding.path}: "
            f"{finding.rule_id} ({finding.classification})"
        )
