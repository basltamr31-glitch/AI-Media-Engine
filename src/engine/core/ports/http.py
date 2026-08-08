"""HTTP transport port.

Fetchers depend on this narrow interface, never on ``httpx`` directly. Two
consequences, both of which matter more than the abstraction costs:

* **Every fetcher is unit-testable without a network.** Tests inject a client
  backed by saved fixtures, so the suite stays fast, deterministic and runnable
  offline or in CI with no egress.
* **Cross-cutting transport policy has one home.** Retries, rate limiting,
  caching and per-tenant proxying are added in the adapter without touching a
  single fetcher.

The interface is deliberately smaller than ``httpx``'s. A port shaped like its
first implementation is not a port.
"""

from __future__ import annotations

import json
from typing import Any, Protocol, runtime_checkable

from pydantic import Field

from engine.core.domain.base import ImmutableModel


class HttpError(RuntimeError):
    """Raised when a request fails or returns a non-success status.

    Deliberately a single exception type: fetchers should treat "the network is
    unhappy" uniformly, and the ingest stage isolates the failure per source
    rather than distinguishing between causes.
    """

    def __init__(self, url: str, message: str, *, status_code: int | None = None) -> None:
        self.url = url
        self.status_code = status_code
        super().__init__(f"{message} (url={url}, status={status_code})")


class RobotsDisallowedError(HttpError):
    """Raised when a site's ``robots.txt`` refuses the request.

    A subclass rather than a separate exception, so callers that treat all
    transport problems alike keep working unchanged. It exists at all because
    this failure is categorically different from the others: retrying will not
    help, the fix is not ours to make, and a run that skipped ten pages because
    it was asked to must be able to say so rather than reporting ten timeouts.
    """

    def __init__(self, url: str, message: str = "refused by robots.txt") -> None:
        super().__init__(url, message)


class HttpResponse(ImmutableModel):
    """A completed HTTP response, decoded far enough to be useful."""

    url: str
    status_code: int
    text: str
    headers: dict[str, str] = Field(default_factory=dict)

    def json_body(self) -> Any:
        """Parse the body as JSON.

        Named ``json_body`` rather than ``json`` because ``BaseModel.json()``
        already exists and means something entirely different (serialise *this
        object*). Shadowing it would compile and then mislead every reader.

        Raises:
            HttpError: If the body is not valid JSON. Converting the parse
                failure here means callers handle exactly one error type.
        """
        try:
            return json.loads(self.text)
        except json.JSONDecodeError as exc:
            msg = f"response body is not valid JSON: {exc}"
            raise HttpError(self.url, msg, status_code=self.status_code) from exc


@runtime_checkable
class HttpClient(Protocol):
    """Minimal synchronous HTTP client."""

    def get(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> HttpResponse:
        """Perform a GET request.

        Implementations must raise :class:`HttpError` for transport failures and
        non-success statuses, so callers never have to inspect status codes.
        """
        ...
