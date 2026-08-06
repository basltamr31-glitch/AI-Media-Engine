"""Test doubles for the ingestion adapters.

Every fetcher depends on a narrow port rather than on ``httpx`` or
``googleapiclient`` directly, which is what makes these fakes possible. The
whole suite therefore runs offline, in milliseconds, with no Google account and
no credentials -- the only kind of test that keeps getting run.
"""

from __future__ import annotations

import base64
import json
from datetime import datetime
from typing import Any

from engine.core.domain.enums import SourceKind
from engine.core.domain.source import Source, SourceDocument
from engine.core.ports.http import HttpError, HttpResponse


class FakeHttpClient:
    """An :class:`HttpClient` serving canned responses, keyed by URL."""

    def __init__(
        self,
        responses: dict[str, str] | None = None,
        *,
        error: Exception | None = None,
        status_code: int = 200,
    ) -> None:
        self._responses = responses or {}
        self._error = error
        self._status_code = status_code
        self.calls: list[tuple[str, dict[str, Any] | None]] = []

    def get(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> HttpResponse:
        self.calls.append((url, params))
        if self._error is not None:
            raise self._error
        if url not in self._responses:
            raise HttpError(url, "no canned response registered", status_code=404)
        return HttpResponse(
            url=url,
            status_code=self._status_code,
            text=self._responses[url],
            headers={"content-type": "application/json"},
        )


class FakeGmailClient:
    """A :class:`GmailApiClient` backed by an in-memory message list."""

    def __init__(self, messages: list[dict[str, Any]] | None = None) -> None:
        self._messages = {message["id"]: message for message in (messages or [])}
        self.queries: list[str] = []

    def list_message_ids(self, *, query: str, max_results: int) -> list[str]:
        self.queries.append(query)
        return list(self._messages)[:max_results]

    def get_message(self, message_id: str) -> dict[str, Any]:
        return self._messages[message_id]


def encode_part(text: str) -> str:
    """Encode text the way Gmail does: base64url with padding stripped."""
    return base64.urlsafe_b64encode(text.encode("utf-8")).decode("ascii").rstrip("=")


def gmail_message(
    *,
    message_id: str = "msg-1",
    subject: str = "TLDR AI 2026-08-04",
    sender: str = "dan@tldrnewsletter.com",
    date: str = "Mon, 04 Aug 2026 09:00:00 +0000",
    html: str | None = "<h1>Today in AI</h1>",
    plain: str | None = "Today in AI",
    thread_id: str = "thread-1",
) -> dict[str, Any]:
    """Build a realistic Gmail ``messages.get`` payload.

    Parts are ordered plain-then-HTML, exactly as ``multipart/alternative``
    does, so tests actually exercise the preference logic rather than a lucky
    first match.
    """
    parts: list[dict[str, Any]] = []
    if plain is not None:
        parts.append({"mimeType": "text/plain", "body": {"data": encode_part(plain)}})
    if html is not None:
        parts.append({"mimeType": "text/html", "body": {"data": encode_part(html)}})

    return {
        "id": message_id,
        "threadId": thread_id,
        "labelIds": ["INBOX", "CATEGORY_UPDATES"],
        "snippet": (plain or "")[:100],
        "sizeEstimate": 4096,
        # Epoch milliseconds for 2026-08-04T09:00:00Z, matching the Date header
        # so the fixture stays internally consistent.
        "internalDate": "1785834000000",
        "payload": {
            "mimeType": "multipart/alternative",
            "headers": [
                {"name": "Subject", "value": subject},
                {"name": "From", "value": sender},
                {"name": "Date", "value": date},
                {"name": "List-Id", "value": "<tldr.tldrnewsletter.com>"},
                {"name": "Message-ID", "value": f"<{message_id}@tldrnewsletter.com>"},
            ],
            "parts": parts,
        },
    }


class FakeFetcher:
    """A :class:`SourceFetcher` returning fixed documents, or raising."""

    def __init__(
        self,
        kind: SourceKind,
        documents: list[SourceDocument] | None = None,
        *,
        error: Exception | None = None,
    ) -> None:
        self._kind = kind
        self._documents = documents or []
        self._error = error
        self.calls = 0

    @property
    def kind(self) -> SourceKind:
        return self._kind

    def fetch(self, source: Source, *, limit: int = 50) -> list[SourceDocument]:
        self.calls += 1
        if self._error is not None:
            raise self._error
        return self._documents[:limit]


class GeneratingFakeFetcher:
    """A fetcher that manufactures documents bound to the source it is given.

    Unlike :class:`FakeFetcher`, which returns a fixed list, this respects the
    workspace and source identity of its argument -- required whenever the
    documents will actually be persisted and hit foreign keys.
    """

    def __init__(
        self,
        kind: SourceKind,
        *,
        count: int,
        clock: Any,
        generation: str = "a",
    ) -> None:
        self._kind = kind
        self._count = count
        self._clock = clock
        # Changing the generation label simulates a feed moving on, producing
        # genuinely new content hashes.
        self._generation = generation

    @property
    def kind(self) -> SourceKind:
        return self._kind

    def fetch(self, source: Source, *, limit: int = 50) -> list[SourceDocument]:
        return [
            make_document(
                workspace_id=source.workspace_id,
                source_id=source.id,
                content_hash=f"{source.id}-{self._generation}-{index}",
                fetched_at=self._clock.now(),
                title=f"{source.name} story {index}",
            )
            for index in range(min(self._count, limit))
        ]


class RecordingArchive:
    """A :class:`RawDocumentArchive` that records calls instead of writing."""

    def __init__(self) -> None:
        self.stored: list[tuple[str, str]] = []

    def store(self, document: SourceDocument, *, workspace_slug: str) -> str:
        self.stored.append((document.id, workspace_slug))
        return f"memory://{workspace_slug}/{document.id}"


def make_document(
    *,
    workspace_id: str,
    source_id: str,
    content_hash: str,
    fetched_at: datetime,
    title: str = "A story",
    raw_content: str = "body",
) -> SourceDocument:
    """Construct a SourceDocument with the boilerplate filled in."""
    return SourceDocument(
        workspace_id=workspace_id,
        source_id=source_id,
        external_id=content_hash[:16],
        content_hash=content_hash,
        title=title,
        url=f"https://example.com/{content_hash[:8]}",
        raw_content=raw_content,
        fetched_at=fetched_at,
    )


def json_fixture(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False)
