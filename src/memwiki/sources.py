from __future__ import annotations

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
from memwiki.workspace import Workspace

TEXT_EXTENSIONS = {".txt"}
MARKDOWN_EXTENSIONS = {".md", ".markdown"}
HTML_EXTENSIONS = {".html", ".htm"}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".tiff", ".tif"}


def detect_kind(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in TEXT_EXTENSIONS:
        return "text"
    if suffix in MARKDOWN_EXTENSIONS:
        return "markdown"
    if suffix in HTML_EXTENSIONS:
        return "html"
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
    elif kind == "pdf":
        text, pdf_metadata, status = _extract_pdf(path)
        metadata.update(pdf_metadata)
    elif kind == "image":
        text, image_metadata = _extract_image(path)
        metadata.update(image_metadata)
    else:
        raise ValueError(f"Unsupported source kind: {kind}")
    return text, metadata, status


def register_source(workspace: Workspace, path: Path, alias: Optional[str] = None) -> Dict[str, Any]:
    workspace.require()
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

    alias_part = slugify(alias) if alias else slugify(source_path.stem)
    source_id = stable_id("src", digest, alias_part)
    raw_name = f"{source_id}{source_path.suffix.lower()}"
    raw_path = workspace.path(f"raw/{raw_name}")
    if not raw_path.exists():
        shutil.copy2(source_path, raw_path)

    text, metadata, status = extract_source(source_path, kind)
    extracted_dir = workspace.path(f".memwiki/extracted/{source_id}")
    extracted_dir.mkdir(parents=True, exist_ok=True)
    (extracted_dir / "text.txt").write_text(text, encoding="utf-8")

    record: Dict[str, Any] = {
        "source_id": source_id,
        "sha256": digest,
        "kind": kind,
        "raw_path": f"raw/{raw_name}",
        "origin": str(source_path),
        "ingested_at": utc_now(),
        "extraction_status": status,
        "metadata": metadata,
    }
    append_jsonl(sources_path, record)
    append_jsonl(
        workspace.path("manifests/events.jsonl"),
        {
            "event_id": stable_id("evt", source_id, "ingest", utc_now()),
            "event_type": "ingest",
            "created_at": utc_now(),
            "details": {"source_id": source_id},
        },
    )
    return record


def preview_source(workspace: Workspace, path: Path, alias: Optional[str] = None) -> Dict[str, Any]:
    workspace.require()
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
    alias_part = slugify(alias) if alias else slugify(source_path.stem)
    source_id = stable_id("src", digest, alias_part)
    text, metadata, status = extract_source(source_path, kind)
    metadata = dict(metadata)
    metadata["extracted_characters"] = len(text)
    return {
        "source_id": source_id,
        "sha256": digest,
        "kind": kind,
        "raw_path": None,
        "origin": str(source_path),
        "ingested_at": None,
        "extraction_status": status,
        "metadata": metadata,
    }
