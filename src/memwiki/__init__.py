"""Compatibility package for Agentic Wiki's historical memwiki API."""

from memwiki.api import (
    AgentContextResult,
    AgentHandoffDigestRenderResult,
    AgenticWikiWorkspace,
    AgentIncidentLogRenderResult,
    AgentMemoryImpactResult,
    AgentMemoryObservationResult,
    AgentMemoryProposalResult,
    AgentRunStateDraftResult,
    AgentRunStateRenderResult,
    DraftResult,
    ExportResult,
    IngestResult,
    InitResult,
    MemwikiWorkspace,
    PromoteResult,
)
from memwiki.coordinator_api import CoordinatorAPI, CoordinatorRuntime, RuntimeTick
from memwiki.policy import AuthorityVerifier, OperationContext, VerifiedAuthority

__all__ = [
    "AgenticWikiWorkspace",
    "CoordinatorAPI",
    "CoordinatorRuntime",
    "AuthorityVerifier",
    "AgentHandoffDigestRenderResult",
    "AgentContextResult",
    "AgentIncidentLogRenderResult",
    "AgentMemoryImpactResult",
    "AgentMemoryObservationResult",
    "AgentMemoryProposalResult",
    "AgentRunStateDraftResult",
    "AgentRunStateRenderResult",
    "DraftResult",
    "ExportResult",
    "IngestResult",
    "InitResult",
    "MemwikiWorkspace",
    "OperationContext",
    "PromoteResult",
    "RuntimeTick",
    "VerifiedAuthority",
    "__version__",
]

__version__ = "0.1.0"
