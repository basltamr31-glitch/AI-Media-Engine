"""Test doubles for the ingestion adapters.

Every fetcher depends on a narrow port rather than on ``httpx`` or
``googleapiclient`` directly, which is what makes these fakes possible. The
whole suite therefore runs offline, in milliseconds, with no Google account and
no credentials -- the only kind of test that keeps getting run.
"""

from __future__ import annotations

import base64
import json
from collections.abc import Mapping
from datetime import datetime
from typing import Any, cast

from pydantic import BaseModel

from engine.core.domain.enums import SourceKind
from engine.core.domain.ledger import TokenUsage
from engine.core.domain.source import Source, SourceDocument
from engine.core.domain.story import EnrichedSource, StoryCandidate
from engine.core.ports.enrichment import ExtractionError
from engine.core.ports.http import HttpError, HttpResponse
from engine.core.ports.llm import LLMError, LLMRequest, LLMResult, SchemaT


class FakeHttpClient:
    """An :class:`HttpClient` serving canned responses, keyed by URL."""

    def __init__(
        self,
        responses: dict[str, str] | None = None,
        *,
        error: Exception | None = None,
        status_code: int = 200,
        content_type: str = "application/json",
    ) -> None:
        self._responses = responses or {}
        self._error = error
        self._status_code = status_code
        self._content_type = content_type
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
            headers={"content-type": self._content_type},
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


class FakeParser:
    """A :class:`DocumentParser` producing a fixed number of candidates.

    Records the config it was handed, so the "source config reaches the parser"
    contract is testable without a real newsletter.
    """

    def __init__(
        self,
        parser_key: str,
        *,
        per_document: int = 1,
        accepts: bool = True,
        fail_on_title: str | None = None,
    ) -> None:
        self._parser_key = parser_key
        self._per_document = per_document
        self._accepts = accepts
        self._fail_on_title = fail_on_title
        self.calls = 0
        self.configs: list[dict[str, Any] | None] = []

    @property
    def parser_key(self) -> str:
        return self._parser_key

    def can_parse(self, document: SourceDocument) -> bool:
        return self._accepts

    def parse(
        self,
        document: SourceDocument,
        *,
        config: Mapping[str, Any] | None = None,
    ) -> list[StoryCandidate]:
        self.calls += 1
        self.configs.append(dict(config) if config is not None else None)
        if self._fail_on_title and self._fail_on_title in (document.title or ""):
            msg = "template changed"
            raise ValueError(msg)
        return [
            StoryCandidate(
                workspace_id=document.workspace_id,
                document_id=document.id,
                source_id=document.source_id,
                headline=f"{document.title} story {index}",
                summary="Summary.",
                primary_url=f"https://example.test/{document.id}/{index}",
                ordinal=index,
            )
            for index in range(self._per_document)
        ]


class FakeExtractor:
    """A :class:`ContentExtractor` returning fixed text, or failing.

    Lets the enrich stage be tested without trafilatura's judgement in the
    loop: what is under test there is what the stage does with a success or a
    failure, not which one the extractor should have produced.
    """

    def __init__(
        self, *, text: str = "The announcement in full.", fails_with: str | None = None
    ) -> None:
        self._text = text
        self._fails_with = fails_with
        self.calls: list[str] = []

    def extract(self, html: str, *, url: str) -> EnrichedSource:
        self.calls.append(url)
        if self._fails_with:
            raise ExtractionError(url, self._fails_with)
        return EnrichedSource(
            url=url,
            title="An extracted title",
            text=self._text,
            extraction_method="fake",
        )


class FakeLLM:
    """An :class:`LLMPort` returning a fixed value, or failing.

    Exists for testing what *callers* do with a generation, not whether a model
    can produce one -- that question is settled by running a real model, and a
    fake that answered it would only be measuring itself.
    """

    def __init__(
        self,
        *,
        value: BaseModel | None = None,
        fails_with: str | None = None,
        fails_code: str = "llm_failed",
        usage: TokenUsage | None = None,
        cost_usd: float = 0.0,
    ) -> None:
        self._value = value
        self._fails_with = fails_with
        self._fails_code = fails_code
        self._usage = usage or TokenUsage(input_tokens=100, output_tokens=20)
        self._cost_usd = cost_usd
        self.requests: list[LLMRequest] = []

    def generate_structured(self, request: LLMRequest, schema: type[SchemaT]) -> LLMResult[SchemaT]:
        self.requests.append(request)
        if self._fails_with:
            raise LLMError(self._fails_with, code=self._fails_code, model=request.model)
        # A minimal instance when none was supplied, so callers that only care
        # about the plumbing need not hand-construct one per schema.
        value = schema.model_construct() if self._value is None else cast(SchemaT, self._value)
        return LLMResult[SchemaT](
            value=value,
            model=request.model,
            usage=self._usage,
            cost_usd=self._cost_usd,
            stop_reason="end_turn",
        )

    def estimate_cost_usd(self, usage: TokenUsage, model: str) -> float:
        return self._cost_usd


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
