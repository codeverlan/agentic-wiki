from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import List, Set

from bs4 import BeautifulSoup

from memwiki.claims import validate_claim
from memwiki.html import validate_html_file
from memwiki.manifest import read_jsonl
from memwiki.policy import config_bool, is_clinical_phi
from memwiki.workspace import Workspace


def _normalized_text(value: str) -> str:
    return " ".join(value.split())


def _claim_evidence_errors(workspace: Workspace, claim: dict[str, object]) -> List[str]:
    if claim.get("clinical_claim_type") == "clinical_guidance":
        return []
    source_id = str(claim.get("source_id", ""))
    provenance = claim.get("provenance")
    locator = provenance.get("source_locator") if isinstance(provenance, dict) else None
    locator_value = locator.get("value") if isinstance(locator, dict) else None
    locator_type = locator.get("type") if isinstance(locator, dict) else None
    if isinstance(locator_value, str) and locator_value != "extracted/text.txt":
        if locator_type != "json":
            return []
        source = next(
            (
                record
                for record in read_jsonl(workspace.path("manifests/sources.jsonl"))
                if record.get("source_id") == source_id
            ),
            None,
        )
        if source is None:
            return []
        raw_path = workspace.path(str(source.get("raw_path", "")))
        try:
            payload = json.loads(raw_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return [f"claim {claim.get('claim_id')} JSON source evidence is unreadable"]
        if not isinstance(payload, dict) or locator_value.split(".", 1)[0] not in payload:
            return [f"claim {claim.get('claim_id')} source_locator does not resolve"]
        return []
    claim_text = str(claim.get("text", ""))
    prefix = f"Source {source_id} states: "
    if not claim_text.startswith(prefix):
        return [f"claim {claim.get('claim_id')} is not supported by extracted source evidence"]
    evidence = _normalized_text(claim_text[len(prefix) :])
    extracted_path = workspace.path(f".memwiki/extracted/{source_id}/text.txt")
    if not extracted_path.is_file():
        return [f"claim {claim.get('claim_id')} extracted source evidence is missing"]
    extracted = _normalized_text(extracted_path.read_text(encoding="utf-8"))
    if evidence == "No extractable text." and not extracted:
        return []
    if evidence not in extracted:
        return [f"claim {claim.get('claim_id')} is not supported by extracted source evidence"]
    return []


@dataclass(frozen=True)
class LintResult:
    errors: List[str]
    warnings: List[str]

    @property
    def ok(self) -> bool:
        return not self.errors


def _manifest_errors(workspace: Workspace, base: Path) -> List[str]:
    errors: List[str] = []
    claims_path = base / "manifests" / "claims.jsonl"
    pages_path = base / "manifests" / "pages.jsonl"
    links_path = base / "manifests" / "links.jsonl"
    source_ids = {record.get("source_id") for record in read_jsonl(workspace.path("manifests/sources.jsonl"))}
    page_ids: Set[str] = set()
    claim_ids: Set[str] = set()
    record_ids: Set[str] = set()
    for page in read_jsonl(pages_path):
        for key in ["page_id", "title", "slug", "page_type", "review_status", "html_path"]:
            if key not in page:
                errors.append(f"page record missing {key}")
        page_ids.add(str(page.get("page_id")))
        html_path = base / "wiki" / str(page.get("html_path", ""))
        if not html_path.exists():
            errors.append(f"page HTML missing for {page.get('page_id')}: {html_path}")
    for claim in read_jsonl(claims_path):
        errors.extend(validate_claim(claim))
        errors.extend(_claim_evidence_errors(workspace, claim))
        claim_ids.add(str(claim.get("claim_id")))
        if isinstance(claim.get("record_id"), str) and claim["record_id"]:
            record_ids.add(str(claim["record_id"]))
        if claim.get("source_id") not in source_ids:
            errors.append(f"claim {claim.get('claim_id')} references unknown source")
    all_claims = read_jsonl(claims_path)
    all_claim_ids = {str(claim.get("claim_id")) for claim in all_claims}
    for claim in all_claims:
        if claim.get("clinical_claim_type") == "clinical_guidance":
            for cited_claim_id in claim.get("cited_claim_ids", []):
                if str(cited_claim_id) not in all_claim_ids:
                    errors.append(f"clinical guidance {claim.get('claim_id')} cites unknown claim {cited_claim_id}")
    if base.resolve() != workspace.root.resolve():
        canonical_pages = read_jsonl(workspace.path("manifests/pages.jsonl"))
        canonical_claims = read_jsonl(workspace.path("manifests/claims.jsonl"))
        page_ids.update(str(page.get("page_id")) for page in canonical_pages)
        claim_ids.update(str(claim.get("claim_id")) for claim in canonical_claims)
        record_ids.update(
            str(claim["record_id"])
            for claim in canonical_claims
            if isinstance(claim.get("record_id"), str) and claim["record_id"]
        )
    known_ids = page_ids | claim_ids | record_ids
    for link in read_jsonl(links_path):
        if link.get("from_id") not in known_ids:
            errors.append(f"link from_id not found: {link.get('from_id')}")
        if link.get("to_id") not in known_ids:
            errors.append(f"link to_id not found: {link.get('to_id')}")
    return errors


def _link_errors(base: Path) -> List[str]:
    errors: List[str] = []
    html_files = list((base / "wiki").glob("*.html"))
    html_names = {path.name for path in html_files}
    for path in html_files:
        soup = BeautifulSoup(path.read_text(encoding="utf-8"), "html.parser")
        for anchor in soup.find_all("a"):
            href = anchor.get("href")
            if not isinstance(href, str) or not href or href.startswith(("http:", "https:", "#", "../")):
                continue
            target = href.split("#", 1)[0]
            if target in {"index.html", "contradictions.html"}:
                continue
            if target and target not in html_names:
                errors.append(f"{path}: broken internal link {href}")
    return errors


def lint_workspace(workspace: Workspace, base: Path | None = None) -> LintResult:
    workspace.require()
    lint_base = base or workspace.root
    errors: List[str] = []
    warnings: List[str] = []
    if is_clinical_phi(workspace.config_path):
        if config_bool(workspace.config_path, "models", "allow_remote", False):
            errors.append("clinical PHI workspaces must keep models.allow_remote = false")
        if config_bool(workspace.config_path, "privacy", "cloud_phi", False):
            errors.append("clinical PHI workspaces must keep privacy.cloud_phi = false")
        if not config_bool(workspace.config_path, "privacy", "require_operation_context", True):
            errors.append("clinical PHI workspaces must require operation context")
        if not config_bool(workspace.config_path, "privacy", "local_encrypted_storage_attested", False):
            warnings.append("clinical PHI workspace has no local encrypted-storage attestation")
    wiki = lint_base / "wiki"
    if not wiki.exists():
        if base is not None and (lint_base / "docs").exists():
            return LintResult(errors=[], warnings=[])
        return LintResult(errors=[f"Missing wiki directory: {wiki}"], warnings=[])
    for path in wiki.glob("*.html"):
        errors.extend(validate_html_file(path))
    errors.extend(_link_errors(lint_base))
    errors.extend(_manifest_errors(workspace, lint_base))
    try:
        for schema_path in workspace.path("schemas").glob("*.schema.json"):
            json.loads(schema_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        errors.append(f"Invalid schema JSON: {exc}")
    accepted_claim_ids = {
        str(record.get("claim_id"))
        for record in read_jsonl(workspace.path("manifests/claims.jsonl"))
        if record.get("review_status") == "accepted"
    }
    draft_claim_ids = {
        str(record.get("claim_id"))
        for record in read_jsonl(lint_base / "manifests" / "claims.jsonl")
    }
    if base is not None and accepted_claim_ids and not accepted_claim_ids.issubset(
        accepted_claim_ids | draft_claim_ids
    ):
        warnings.append("draft may remove accepted claims")
    return LintResult(errors=errors, warnings=warnings)


def render_lint_errors(errors: Iterable[str]) -> str:
    return "\n".join(f"ERROR: {error}" for error in errors)
