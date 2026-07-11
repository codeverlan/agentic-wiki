from __future__ import annotations

import json
import mimetypes
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from bs4 import BeautifulSoup
from PIL import Image
from pypdf import PdfReader

from memwiki.ids import sha256_bytes, slugify, stable_id, utc_now
from memwiki.manifest import append_jsonl, read_jsonl
from memwiki.policy import (
    OperationContext,
    append_event,
    client_record_id,
    is_clinical_phi,
    require_operation_context,
    sensitivity,
)
from memwiki.workspace import Workspace


def _integrity_entry_digest(entry: Dict[str, Any]) -> str:
    payload = {key: value for key, value in entry.items() if key != "entry_sha256"}
    return sha256_bytes(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8"))


def _append_source_integrity_entry(workspace: Workspace, record: Dict[str, Any]) -> None:
    ledger_path = workspace.path(".memwiki/source-integrity.jsonl")
    existing = read_jsonl(ledger_path)
    entry: Dict[str, Any] = {
        "source_id": record["source_id"],
        "raw_path": record["raw_path"],
        "raw_sha256": record["sha256"],
        "extracted_sha256": record["extracted_sha256"],
        "source_category": record["source_category"],
        "previous_entry_sha256": existing[-1]["entry_sha256"] if existing else None,
    }
    entry["entry_sha256"] = _integrity_entry_digest(entry)
    append_jsonl(ledger_path, entry)

TEXT_EXTENSIONS = {".txt"}
MARKDOWN_EXTENSIONS = {".md", ".markdown"}
HTML_EXTENSIONS = {".html", ".htm"}
JSON_EXTENSIONS = {".json"}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".tiff", ".tif"}


