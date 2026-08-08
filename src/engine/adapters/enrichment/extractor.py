"""Article extraction backed by trafilatura.

Chosen over a hand-written readability heuristic for the same reason
BeautifulSoup was chosen for parsing: separating an article from its navigation,
newsletter signup, cookie banner and comment thread is a well-studied problem
with a maintained solution, and a home-grown version of it is code that quietly
degrades as the web changes.

Three quality gates sit on top of it, because "extracted something" and
"extracted the article" are not the same claim:

* **Too short.** A paywall stub, a consent interstitial or a JavaScript shell
  all extract cleanly and produce a paragraph. Writing a post from that is how
  a system invents facts.
* **Not an article.** A PDF or an image served at an HTML-looking URL.
* **No text at all.** Extraction succeeded structurally and found nothing.

Each is reported as a distinct reason so "why is this story not enriched?" is
answerable without re-fetching.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Final

import trafilatura
from trafilatura.settings import use_config

from engine.core.domain.story import EnrichedSource
from engine.core.ports.clock import Clock
from engine.core.ports.enrichment import ExtractionError
from engine.observability.logging import get_logger

logger = get_logger(__name__)

EXTRACTION_METHOD: Final = "trafilatura"

#: Below this many characters, what came back is a stub rather than an article.
#: Tuned against real pages: consent walls and paywall teasers land under 400,
#: while the shortest genuine release notes in the corpus clear it.
DEFAULT_MIN_TEXT_LENGTH: Final = 400

#: Generation never needs an entire long-read, and unbounded text turns into
#: unbounded prompt cost in Phase 3.
DEFAULT_MAX_TEXT_LENGTH: Final = 40_000

_MAX_TITLE: Final = 1024
_MAX_AUTHOR: Final = 255


def _quiet_config() -> Any:
    """Trafilatura's own settings, with its signal handling disabled.

    Its default configuration installs a SIGALRM-based timeout, which is not
    available on Windows and is the wrong layer besides -- request timeouts
    belong to the HTTP client, which already has them.
    """
    config = use_config()
    config.set("DEFAULT", "EXTRACTION_TIMEOUT", "0")
    return config


class TrafilaturaExtractor:
    """Extracts the main article text and metadata from an HTML page."""

    def __init__(
        self,
        *,
        clock: Clock,
        min_text_length: int = DEFAULT_MIN_TEXT_LENGTH,
        max_text_length: int = DEFAULT_MAX_TEXT_LENGTH,
    ) -> None:
        self._clock = clock
        self._min_text_length = min_text_length
        self._max_text_length = max_text_length
        self._config = _quiet_config()

    def extract(self, html: str, *, url: str) -> EnrichedSource:
        if not html.strip():
            raise ExtractionError(url, "empty document", code="empty_document")

        text = trafilatura.extract(
            html,
            url=url,
            favor_precision=True,
            include_comments=False,
            include_tables=True,
            config=self._config,
        )
        if not text or not text.strip():
            raise ExtractionError(url, "no article text found", code="no_article_text")

        cleaned = text.strip()
        if len(cleaned) < self._min_text_length:
            msg = f"only {len(cleaned)} characters extracted (paywall or script-rendered page?)"
            raise ExtractionError(url, msg, code="text_too_short")

        title, author, published_at = self._metadata(html, url=url)
        return EnrichedSource(
            url=url,
            title=title,
            author=author,
            published_at=published_at,
            text=cleaned[: self._max_text_length],
            fetched_at=self._clock.now(),
            extraction_method=EXTRACTION_METHOD,
        )

    def _metadata(self, html: str, *, url: str) -> tuple[str | None, str | None, datetime | None]:
        """Best-effort title, author and publication date.

        Missing metadata is normal and never fatal: the text is what the story
        needs, and a byline we could not find is not a reason to discard it.
        """
        try:
            # Annotated as always returning a document; on malformed input it
            # returns None. Typed loosely here so the guard below is honest
            # rather than dead code the checker has to be argued with.
            document: Any = trafilatura.extract_metadata(html, default_url=url)
        except Exception as exc:  # third-party parser over hostile input
            logger.debug("enrich.metadata_failed", url=url, error=str(exc))
            return None, None, None
        if document is None:
            return None, None, None

        title = (document.title or "").strip()[:_MAX_TITLE] or None
        author = (document.author or "").strip()[:_MAX_AUTHOR] or None
        return title, author, _parse_date(document.date)


def _parse_date(value: str | None) -> datetime | None:
    """Trafilatura returns dates as ``YYYY-MM-DD`` strings, or nothing.

    Assumed UTC rather than local: a naive datetime in a scheduled pipeline is
    a correctness bug waiting for a timezone change.
    """
    if not value:
        return None
    try:
        return datetime.fromisoformat(value).replace(tzinfo=UTC)
    except ValueError:
        return None
