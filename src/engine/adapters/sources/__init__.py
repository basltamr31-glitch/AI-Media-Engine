"""Source adapters: fetchers (transport) and parsers (format).

Empty of concrete implementations in Phase 0 by design -- only the registries
that Phase 1 will populate.
"""

from __future__ import annotations

from engine.adapters.sources.registry import (
    FetcherRegistry,
    ParserNotFoundError,
    ParserRegistry,
)

__all__ = ["FetcherRegistry", "ParserNotFoundError", "ParserRegistry"]
