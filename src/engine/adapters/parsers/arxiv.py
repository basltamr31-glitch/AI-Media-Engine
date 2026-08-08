"""arXiv feed parser.

One entry, one paper, so this parser produces at most one candidate per
document -- the opposite shape from a newsletter, and a useful check that the
parse stage carries no assumption that documents contain many stories.

The fetcher has already mapped title, authors and link. What is left is the
part that needs arXiv-specific knowledge: the announcement header the feed
prepends to every abstract, and the difference between a genuinely new paper
and the re-announcement of one that was published months ago.

Primary source by definition: an arXiv abstract page is the authors' own words,
which is exactly what the editorial policy asks for (ADR 0003).
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any, Final

from engine.adapters.parsers.markup import canonical_url, clean_text
from engine.adapters.plugins.context import PluginContext
from engine.core.domain.source import SourceDocument
from engine.core.domain.story import StoryCandidate
from engine.core.ports.clock import Clock
from engine.observability.logging import get_logger

logger = get_logger(__name__)

PARSER_KEY: Final = "arxiv_rss"
PARSER_VERSION: Final = 2

#: Every arXiv feed entry opens with ``arXiv:2608.02604v1 Announce Type: new``.
_HEADER: Final = re.compile(
    r"^\s*arXiv:\s*(?P<identifier>[^\s]+?)\s+Announce\s+Type:\s*(?P<announce>[a-z-]+)\s*",
    re.IGNORECASE,
)
_ABSTRACT: Final = re.compile(r"Abstract:\s*(?P<abstract>.+)\Z", re.IGNORECASE | re.DOTALL)
_VERSION: Final = re.compile(r"^(?P<base>.+?)v(?P<version>\d+)$")

#: ``new`` is a first announcement and ``cross`` is a first announcement in this
#: category. ``replace`` is a revision of a paper announced earlier, which is
#: not news -- publishing it would mean covering the same paper twice.
#: Overridable per source, because a workspace tracking *revisions* is a
#: legitimate thing to want.
DEFAULT_ANNOUNCE_TYPES: Final = ("new", "cross", "cross-list")

_MAX_HEADLINE: Final = 1024
_MAX_EXCERPT: Final = 4000
#: Author lists on large collaborations run to hundreds of names.
_MAX_AUTHORS: Final = 512


class ArxivFeedParser:
    """Extracts one story candidate from one arXiv feed entry."""

    def __init__(self, *, clock: Clock) -> None:
        self._clock = clock

    @property
    def parser_key(self) -> str:
        return PARSER_KEY

    def can_parse(self, document: SourceDocument) -> bool:
        return bool(document.title and document.title.strip())

    def parse(
        self,
        document: SourceDocument,
        *,
        config: Mapping[str, Any] | None = None,
    ) -> list[StoryCandidate]:
        """Extract the paper, unless it is a re-announcement.

        Args:
            document: One feed entry.
            config: The owning source's ``config`` block. Recognises
                ``announce_types``.
        """
        headline = clean_text(document.title)
        if not headline:
            return []

        header = _HEADER.search(document.raw_content)
        announce = (header.group("announce").casefold() if header else "new").strip()
        allowed = _announce_types_from(config)
        if announce not in allowed:
            logger.info(
                "arxiv.skipped_reannouncement",
                document_id=document.id,
                announce_type=announce,
            )
            return []

        raw_identifier = header.group("identifier") if header else ""
        identifier, version = _split_version(raw_identifier)
        abstract = _abstract_of(document.raw_content)

        meta: dict[str, Any] = {
            "parser": PARSER_KEY,
            "parser_version": PARSER_VERSION,
            # One entry, one story: there is no ordering to read, and
            # scoring must not mistake "only item" for "lead item".
            "document_items": 1,
            "announce_type": announce,
            "authors": clean_text(document.author)[:_MAX_AUTHORS] or None,
        }
        if identifier:
            meta["arxiv_id"] = identifier
        if version is not None:
            meta["arxiv_version"] = version
        categories = document.meta.get("tags")
        if isinstance(categories, list) and categories:
            meta["categories"] = [str(value) for value in categories]
            meta["primary_category"] = str(categories[0])

        return [
            StoryCandidate(
                workspace_id=document.workspace_id,
                document_id=document.id,
                source_id=document.source_id,
                headline=headline[:_MAX_HEADLINE],
                summary=abstract or None,
                primary_url=canonical_url(document.url or "") or None,
                raw_excerpt=document.raw_content[:_MAX_EXCERPT] or None,
                ordinal=0,
                extracted_at=self._clock.now(),
                meta=meta,
            )
        ]


def _abstract_of(raw_content: str) -> str:
    """The abstract, with the announcement header removed.

    Falls back to the whole body: an entry whose header we do not recognise
    still carries text worth keeping, and a missing summary is worse than an
    untidy one.
    """
    matched = _ABSTRACT.search(raw_content)
    if matched:
        return clean_text(matched.group("abstract"))
    return clean_text(_HEADER.sub("", raw_content))


def _split_version(identifier: str) -> tuple[str, int | None]:
    """``2608.02604v1`` becomes ``("2608.02604", 1)``."""
    cleaned = identifier.strip()
    if not cleaned:
        return "", None
    matched = _VERSION.match(cleaned)
    if not matched:
        return cleaned, None
    return matched.group("base"), int(matched.group("version"))


def _announce_types_from(config: Mapping[str, Any] | None) -> frozenset[str]:
    if not config:
        return frozenset(DEFAULT_ANNOUNCE_TYPES)
    configured = config.get("announce_types")
    if not isinstance(configured, list) or not configured:
        return frozenset(DEFAULT_ANNOUNCE_TYPES)
    return frozenset(str(value).casefold() for value in configured)


def build_parser(context: PluginContext) -> ArxivFeedParser:
    """Entry-point factory (group ``engine.parsers``, name ``arxiv_rss``)."""
    return ArxivFeedParser(clock=context.clock)
