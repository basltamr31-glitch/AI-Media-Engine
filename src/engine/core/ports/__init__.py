"""Ports: the interfaces the domain requires from the outside world.

Every port is a :class:`typing.Protocol`, so adapters conform structurally --
no inheritance, no import from adapter back to domain, and test fakes are
ordinary classes rather than mock frameworks.

Ports are added when the phase that needs them arrives. Present today:
clock, persistence, ingestion, LLM. Deferred: channel export (Phase 5),
metrics collection (Phase 5), primary-source enrichment (Phase 2).
"""

from __future__ import annotations

from engine.core.ports.clock import Clock
from engine.core.ports.llm import LLMPort, LLMRequest, LLMResult
from engine.core.ports.repositories import (
    RunRepository,
    SourceDocumentRepository,
    SourceRepository,
    UnitOfWork,
    WorkspaceRepository,
)
from engine.core.ports.sources import DocumentParser, SourceFetcher

__all__ = [
    "Clock",
    "DocumentParser",
    "LLMPort",
    "LLMRequest",
    "LLMResult",
    "RunRepository",
    "SourceDocumentRepository",
    "SourceFetcher",
    "SourceRepository",
    "UnitOfWork",
    "WorkspaceRepository",
]
