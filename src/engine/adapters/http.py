"""httpx-backed implementation of the HTTP port.

The only implementation of the HTTP port, and the only place fetchers get their
transport. Retry policy, timeouts and the user-agent live here, so transport
behaviour is changed in one place rather than negotiated separately by every
fetcher.

One other module reaches for ``httpx`` directly:
:mod:`engine.adapters.llm.ollama`. That is deliberate rather than a leak -- a
local inference call wants a ten-minute timeout and no retries, which is the
opposite of what fetching a web page wants, and forcing both through one
policy would make each wrong for the other.
"""

from __future__ import annotations

import time
from types import TracebackType
from typing import Any, Final

import httpx

from engine.core.ports.http import HttpError, HttpResponse
from engine.observability.logging import get_logger

logger = get_logger(__name__)

#: Statuses worth retrying: transient server trouble and rate limiting.
#: A 404 or 401 will not improve on a second attempt, so they fail immediately.
_RETRYABLE_STATUSES: Final = frozenset({429, 500, 502, 503, 504})


class HttpxClient:
    """Synchronous HTTP client with bounded retries and exponential backoff.

    Synchronous by choice. Ingestion runs a handful of requests on a schedule;
    async would add concurrency complexity to code whose bottleneck is an LLM
    call in a later stage, not network I/O here.
    """

    def __init__(
        self,
        *,
        timeout_seconds: float = 30.0,
        user_agent: str = "ai-media-engine/0.1",
        max_attempts: int = 3,
        backoff_seconds: float = 1.0,
        client: httpx.Client | None = None,
    ) -> None:
        self._max_attempts = max(1, max_attempts)
        self._backoff_seconds = backoff_seconds
        self._owns_client = client is None
        self._client = client or httpx.Client(
            timeout=timeout_seconds,
            follow_redirects=True,
            headers={"User-Agent": user_agent},
        )

    def get(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> HttpResponse:
        last_error: str = "no attempt was made"

        for attempt in range(1, self._max_attempts + 1):
            try:
                response = self._client.get(url, params=params, headers=headers)
            except httpx.HTTPError as exc:
                last_error = f"transport error: {exc}"
                logger.warning("http.attempt_failed", url=url, attempt=attempt, error=last_error)
                self._sleep_before_retry(attempt)
                continue

            if response.status_code in _RETRYABLE_STATUSES:
                last_error = f"retryable status {response.status_code}"
                logger.warning(
                    "http.retryable_status",
                    url=url,
                    attempt=attempt,
                    status_code=response.status_code,
                )
                self._sleep_before_retry(attempt)
                continue

            if response.status_code >= 400:
                # Non-retryable client error: fail fast rather than burn attempts.
                raise HttpError(
                    url,
                    f"request failed with status {response.status_code}",
                    status_code=response.status_code,
                )

            return HttpResponse(
                url=str(response.url),
                status_code=response.status_code,
                text=response.text,
                headers={key.lower(): value for key, value in response.headers.items()},
            )

        raise HttpError(url, f"exhausted {self._max_attempts} attempts: {last_error}")

    def _sleep_before_retry(self, attempt: int) -> None:
        """Exponential backoff, skipped after the final attempt."""
        if attempt >= self._max_attempts:
            return
        time.sleep(self._backoff_seconds * (2 ** (attempt - 1)))

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> HttpxClient:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()
