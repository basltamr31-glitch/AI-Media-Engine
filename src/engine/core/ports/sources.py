"""Ingestion ports -- the seam that prevents single-source lock-in.

Two distinct responsibilities, deliberately separated:

* :class:`SourceFetcher` knows a **transport** (email, RSS, HTTP API) and
  produces raw :class:`SourceDocument` records.
* :class:`DocumentParser` knows a **format** (a TLDR issue, an arXiv entry) and
  turns one document into story candidates.

Because they are separate, adding a newsletter means writing one parser and
inserting one config row. The pipeline never learns the word "TLDR", and losing
any single source degrades quality instead of stopping production.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol, runtime_checkable

from engine.core.domain.enums import SourceKind
from engine.core.domain.source import Source, SourceDocument
from engine.core.domain.story import StoryCandidate


@runtime_checkable
class SourceFetcher(Protocol):
    """Retrieves raw documents for one transport."""

    @property
    def kind(self) -> SourceKind:
        """The transport this fetcher handles."""
        ...

    def fetch(self, source: Source, *, limit: int = 50) -> list[SourceDocument]:
        """Fetch new documents for ``source``.

        Implementations must be safe to call repeatedly: deduplication happens
        downstream via content hash, but fetchers should avoid re-downloading
        what they can cheaply detect as already seen.
        """
        ...


@runtime_checkable
class RawDocumentArchive(Protocol):
    """Durable storage for verbatim fetched payloads.

    The database keeps what we ingested; the archive keeps exactly what arrived.
    Three reasons this is worth the disk:

    * **Replay.** Parsing can be re-run against real historical payloads when a
      parser is fixed, without re-fetching (often impossible for email).
    * **Fixtures.** Phase 2 builds parsers against real newsletters rather than
      imagined ones.
    * **Provenance.** If a published claim is ever challenged, the original
      bytes are still on disk.
    """

    def store(self, document: SourceDocument, *, workspace_slug: str) -> str:
        """Persist ``document`` verbatim and return a locator for it."""
        ...


@runtime_checkable
class DocumentParser(Protocol):
    """Extracts story candidates from one document format."""

    @property
    def parser_key(self) -> str:
        """Registry key referenced by ``Source.parser_key``."""
        ...

    def can_parse(self, document: SourceDocument) -> bool:
        """Cheap check before committing to a full parse."""
        ...

    def parse(
        self,
        document: SourceDocument,
        *,
        config: Mapping[str, Any] | None = None,
    ) -> list[StoryCandidate]:
        """Extract candidates, excluding advertising and sponsored sections.

        Parsers extract *leads* -- headline, gist, and crucially the outbound
        link. They must not be relied on for publishable prose; that comes from
        the primary source during enrichment.

        Args:
            document: The document to parse.
            config: The owning :class:`Source`'s ``config`` block. Passed rather
                than baked into the parser so that two sources sharing a format
                can differ -- which is what keeps "adding a newsletter is a
                config row" true for parsing as well as for fetching.
        """
        ...
