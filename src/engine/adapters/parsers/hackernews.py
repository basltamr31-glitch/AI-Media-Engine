"""Hacker News parser.

One API item, one story. The fetcher has already mapped title, URL and author
out of the JSON; what needs Hacker-News-specific knowledge is everything else:
which items are recruitment adverts rather than stories, what to do about a
discussion that links nowhere, and the attention numbers that make HN worth
reading at all.

Points and comment counts are carried into ``meta`` rather than dropped. They
are the raw material for a corroboration or prominence signal later, and they
cannot be recovered afterwards -- the API returns *current* counts, so a number
observed today is not the number it will return next week.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any, Final

from engine.adapters.parsers.markup import canonical_url, clean_text, parse_html
from engine.adapters.plugins.context import PluginContext
from engine.core.domain.source import SourceDocument
from engine.core.domain.story import StoryCandidate
from engine.core.ports.clock import Clock
from engine.observability.logging import get_logger

logger = get_logger(__name__)

PARSER_KEY: Final = "hackernews_api"
PARSER_VERSION: Final = 2

_DISCUSSION_URL: Final = "https://news.ycombinator.com/item?id={item_id}"

#: ``job`` items are paid recruitment placements -- Hacker News's equivalent of
#: a sponsor block. Overridable per source, in the same spirit as the
#: newsletter parser's section list.
DEFAULT_DROP_TAGS: Final = ("job",)

_MAX_HEADLINE: Final = 1024
_MAX_EXCERPT: Final = 4000
_MAX_SUMMARY: Final = 4000


class HackerNewsParser:
    """Extracts one story candidate from one Hacker News item."""

    def __init__(self, *, clock: Clock) -> None:
        self._clock = clock

    @property
    def parser_key(self) -> str:
        return PARSER_KEY

    def can_parse(self, document: SourceDocument) -> bool:
        return document.raw_content.strip().startswith("{")

    def parse(
        self,
        document: SourceDocument,
        *,
        config: Mapping[str, Any] | None = None,
    ) -> list[StoryCandidate]:
        """Extract the story, unless it is a recruitment placement.

        Args:
            document: One API item.
            config: The owning source's ``config`` block. Recognises
                ``drop_tags``.

        Raises:
            ValueError: If the payload is not a JSON object. Malformed input is
                the stage's problem to isolate, not something to swallow here.
        """
        item = _load(document)
        tags = {str(tag).casefold() for tag in item.get("_tags", []) if isinstance(tag, str)}
        dropped = _drop_tags_from(config)
        if tags & dropped:
            logger.info(
                "hackernews.skipped_tag", document_id=document.id, tags=sorted(tags & dropped)
            )
            return []

        headline = clean_text(document.title or str(item.get("title") or ""))
        if not headline:
            return []

        item_id = str(item.get("objectID") or document.external_id or "").strip()
        discussion_url = _DISCUSSION_URL.format(item_id=item_id) if item_id else None
        # A text post ("Ask HN", "Show HN" with no link) points at its own
        # discussion. Leaving the URL empty would make the story unenrichable
        # for no reason -- the thread *is* the primary source in that case.
        target = canonical_url(document.url or str(item.get("url") or "")) or discussion_url

        meta: dict[str, Any] = {
            "parser": PARSER_KEY,
            "parser_version": PARSER_VERSION,
            # One entry, one story: there is no ordering to read, and
            # scoring must not mistake "only item" for "lead item".
            "document_items": 1,
        }
        if item_id:
            meta["hackernews_id"] = item_id
        if discussion_url:
            meta["discussion_url"] = discussion_url
        for field, key in (("points", "points"), ("num_comments", "comments")):
            value = item.get(field)
            if isinstance(value, int):
                meta[key] = value
        if tags:
            meta["tags"] = sorted(tags)
        if target == discussion_url:
            meta["self_post"] = True

        return [
            StoryCandidate(
                workspace_id=document.workspace_id,
                document_id=document.id,
                source_id=document.source_id,
                headline=headline[:_MAX_HEADLINE],
                summary=_summary_of(item),
                primary_url=target,
                raw_excerpt=document.raw_content[:_MAX_EXCERPT] or None,
                ordinal=0,
                extracted_at=self._clock.now(),
                meta=meta,
            )
        ]


def _load(document: SourceDocument) -> dict[str, Any]:
    payload = json.loads(document.raw_content)
    if not isinstance(payload, dict):
        msg = f"expected a JSON object, got {type(payload).__name__}"
        raise ValueError(msg)
    return payload


def _summary_of(item: Mapping[str, Any]) -> str | None:
    """The submitter's own text, if there is any. Usually there is not.

    ``story_text`` arrives as HTML, and the stray markup would otherwise end up
    quoted verbatim in a generated post.
    """
    text = item.get("story_text")
    if not isinstance(text, str) or not text.strip():
        return None
    return clean_text(parse_html(text).get_text(" "))[:_MAX_SUMMARY] or None


def _drop_tags_from(config: Mapping[str, Any] | None) -> frozenset[str]:
    if not config:
        return frozenset(DEFAULT_DROP_TAGS)
    configured = config.get("drop_tags")
    if not isinstance(configured, list) or not configured:
        return frozenset(DEFAULT_DROP_TAGS)
    return frozenset(str(value).casefold() for value in configured)


def build_parser(context: PluginContext) -> HackerNewsParser:
    """Entry-point factory (group ``engine.parsers``, name ``hackernews_api``)."""
    return HackerNewsParser(clock=context.clock)
