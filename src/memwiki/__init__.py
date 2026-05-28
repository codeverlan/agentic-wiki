"""HTML-first local memory wiki."""

from memwiki.api import (
    DraftResult,
    ExportResult,
    IngestResult,
    InitResult,
    MemwikiWorkspace,
    PromoteResult,
)

__all__ = [
    "DraftResult",
    "ExportResult",
    "IngestResult",
    "InitResult",
    "MemwikiWorkspace",
    "PromoteResult",
    "__version__",
]

__version__ = "0.1.0"
