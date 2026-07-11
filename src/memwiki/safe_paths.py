from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Iterable

PROTECTED_WORKSPACE_ROOTS = {"raw", "wiki", "drafts", "manifests", "schemas", "docs", ".memwiki"}


def resolve_confined_output(
    output_root: Path | str,
    requested: Path | str,
    *,
    suffix: str,
    protected_roots: Iterable[str] = PROTECTED_WORKSPACE_ROOTS,
) -> Path:
    root = Path(output_root).resolve()
    raw_requested = Path(requested)
    candidate = raw_requested if raw_requested.is_absolute() else root / raw_requested
    try:
        relative = candidate.absolute().relative_to(root)
    except ValueError:
        raise ValueError("Output path must remain inside the workspace") from None
    if not relative.parts or any(part in {"", ".", ".."} for part in relative.parts):
        raise ValueError("Output path must be a non-empty confined path")
    if relative.parts[0] in set(protected_roots):
        raise ValueError(f"Output path targets protected workspace storage: {relative.parts[0]}")
    if candidate.suffix.lower() != suffix.lower():
        raise ValueError(f"Output path must use the {suffix} extension")
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError("Output path must not contain symlinks")
    resolved_parent = candidate.parent.resolve()
    try:
        resolved_parent.relative_to(root)
    except ValueError:
        raise ValueError("Output path must remain inside the workspace") from None
    if candidate.exists() and not candidate.is_file():
        raise ValueError("Output path must be a regular file")
    return candidate


def write_confined_text(output_root: Path | str, requested: Path | str, text: str, *, suffix: str = ".html") -> Path:
    output = resolve_confined_output(output_root, requested, suffix=suffix)
    output.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{output.name}.", dir=output.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        if output.is_symlink():
            raise ValueError("Output path must not contain symlinks")
        os.replace(temporary, output)
    finally:
        if temporary.exists():
            temporary.unlink()
    return output


def resolve_confined_directory(output_root: Path | str, requested: Path | str) -> Path:
    root = Path(output_root).resolve()
    candidate = Path(requested)
    candidate = candidate if candidate.is_absolute() else root / candidate
    try:
        relative = candidate.absolute().relative_to(root)
    except ValueError:
        raise ValueError("Output path must remain inside the workspace") from None
    if not relative.parts or any(part in {"", ".", ".."} for part in relative.parts):
        raise ValueError("Output directory must be a non-empty confined path")
    if relative.parts[0] in PROTECTED_WORKSPACE_ROOTS:
        raise ValueError(f"Output path targets protected workspace storage: {relative.parts[0]}")
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError("Output path must not contain symlinks")
    if candidate.exists() or candidate.is_symlink():
        raise ValueError("Static export output directory must not already exist")
    return candidate
