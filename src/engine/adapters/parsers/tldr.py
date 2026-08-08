"""TLDR newsletter parser.

TLDR sends eight editions (AI, Dev, Design, Marketing, DevOps, Data, Product,
and the flagship) from one address, all sharing a layout: an emoji, an ``<h1>``
section heading, then a run of blocks each holding one link and one summary
paragraph. Every link's text ends with a parenthesised marker -- ``(5 minute
read)``, ``(GitHub Repo)``, ``(Sponsor)``.

That marker does most of the editorial work. It separates stories from
sponsorships, and it separates both from the referral, jobs and unsubscribe
links in the footer, which carry no marker at all. Matching on it rather than
on position means a template change that moves the footer does not start
producing job adverts as news.

Two things this parser deliberately does **not** do:

* **It does not keep TLDR's tracking URLs.** Every link is wrapped as
  ``tracking.tldrnewsletter.com/CL0/<encoded url>/1/<per-send token>``. The
  token identifies the individual send, and therefore the subscriber. Only the
  encoded destination is kept; the token is discarded and never stored, and
  enrichment later fetches the destination directly rather than registering a
  click on the reader's behalf.
* **It does not treat the summary as publishable text.** The excerpt exists for
  traceability. What we publish comes from the primary source (ADR 0003).
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import datetime
from typing import Any, Final
from urllib.parse import unquote, urlsplit

from bs4 import Tag

from engine.adapters.parsers.markup import (
    canonical_url,
    clean_text,
    element_text,
    href_of,
    parse_html,
)
from engine.adapters.plugins.context import PluginContext
from engine.core.domain.source import SourceDocument
from engine.core.domain.story import StoryCandidate
from engine.core.ports.clock import Clock
from engine.observability.logging import get_logger

logger = get_logger(__name__)

PARSER_KEY: Final = "tldr_newsletter"

#: Bumped whenever extraction changes in a way that would produce different
#: candidates from the same document. Recorded on every candidate so a
#: re-parsed backlog can be told apart from an old one.
PARSER_VERSION: Final = 2

#: ``Headline (marker)``. Non-greedy so a headline containing its own
#: parentheses keeps them and only the trailing marker is taken.
_MARKED_LINK: Final = re.compile(r"^(?P<headline>.+?)\s*\((?P<marker>[^()]{1,40})\)$", re.DOTALL)

#: ``5 minute read``, ``20 minute podcast``, ``47 minute video``.
_DURATION_MARKER: Final = re.compile(r"^(?P<minutes>\d{1,3})\s+minute\s+(?P<medium>[a-z]+)$", re.I)

#: Markers naming a resource rather than a reading time. Anything not matched
#: here or by the duration pattern is not a story: ``(Webinar)``, ``(FREE)``
#: and ``(TLDR Curator, ~3 hrs/week)`` are promotions and job adverts.
_RESOURCE_MARKERS: Final = frozenset(
    {
        "website",
        "github repo",
        "hugging face repo",
        "gitlab repo",
        "tool",
        "app",
        "demo",
        "product launch",
        "video",
        "podcast",
        "paper",
        "dataset",
    }
)

#: Markers that identify paid placement outright.
_SPONSOR_MARKERS: Final = frozenset({"sponsor", "sponsored", "advertisement", "ad"})

#: Section headings dropped wholesale. Overridable per source via
#: ``config.strip_sections`` so a newsletter with different section names needs
#: a config row, not a code change.
DEFAULT_STRIP_SECTIONS: Final = ("sponsor", "advertisement", "together with", "presented by")

_TRACKING_HOSTS: Final = frozenset({"tracking.tldrnewsletter.com"})
_TRACKING_PREFIX: Final = "/CL0/"
#: The segment that follows the encoded destination in a tracking URL.
_TOKEN_SEPARATOR: Final = "/1/"  # noqa: S105 -- a URL path segment, not a secret

_TITLE_TAG: Final = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)

_MAX_HEADLINE: Final = 1024
_MAX_EXCERPT: Final = 4000


class TldrNewsletterParser:
    """Extracts story candidates from a TLDR newsletter issue."""

    def __init__(self, *, clock: Clock) -> None:
        self._clock = clock

    @property
    def parser_key(self) -> str:
        return PARSER_KEY

    def can_parse(self, document: SourceDocument) -> bool:
        """Cheap structural check before committing to a full parse."""
        if not document.raw_content.strip():
            return False
        return "html" in document.content_type.casefold() or "<" in document.raw_content[:200]

    def parse(
        self,
        document: SourceDocument,
        *,
        config: Mapping[str, Any] | None = None,
    ) -> list[StoryCandidate]:
        """Extract every story mentioned in one issue.

        Args:
            document: The fetched newsletter.
            config: The owning source's ``config`` block. Recognises
                ``strip_sections``.
        """
        strip_sections = _strip_sections_from(config)
        soup = parse_html(document.raw_content)
        edition = _edition_of(document)
        extracted_at = self._clock.now()

        candidates: list[StoryCandidate] = []
        seen: set[tuple[str, str]] = set()
        section = ""
        dropped_sponsored = 0

        # Headings and links in document order, so each link is attributed to
        # the heading it actually appeared under.
        for element in soup.find_all(["h1", "h2", "a"]):
            if element.name in ("h1", "h2"):
                section = element_text(element)
                continue

            extracted = self._extract(element)
            if extracted is None:
                continue
            headline, marker, url = extracted

            if marker.casefold() in _SPONSOR_MARKERS or _is_stripped(section, strip_sections):
                dropped_sponsored += 1
                continue

            fingerprint = (headline.casefold(), url)
            if fingerprint in seen:
                continue
            seen.add(fingerprint)

            candidates.append(
                self._build(
                    document=document,
                    headline=headline,
                    marker=marker,
                    url=url,
                    element=element,
                    section=section,
                    edition=edition,
                    ordinal=len(candidates),
                    extracted_at=extracted_at,
                )
            )

        # Position only means something relative to the rest of the issue, so
        # the count travels with each candidate. Written after the fact because
        # it is not known until the whole document has been read.
        for candidate in candidates:
            candidate.meta["document_items"] = len(candidates)

        logger.info(
            "tldr.parsed",
            document_id=document.id,
            edition=edition,
            candidates=len(candidates),
            sponsored_dropped=dropped_sponsored,
        )
        return candidates

    def _extract(self, anchor: Tag) -> tuple[str, str, str] | None:
        """Headline, marker and destination URL, or None if not a story link."""
        text = element_text(anchor)
        matched = _MARKED_LINK.match(text)
        if matched is None:
            return None

        marker = clean_text(matched.group("marker"))
        if not _is_story_marker(marker):
            return None

        headline = clean_text(matched.group("headline"))
        if not headline:
            return None

        return headline[:_MAX_HEADLINE], marker, untrack(href_of(anchor))

    def _build(
        self,
        *,
        document: SourceDocument,
        headline: str,
        marker: str,
        url: str,
        element: Tag,
        section: str,
        edition: str,
        ordinal: int,
        extracted_at: datetime,
    ) -> StoryCandidate:
        block = element.find_parent("div", class_="text-block") or element.parent
        block_text = element_text(block)
        # The block holds the link text followed by the summary; removing the
        # former leaves the latter without depending on the tag nesting, which
        # differs between editions.
        summary = clean_text(block_text.replace(element_text(element), "", 1))

        meta: dict[str, Any] = {
            "parser": PARSER_KEY,
            "parser_version": PARSER_VERSION,
            "marker": marker,
            "section": section,
            "edition": edition,
        }
        duration = _DURATION_MARKER.match(marker)
        if duration:
            meta["read_minutes"] = int(duration.group("minutes"))
            meta["medium"] = duration.group("medium").casefold()
        if not url:
            # Kept as a flag rather than dropped: a story we cannot follow is
            # still a story someone else may corroborate with a usable link.
            meta["unresolved_link"] = True

        return StoryCandidate(
            workspace_id=document.workspace_id,
            document_id=document.id,
            source_id=document.source_id,
            headline=headline,
            summary=summary or None,
            primary_url=url or None,
            raw_excerpt=(block_text or None) and block_text[:_MAX_EXCERPT],
            ordinal=ordinal,
            extracted_at=extracted_at,
            meta=meta,
        )


def untrack(url: str) -> str:
    """Recover the real destination from a TLDR tracking link.

    ``https://tracking.tldrnewsletter.com/CL0/<encoded>/1/<per-send token>``
    becomes ``<decoded>``. The token is dropped rather than followed: it
    identifies the send and therefore the subscriber.

    A tracking URL we cannot decode returns the empty string. Storing it would
    put a subscriber identifier in the database and, worse, hand it to a third
    party the first time enrichment followed the link.
    """
    if not url:
        return ""
    parts = urlsplit(url)
    if parts.netloc not in _TRACKING_HOSTS:
        return canonical_url(url)
    if not parts.path.startswith(_TRACKING_PREFIX):
        return ""

    encoded = parts.path[len(_TRACKING_PREFIX) :].split(_TOKEN_SEPARATOR, 1)[0]
    destination = unquote(encoded)
    if not destination.lower().startswith(("http://", "https://")):
        return ""
    return canonical_url(destination)


def _is_story_marker(marker: str) -> bool:
    """Sponsors count as story markers here; they are dropped one step later.

    Keeping them recognisable is what allows the parser to *count* what it
    removed instead of quietly ignoring it.
    """
    folded = marker.casefold()
    return (
        folded in _SPONSOR_MARKERS
        or folded in _RESOURCE_MARKERS
        or _DURATION_MARKER.match(marker) is not None
    )


def _is_stripped(section: str, strip_sections: tuple[str, ...]) -> bool:
    folded = section.casefold()
    return any(needle in folded for needle in strip_sections)


def _strip_sections_from(config: Mapping[str, Any] | None) -> tuple[str, ...]:
    if not config:
        return DEFAULT_STRIP_SECTIONS
    configured = config.get("strip_sections")
    if not isinstance(configured, list) or not configured:
        return DEFAULT_STRIP_SECTIONS
    return tuple(str(value).casefold() for value in configured)


def _edition_of(document: SourceDocument) -> str:
    """Which TLDR newsletter this issue is.

    One configured source receives all eight editions, so the edition is a
    property of the document rather than of the source. Read from the raw
    markup because ``<title>`` is stripped before parsing.
    """
    matched = _TITLE_TAG.search(document.raw_content)
    return clean_text(matched.group(1)) if matched else ""


def build_parser(context: PluginContext) -> TldrNewsletterParser:
    """Entry-point factory (group ``engine.parsers``, name ``tldr_newsletter``)."""
    return TldrNewsletterParser(clock=context.clock)
