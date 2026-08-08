"""RSS and Atom ingestion.

``feedparser`` handles both formats and, more importantly, handles the malformed
feeds that make up a depressing share of the real web. Fetching goes through the
:class:`HttpClient` port rather than letting feedparser do its own networking,
so retries, timeouts and the user-agent stay under our control and the fetcher
stays testable against saved fixtures.
"""

from __future__ import annotations

import json
from typing import Any, Final

import feedparser

from engine.adapters.plugins.context import PluginContext
from engine.adapters.sources.parsing import coerce_text, parse_datetime, truncate
from engine.core.domain.enums import SourceKind
from engine.core.domain.source import Source, SourceDocument, compute_content_hash
from engine.core.ports.clock import Clock
from engine.core.ports.http import HttpClient
from engine.observability.logging import get_logger

logger = get_logger(__name__)

_MAX_TITLE: Final = 1024
_MAX_AUTHOR: Final = 255
_MAX_URL: Final = 2048
_MAX_EXTERNAL_ID: Final = 255


class RssFetcher:
    """Fetches feed entries as :class:`SourceDocument` records.

    Implements :class:`engine.core.ports.sources.SourceFetcher` for
    ``SourceKind.RSS``.
    """

    def __init__(self, *, http: HttpClient, clock: Clock) -> None:
        self._http = http
        self._clock = clock

    @property
    def kind(self) -> SourceKind:
        return SourceKind.RSS

    def fetch(self, source: Source, *, limit: int = 50) -> list[SourceDocument]:
        max_items = min(int(source.config.get("max_items", limit)), limit)
        logger.info("rss.fetch_started", source=source.name, url=source.identifier)

        response = self._http.get(source.identifier)
        feed = feedparser.parse(response.text)

        # `bozo` means feedparser hit a parse problem. It usually still recovers
        # entries, so this is a warning rather than a failure -- refusing to
        # ingest a slightly non-compliant feed would eliminate much of the web.
        if getattr(feed, "bozo", 0):
            logger.warning(
                "rss.feed_malformed",
                source=source.name,
                error=str(getattr(feed, "bozo_exception", "unknown")),
            )

        documents: list[SourceDocument] = []
        for entry in feed.entries[:max_items]:
            document = self._to_document(source, entry)
            if document is not None:
                documents.append(document)

        logger.info("rss.fetch_finished", source=source.name, count=len(documents))
        return documents

    def _to_document(self, source: Source, entry: Any) -> SourceDocument | None:
        title = coerce_text(entry.get("title"))
        link = coerce_text(entry.get("link"))

        # An entry with neither a title nor a link cannot become a story lead.
        if not title and not link:
            logger.warning("rss.entry_skipped", source=source.name, reason="no title or link")
            return None

        body = _extract_body(entry)
        external_id = coerce_text(entry.get("id")) or link or title
        published_at = _resolve_published_at(entry)

        return SourceDocument(
            workspace_id=source.workspace_id,
            source_id=source.id,
            external_id=truncate(external_id, _MAX_EXTERNAL_ID),
            content_hash=compute_content_hash(external_id or "", title or "", body),
            title=truncate(title, _MAX_TITLE),
            author=truncate(coerce_text(entry.get("author")), _MAX_AUTHOR),
            url=truncate(link, _MAX_URL),
            content_type="text/html",
            raw_content=body,
            published_at=published_at,
            fetched_at=self._clock.now(),
            meta={
                "tags": [tag.get("term") for tag in entry.get("tags", []) if tag.get("term")],
                "feed_title": coerce_text(getattr(entry, "source", {}).get("title"))
                if isinstance(getattr(entry, "source", None), dict)
                else None,
            },
        )


def _extract_body(entry: Any) -> str:
    """Pull the richest available text from a feed entry.

    Feeds disagree about where the body lives: ``content`` (Atom), ``summary``
    (RSS), or ``description``. We take the longest, since a truncated summary
    alongside full content is common and the full version is what we want.
    """
    candidates: list[str] = []

    for block in entry.get("content", []) or []:
        if isinstance(block, dict) and (value := coerce_text(block.get("value"))):
            candidates.append(value)

    for key in ("summary", "description"):
        if value := coerce_text(entry.get(key)):
            candidates.append(value)

    if not candidates:
        # Preserve the raw entry rather than storing nothing: Phase 2 may find
        # something useful in a field we did not anticipate.
        return json.dumps(dict(entry), default=str, ensure_ascii=False)

    return max(candidates, key=len)


def _resolve_published_at(entry: Any) -> Any:
    """Prefer the published date, fall back to updated, then to parsed structs."""
    for key in ("published", "updated", "created"):
        if parsed := parse_datetime(entry.get(key)):
            return parsed

    # feedparser also exposes pre-parsed time.struct_time values, which are more
    # reliable than the raw strings on feeds with unusual date formats.
    for key in ("published_parsed", "updated_parsed"):
        struct = entry.get(key)
        if struct is not None:
            import calendar

            return parse_datetime(calendar.timegm(struct))
    return None


def build_fetcher(context: PluginContext) -> RssFetcher:
    """Entry-point factory (group ``engine.fetchers``, name ``rss``)."""
    return RssFetcher(http=context.http, clock=context.clock)
