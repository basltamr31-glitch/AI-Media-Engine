"""Gmail ingestion.

Structure worth noting: :class:`GmailFetcher` depends on the narrow
:class:`GmailApiClient` protocol, not on ``googleapiclient``. That one seam is
what lets every Gmail behaviour -- query construction, pagination, MIME
decoding, header extraction -- be tested against a fake, with no Google account,
no network and no credentials. Tests that require a live inbox are tests that
eventually stop being run.
"""

from __future__ import annotations

import base64
import binascii
from collections.abc import Callable
from datetime import datetime
from typing import Any, Final, Protocol, runtime_checkable

from engine.adapters.plugins.context import PluginContext
from engine.adapters.sources.email.oauth import credential_store_from
from engine.adapters.sources.parsing import parse_datetime, truncate
from engine.core.domain.enums import SourceKind
from engine.core.domain.source import Source, SourceDocument, compute_content_hash
from engine.core.ports.clock import Clock
from engine.observability.logging import get_logger

logger = get_logger(__name__)

_MAX_TITLE: Final = 1024
_MAX_AUTHOR: Final = 255
_MAX_EXTERNAL_ID: Final = 255

#: HTML first: newsletters carry their outbound links in the HTML part, and
#: those links are the entire point -- they are the leads we follow to primary
#: sources in Phase 2.
_PREFERRED_MIME_TYPES: Final = ("text/html", "text/plain")


class GmailFetchError(RuntimeError):
    """Raised when Gmail cannot be reached or returns an unusable response."""


@runtime_checkable
class GmailApiClient(Protocol):
    """The two Gmail operations this fetcher needs, and nothing else."""

    def list_message_ids(self, *, query: str, max_results: int) -> list[str]:
        """Return message IDs matching ``query``, newest first."""
        ...

    def get_message(self, message_id: str) -> dict[str, Any]:
        """Return one message in Gmail's ``full`` format."""
        ...


class GoogleApiGmailClient:
    """Real Gmail client backed by ``googleapiclient``."""

    def __init__(self, credentials: Any, *, user_id: str = "me") -> None:
        from googleapiclient.discovery import build

        # cache_discovery=False silences a noisy warning and avoids writing a
        # discovery cache into the user's home directory.
        self._service = build("gmail", "v1", credentials=credentials, cache_discovery=False)
        self._user_id = user_id

    def list_message_ids(self, *, query: str, max_results: int) -> list[str]:
        from googleapiclient.errors import HttpError as GoogleHttpError

        collected: list[str] = []
        page_token: str | None = None

        try:
            while len(collected) < max_results:
                response = (
                    self._service.users()
                    .messages()
                    .list(
                        userId=self._user_id,
                        q=query,
                        maxResults=min(100, max_results - len(collected)),
                        pageToken=page_token,
                    )
                    .execute()
                )
                collected.extend(item["id"] for item in response.get("messages", []))
                page_token = response.get("nextPageToken")
                if not page_token:
                    break
        except GoogleHttpError as exc:
            msg = f"Gmail list failed: {exc}"
            raise GmailFetchError(msg) from exc

        return collected[:max_results]

    def get_message(self, message_id: str) -> dict[str, Any]:
        from googleapiclient.errors import HttpError as GoogleHttpError

        try:
            result = (
                self._service.users()
                .messages()
                .get(userId=self._user_id, id=message_id, format="full")
                .execute()
            )
        except GoogleHttpError as exc:
            msg = f"Gmail get failed for message {message_id}: {exc}"
            raise GmailFetchError(msg) from exc
        return dict(result)