def detect_kind(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in TEXT_EXTENSIONS:
        return "text"
    if suffix in MARKDOWN_EXTENSIONS:
        return "markdown"
    if suffix in HTML_EXTENSIONS:
        return "html"
    if suffix in JSON_EXTENSIONS:
        return "json"
    if suffix == ".pdf":
        return "pdf"
    if suffix in IMAGE_EXTENSIONS:
        return "image"
    raise ValueError(f"Unsupported source type: {path.suffix}")


def read_text_fallback(path: Path) -> str:
    data = path.read_bytes()
    for encoding in ["utf-8", "utf-16", "latin-1"]:
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _extract_html(path: Path) -> str:
    soup = BeautifulSoup(read_text_fallback(path), "html.parser")
    return soup.get_text("\n", strip=True)


def _json_metadata(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return {"json_top_level_type": "object", "json_keys": sorted(value.keys())}
    if isinstance(value, list):
        return {"json_top_level_type": "array", "json_items": len(value)}
    if value is None:
        return {"json_top_level_type": "null"}
    return {"json_top_level_type": type(value).__name__}


def _extract_json(path: Path) -> Tuple[str, Dict[str, Any]]:
    value = json.loads(read_text_fallback(path))
    return json.dumps(value, indent=2, sort_keys=True), _json_metadata(value)


def _extract_pdf(path: Path) -> Tuple[str, Dict[str, Any], str]:
    try:
        reader = PdfReader(str(path))
        page_text = []
        for index, page in enumerate(reader.pages, start=1):
            text = page.extract_text() or ""
            page_text.append(f"[page {index}]\n{text}".strip())
        return "\n\n".join(page_text), {"pages": len(reader.pages)}, "extracted"
    except Exception as exc:  # pypdf raises several parser-specific exceptions.
        return "", {"pages": 0, "warning": str(exc)}, "partial"


def _extract_image(path: Path) -> Tuple[str, Dict[str, Any]]:
    with Image.open(path) as image:
        metadata: Dict[str, Any] = {
            "width": image.width,
            "height": image.height,
            "mode": image.mode,
            "format": image.format,
        }
    ocr_text = ""
    if shutil.which("tesseract"):
        try:
            output = subprocess.run(
                ["tesseract", str(path), "stdout"],
                check=False,
                capture_output=True,
                text=True,
            )
            if output.returncode == 0:
                ocr_text = output.stdout.strip()
                metadata["ocr"] = "available"
            else:
                metadata["ocr"] = "failed"
        except OSError:
            metadata["ocr"] = "unavailable"
    else:
        metadata["ocr"] = "unavailable"
    description = f"Image {path.name}: {metadata['width']}x{metadata['height']} pixels."
    if ocr_text:
        description = f"{description}\n\nOCR text:\n{ocr_text}"
    return description, metadata


def extract_source(path: Path, kind: str) -> Tuple[str, Dict[str, Any], str]:
    metadata: Dict[str, Any] = {"mime_type": mimetypes.guess_type(path.name)[0]}
    status = "extracted"
    if kind in {"text", "markdown"}:
        text = read_text_fallback(path)
    elif kind == "html":
        text = _extract_html(path)
    elif kind == "json":
        text, json_metadata = _extract_json(path)
        metadata.update(json_metadata)
    elif kind == "pdf":
        text, pdf_metadata, status = _extract_pdf(path)
        metadata.update(pdf_metadata)
    elif kind == "image":
        text, image_metadata = _extract_image(path)
        metadata.update(image_metadata)
    else:
        raise ValueError(f"Unsupported source kind: {kind}")
    return text, metadata, status


def register_source(
    workspace: Workspace,
    path: Path,
    alias: Optional[str] = None,
    source_category: Optional[str] = None,
    context: Optional[OperationContext] = None,
) -> Dict[str, Any]:
    workspace.require()
    require_operation_context(workspace.config_path, "ingest", context)
    source_path = path.resolve()
    if not source_path.exists():
        raise ValueError(f"Source does not exist: {source_path}")
    kind = detect_kind(source_path)
    data = source_path.read_bytes()
    digest = sha256_bytes(data)
    sources_path = workspace.path("manifests/sources.jsonl")
    existing = read_jsonl(sources_path)
    duplicate = next((record for record in existing if record.get("sha256") == digest), None)
    if duplicate and not alias:
        raise ValueError(f"Duplicate source hash already registered as {duplicate['source_id']}")

    clinical = is_clinical_phi(workspace.config_path)
    alias_part = "clinical-source" if clinical else slugify(alias) if alias else slugify(source_path.stem)
    source_id = stable_id("src", digest, alias_part)
    raw_name = f"{source_id}{source_path.suffix.lower()}"
    raw_path = workspace.path(f"raw/{raw_name}")
    if not raw_path.exists():
        raw_path.write_bytes(data)

    text, metadata, status = extract_source(raw_path, kind)
    extracted_dir = workspace.path(f".memwiki/extracted/{source_id}")
    extracted_dir.mkdir(parents=True, exist_ok=True)
    (extracted_dir / "text.txt").write_text(text, encoding="utf-8")

    workspace_sensitivity = sensitivity(workspace.config_path)
    workspace_client_record_id = client_record_id(workspace.config_path)
    record: Dict[str, Any] = {
        "source_id": source_id,
        "sha256": digest,
        "extracted_sha256": sha256_bytes(text.encode("utf-8")),
        "kind": kind,
        "raw_path": f"raw/{raw_name}",
        "origin": "redacted-local-origin" if clinical else str(source_path),
        "ingested_at": utc_now(),
        "extraction_status": status,
        "metadata": metadata,
        "sensitivity": workspace_sensitivity,
        "source_category": source_category or ("clinical_note" if clinical else "source"),
    }
    if workspace_client_record_id:
        record["client_record_id"] = workspace_client_record_id
    if clinical:
        record["metadata"] = dict(record["metadata"])
        record["metadata"]["origin_redacted"] = True
        record["metadata"]["origin_filename_sha256"] = sha256_bytes(source_path.name.encode("utf-8"))
    _append_source_integrity_entry(workspace, record)
    append_jsonl(sources_path, record)
    append_event(
        workspace.root,
        "ingest",
        {
            "source_id": source_id,
            "kind": kind,
            "sensitivity": workspace_sensitivity,
            "client_record_id": workspace_client_record_id,
        },
        context,
    )
    return record


def verify_source_integrity(workspace: Workspace, source_id: str) -> Dict[str, Any]:
    source = next(
        (
            record
            for record in read_jsonl(workspace.path("manifests/sources.jsonl"))
            if record.get("source_id") == source_id
        ),
        None,
    )
    if source is None:
        raise ValueError(f"Unknown source: {source_id}")
    ledger_entries = read_jsonl(workspace.path(".memwiki/source-integrity.jsonl"))
    previous_digest: Optional[str] = None
    trusted_entry = None
    for entry in ledger_entries:
        chain_matches = entry.get("previous_entry_sha256") == previous_digest
        digest_matches = entry.get("entry_sha256") == _integrity_entry_digest(entry)
        if not chain_matches or not digest_matches:
            raise ValueError("Source integrity ledger verification failed")
        previous_digest = str(entry["entry_sha256"])
        if entry.get("source_id") == source_id:
            trusted_entry = entry
    if trusted_entry is not None:
        comparisons = {
            "raw_path": "raw_path",
            "sha256": "raw_sha256",
            "extracted_sha256": "extracted_sha256",
            "source_category": "source_category",
        }
        if any(source.get(field) != trusted_entry.get(ledger_field) for field, ledger_field in comparisons.items()):
            raise ValueError(f"Source manifest does not match integrity ledger: {source_id}")
    raw_root = workspace.path("raw").resolve()
    raw_path = workspace.path(str(source.get("raw_path", ""))).resolve()
    try:
        raw_path.relative_to(raw_root)
    except ValueError:
        raise ValueError("Raw source path is outside the workspace raw directory") from None
    if not raw_path.is_file() or sha256_bytes(raw_path.read_bytes()) != source.get("sha256"):
        raise ValueError(f"Raw source integrity check failed: {source_id}")
    extracted_root = workspace.path(".memwiki/extracted").resolve()
    extracted_path = workspace.path(f".memwiki/extracted/{source_id}/text.txt").resolve()
    try:
        extracted_path.relative_to(extracted_root)
    except ValueError:
        raise ValueError("Extracted source path is outside the workspace extraction directory") from None
    if not extracted_path.is_file():
        raise ValueError(f"Extracted source integrity check failed: {source_id}")
    observed_extracted = sha256_bytes(extracted_path.read_bytes())
    expected_extracted = source.get("extracted_sha256")
    if not isinstance(expected_extracted, str):
        expected_extracted = observed_extracted
    if observed_extracted != expected_extracted:
        raise ValueError(f"Extracted source integrity check failed: {source_id}")
    return source


def preview_source(
    workspace: Workspace,
    path: Path,
    alias: Optional[str] = None,
    source_category: Optional[str] = None,
    context: Optional[OperationContext] = None,
) -> Dict[str, Any]:
    workspace.require()
    require_operation_context(workspace.config_path, "ingest_preview", context)
    source_path = path.resolve()
    if not source_path.exists():
        raise ValueError(f"Source does not exist: {source_path}")
    kind = detect_kind(source_path)
    data = source_path.read_bytes()
    digest = sha256_bytes(data)
    existing = read_jsonl(workspace.path("manifests/sources.jsonl"))
    duplicate = next((record for record in existing if record.get("sha256") == digest), None)
    if duplicate and not alias:
        raise ValueError(f"Duplicate source hash already registered as {duplicate['source_id']}")
    clinical = is_clinical_phi(workspace.config_path)
    alias_part = "clinical-source" if clinical else slugify(alias) if alias else slugify(source_path.stem)
    source_id = stable_id("src", digest, alias_part)
    text, metadata, status = extract_source(source_path, kind)
    metadata = dict(metadata)
    metadata["extracted_characters"] = len(text)
    return {
        "source_id": source_id,
        "sha256": digest,
        "kind": kind,
        "raw_path": None,
        "origin": "redacted-local-origin" if clinical else str(source_path),
        "ingested_at": None,
        "extraction_status": status,
        "metadata": metadata,
        "sensitivity": sensitivity(workspace.config_path),
        "client_record_id": client_record_id(workspace.config_path),
        "source_category": source_category or ("clinical_note" if clinical else "source"),
    }
