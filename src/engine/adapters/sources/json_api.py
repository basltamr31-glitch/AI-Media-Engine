"""Generic, configuration-driven JSON API ingestion.

This is the module that keeps the engine from being locked to any one provider.
It has no knowledge of Hacker News, Reddit or GitHub. It knows how to call a
JSON endpoint, find the array of items inside the response, and map fields out
of each item using dot-paths. Which endpoint, which array and which fields are
all configuration.

Consequence: **adding Reddit, GitHub Releases, Product Hunt or Dev.to is a YAML
entry, not a code change.** Below are three real shapes, all handled by this one
class.

Hacker News (items nested under ``hits``)::

    - kind: api
      name: Hacker News (front page)
      identifier: https://hn.algolia.com/api/v1/search
      config:
        items_path: hits
        request:
          params: {tags: front_page, hitsPerPage: 50}
        field_map:
          external_id: objectID
          title: title
          url: url
          author: author
          published_at: created_at
        filters:
          min: {points: 100}
          require: [title]

Reddit (double nesting -- ``data.children``, then ``data`` per item)::

    - kind: api
      name: r/MachineLearning
      identifier: https://www.reddit.com/r/MachineLearning/top.json
      config:
        items_path: data.children
        request:
          params: {t: day, limit: 50}
        field_map:
          external_id: data.id
          title: data.title
          url: data.url
          author: data.author
          published_at: data.created_utc      # Unix epoch, parsed transparently
          summary: data.selftext
        filters:
          min: {data.score: 50}

GitHub Releases (a bare top-level array)::

    - kind: api
      name: vLLM releases
      identifier: https://api.github.com/repos/vllm-project/vllm/releases
      config:
        items_path: ""                        # the payload *is* the array
        field_map:
          external_id: id
          title: name
          url: html_url
          author: author.login
          published_at: published_at
          summary: body
        filters:
          exclude_true: [draft, prerelease]
"""

from __future__ import annotations

import json
from typing import Any, Final

from engine.adapters.sources.parsing import coerce_text, get_path, parse_datetime, truncate
from engine.core.domain.enums import SourceKind
from engine.core.domain.source import Source, SourceDocument, compute_content_hash
from engine.core.ports.clock import Clock
from engine.core.ports.http import HttpClient, HttpError
from engine.observability.logging import get_logger

logger = get_logger(__name__)

_MAX_TITLE: Final = 1024
_MAX_AUTHOR: Final = 255
_MAX_URL: Final = 2048
_MAX_EXTERNAL_ID: Final = 255

#: Sensible defaults so a minimal config still works against a typical API.
_DEFAULT_FIELD_MAP: Final = {
    "external_id": "id",
    "title": "title",
    "url": "url",
    "author": "author",
    "published_at": "published_at",
    "summary": "summary",
}


class JsonApiFetcher:
    """Fetches items from any JSON endpoint using a declarative field map.

    Implements :class:`engine.core.ports.sources.SourceFetcher` for
    ``SourceKind.API``.
    """

    def __init__(self, *, http: HttpClient, clock: Clock) -> None:
        self._http = http
        self._clock = clock

    @property
    def kind(self) -> SourceKind:
        return SourceKind.API

    def fetch(self, source: Source, *, limit: int = 50) -> list[SourceDocument]:
        config = source.config
        request = config.get("request", {}) or {}
        max_items = min(int(config.get("max_items", limit)), limit)

        logger.info("json_api.fetch_started", source=source.name, url=source.identifier)

        response = self._http.get(
            source.identifier,
            params=request.get("params"),
            headers=request.get("headers"),
        )
        payload = response.json_body()

        items = get_path(payload, str(config.get("items_path", "")), default=None)
        if not isinstance(items, list):
            msg = (
                f"items_path {config.get('items_path', '')!r} did not resolve to a list "
                f"(got {type(items).__name__}). Check the endpoint's response shape."
            )
            raise HttpError(source.identifier, msg, status_code=response.status_code)

        field_map = {**_DEFAULT_FIELD_MAP, **(config.get("field_map") or {})}
        filters = config.get("filters", {}) or {}

        documents: list[SourceDocument] = []
        rejected = 0
        for item in items:
            if len(documents) >= max_items:
                break
            if not _passes_filters(item, filters):
                rejected += 1
                continue
            document = self._to_document(source, item, field_map)
            if document is not None:
                documents.append(document)

        logger.info(
            "json_api.fetch_finished",
            source=source.name,
            kept=len(documents),
            filtered_out=rejected,
        )
        return documents

    def _to_document(
        self,
        source: Source,
        item: Any,
        field_map: dict[str, str],
    ) -> SourceDocument | None:
        def field(name: str) -> str | None:
            path = field_map.get(name)
            return coerce_text(get_path(item, path)) if path else None

        title = field("title")
        url = field("url")
        if not title and not url:
            logger.warning("json_api.item_skipped", source=source.name, reason="no title or url")
            return None

        external_id = field("external_id") or url or title
        summary = field("summary") or ""
        published_at = parse_datetime(get_path(item, field_map.get("published_at", "")))

        # The full item is preserved verbatim as the raw content. Phase 2
        # parsers can then reach fields this map never mentioned, without a
        # re-fetch -- and for APIs with retention windows, a re-fetch may be
        # impossible.
        raw_content = json.dumps(item, ensure_ascii=False, default=str)

        return SourceDocument(
            workspace_id=source.workspace_id,
            source_id=source.id,
            external_id=truncate(external_id, _MAX_EXTERNAL_ID),
            content_hash=compute_content_hash(external_id or "", title or "", summary),
            title=truncate(title, _MAX_TITLE),
            author=truncate(field("author"), _MAX_AUTHOR),
            url=truncate(url, _MAX_URL),
            content_type="application/json",
            raw_content=raw_content,
            published_at=published_at,
            fetched_at=self._clock.now(),
            meta={"summary": summary[:2000]} if summary else {},
        )


def _passes_filters(item: Any, filters: dict[str, Any]) -> bool:
    """Apply declarative filters to one item.

    Supported keys, all optional and all dot-path aware:

    ``min`` / ``max``
        Numeric thresholds, e.g. ``{points: 100}`` or ``{data.score: 50}``.
        An item whose field is missing or non-numeric fails the check --
        filters are a floor, so absent evidence does not clear the bar.
    ``require``
        Field paths that must be present and non-empty.
    ``exclude_true``
        Field paths that must not be truthy, e.g. ``[draft, prerelease]``.
    """
    for path, threshold in (filters.get("min") or {}).items():
        value = _as_number(get_path(item, path))
        if value is None or value < float(threshold):
            return False

    for path, threshold in (filters.get("max") or {}).items():
        value = _as_number(get_path(item, path))
        if value is None or value > float(threshold):
            return False

    for path in filters.get("require") or []:
        if not coerce_text(get_path(item, path)):
            return False

    return all(not get_path(item, path) for path in filters.get("exclude_true") or [])


def _as_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None