class GmailFetcher:
    """Fetches newsletter emails as :class:`SourceDocument` records.

    Implements :class:`engine.core.ports.sources.SourceFetcher` for
    ``SourceKind.EMAIL``.
    """

    def __init__(
        self,
        *,
        client_factory: Callable[[], GmailApiClient],
        clock: Clock,
        lookback_days: int = 7,
    ) -> None:
        # A factory, not a client: constructing the container must not require
        # Google credentials. A workspace with only RSS sources has to work on a
        # machine that has never run `engine sources auth`.
        self._client_factory = client_factory
        self._clock = clock
        self._lookback_days = lookback_days

    @property
    def kind(self) -> SourceKind:
        return SourceKind.EMAIL

    def fetch(self, source: Source, *, limit: int = 50) -> list[SourceDocument]:
        query = self.build_query(source)
        logger.info("gmail.fetch_started", source=source.name, query=query, limit=limit)

        client = self._client_factory()
        message_ids = client.list_message_ids(query=query, max_results=limit)
        logger.info("gmail.messages_listed", source=source.name, count=len(message_ids))

        documents: list[SourceDocument] = []
        for message_id in message_ids:
            try:
                payload = client.get_message(message_id)
                documents.append(self._to_document(source, payload))
            except (GmailFetchError, ValueError, KeyError) as exc:
                # One malformed message must not cost us the whole issue. Skip
                # it loudly and continue.
                logger.warning(
                    "gmail.message_skipped",
                    source=source.name,
                    message_id=message_id,
                    error=str(exc),
                )
        return documents

    def build_query(self, source: Source) -> str:
        """Compose a Gmail search query from source configuration.

        Config keys (all optional):

        ``query``
            Extra raw Gmail search terms, appended verbatim.
        ``label``
            Restrict to a Gmail label.
        ``subject``
            Restrict to a subject substring -- useful when one sender publishes
            several newsletters from the same address.
        ``lookback_days``
            Per-source override of the global lookback window.
        """
        config = source.config
        terms = [f"from:({source.identifier})"]

        lookback = int(config.get("lookback_days", self._lookback_days))
        if lookback > 0:
            terms.append(f"newer_than:{lookback}d")

        if label := config.get("label"):
            terms.append(f"label:{label}")
        if subject := config.get("subject"):
            terms.append(f'subject:"{subject}"')
        if extra := config.get("query"):
            terms.append(str(extra))

        return " ".join(terms)

    # --- conversion ------------------------------------------------------

    def _to_document(self, source: Source, payload: dict[str, Any]) -> SourceDocument:
        message_id = str(payload.get("id", ""))
        if not message_id:
            msg = "Gmail message payload has no id"
            raise ValueError(msg)

        headers = _extract_headers(payload.get("payload", {}))
        body, content_type = _extract_body(payload.get("payload", {}))

        subject = headers.get("subject") or "(no subject)"
        published_at = _resolve_published_at(headers, payload)

        return SourceDocument(
            workspace_id=source.workspace_id,
            source_id=source.id,
            external_id=truncate(message_id, _MAX_EXTERNAL_ID),
            # Gmail message IDs are stable per account, so including the ID
            # makes the hash deterministic across re-fetches while the body
            # keeps it sensitive to genuinely different content.
            content_hash=compute_content_hash(message_id, subject, body),
            title=truncate(subject, _MAX_TITLE),
            author=truncate(headers.get("from"), _MAX_AUTHOR),
            url=f"https://mail.google.com/mail/u/0/#inbox/{message_id}",
            content_type=content_type,
            raw_content=body,
            published_at=published_at,
            fetched_at=self._clock.now(),
            meta={
                "thread_id": payload.get("threadId"),
                "label_ids": payload.get("labelIds", []),
                "snippet": payload.get("snippet", ""),
                "list_id": headers.get("list-id"),
                "rfc_message_id": headers.get("message-id"),
                "size_estimate": payload.get("sizeEstimate"),
            },
        )


def _extract_headers(part: dict[str, Any]) -> dict[str, str]:
    """Flatten Gmail's header list into a lowercase-keyed mapping."""
    return {
        str(header.get("name", "")).lower(): str(header.get("value", ""))
        for header in part.get("headers", [])
    }


def _extract_body(part: dict[str, Any]) -> tuple[str, str]:
    """Walk the MIME tree and return ``(body, content_type)``.

    Gmail returns arbitrarily nested multipart structures. We collect every
    text part, then pick by preference rather than taking the first match --
    ``multipart/alternative`` lists plain text before HTML, and we want HTML.
    """
    collected: dict[str, str] = {}
    _walk_parts(part, collected)

    for mime_type in _PREFERRED_MIME_TYPES:
        if content := collected.get(mime_type):
            return content, mime_type

    if collected:
        mime_type, content = next(iter(collected.items()))
        return content, mime_type

    return "", "text/plain"


def _walk_parts(part: dict[str, Any], collected: dict[str, str]) -> None:
    """Depth-first collection of decoded text parts, keyed by MIME type."""
    mime_type = str(part.get("mimeType", ""))
    body = part.get("body", {})

    if data := body.get("data"):
        decoded = _decode_base64url(str(data))
        if decoded and mime_type not in collected:
            collected[mime_type] = decoded

    for child in part.get("parts", []):
        _walk_parts(child, collected)


def _decode_base64url(data: str) -> str:
    """Decode Gmail's base64url payload, tolerating missing padding."""
    try:
        # Gmail strips '=' padding; b64decode requires it. Restore it.
        padded = data + "=" * (-len(data) % 4)
        return base64.urlsafe_b64decode(padded).decode("utf-8", errors="replace")
    except (binascii.Error, ValueError) as exc:
        logger.warning("gmail.decode_failed", error=str(exc))
        return ""


def _resolve_published_at(headers: dict[str, str], payload: dict[str, Any]) -> datetime | None:
    """Prefer the ``Date`` header; fall back to Gmail's internal timestamp."""
    if parsed := parse_datetime(headers.get("date")):
        return parsed
    if internal := payload.get("internalDate"):
        return parse_datetime(int(internal))
    return None


def build_fetcher(context: PluginContext) -> GmailFetcher:
    """Entry-point factory (group ``engine.fetchers``, name ``email``).

    The store is built now but read lazily: constructing the registry must not
    require Google credentials, because a workspace with only RSS sources has
    to work on a machine that has never run ``engine sources auth``.
    """
    store = credential_store_from(context)
    return GmailFetcher(
        client_factory=lambda: GoogleApiGmailClient(store.load()),
        clock=context.clock,
        lookback_days=context.settings.ingest_lookback_days,
    )
