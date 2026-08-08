"""Being a good citizen of other people's servers.

Enrichment fetches pages on a story's behalf. While that was operator-triggered
and measured in dozens, it was indistinguishable from a human following a link
someone had already sent them. Scheduling changes that: the same code now runs
with nobody watching, on a timer, forever. Two things have to be true before
that is acceptable, and neither was:

* **We ask permission.** ``robots.txt`` is how a site says what automated
  clients may fetch, and ignoring it while running unattended is the difference
  between a tool and a nuisance.
* **We space our requests.** One host often serves many stories, and a burst of
  requests from one client is what gets a user agent blocked -- which would
  degrade quality silently, in a way that looks like an extraction bug.

Both are implemented as a **decorator over the** :class:`HttpClient` **port**
rather than as changes to the fetchers. Politeness is transport policy, and the
port docstring already reserved that seam. The consequence worth having: no
stage knows this exists, and a fetcher written next year is polite without its
author doing anything.

The robots implementation follows RFC 9309, including the part most
implementations get wrong -- an *unreachable* robots.txt (5xx, timeout) means
assume disallowed, while a *missing* one (404) means allow everything. Those
are opposite answers to superficially similar failures.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Final
from urllib.parse import urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

from engine.core.ports.http import (
    HttpClient,
    HttpError,
    HttpResponse,
    RobotsDisallowedError,
)
from engine.observability.logging import get_logger

logger = get_logger(__name__)

#: How long a fetched ``robots.txt`` is trusted. RFC 9309 suggests a standard
#: cache lifetime of 24 hours; one host serves many stories, so without caching
#: a run of 25 enrichments could fetch the same file 25 times and the politeness
#: mechanism would itself be the impolite part.
DEFAULT_ROBOTS_TTL_SECONDS: Final = 24 * 60 * 60

#: Minimum gap between two requests to the same host, when the site does not
#: state its own. One second is unremarkable to a server and invisible to a run
#: that fetches a few dozen pages.
DEFAULT_MIN_INTERVAL_SECONDS: Final = 1.0

#: Ceiling on an honoured ``Crawl-delay``. Some sites declare values in the
#: hundreds of seconds, which would stall a run rather than pace it; past this
#: point the correct response is to fetch fewer pages, not to wait longer.
MAX_HONOURED_CRAWL_DELAY_SECONDS: Final = 30.0


def origin_of(url: str) -> str:
    """The scheme and host a robots policy and rate limit apply to.

    Per-origin rather than per-domain: ``http`` and ``https`` on the same host
    are separate origins with separate ``robots.txt`` files, and a site that
    serves different rules on each is unusual but not wrong.
    """
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, "", "", ""))


@dataclass
class RateLimiter:
    """Enforces a minimum gap between requests to the same host.

    Sleeps rather than queues, because the caller is a sequential pipeline
    stage: there is nothing else for this thread to be doing, and a queue would
    add a scheduler to a system whose concurrency is one.
    """

    min_interval_seconds: float = DEFAULT_MIN_INTERVAL_SECONDS
    monotonic: Callable[[], float] = time.monotonic
    sleep: Callable[[float], None] = time.sleep
    _last_request: dict[str, float] = field(default_factory=dict)

    def wait(self, origin: str, *, min_interval: float | None = None) -> float:
        """Block until this origin may be called again.

        Returns:
            The seconds actually waited, so a caller can report the cost of
            politeness rather than have it disappear into wall-clock time.
        """
        interval = self.min_interval_seconds if min_interval is None else min_interval
        now = self.monotonic()
        previous = self._last_request.get(origin)

        waited = 0.0
        if previous is not None:
            remaining = interval - (now - previous)
            if remaining > 0:
                self.sleep(remaining)
                waited = remaining

        self._last_request[origin] = self.monotonic()
        return waited


@dataclass
class _CachedRobots:
    """One host's answer, and when we got it."""

    parser: RobotFileParser | None
    fetched_at: float
    #: Set when there is no usable file and the outcome is uniform: ``True``
    #: after a 404 (nothing is disallowed), ``False`` when the file could not
    #: be reached at all (RFC 9309 says assume everything is).
    blanket: bool | None = None


class RobotsPolicy:
    """Whether a URL may be fetched, according to the site that serves it.

    Caches per origin with a TTL. Not thread-safe, and deliberately not: the
    engine's fetching is sequential by design (see :class:`HttpxClient`), and a
    lock here would be protecting against a concurrency model that does not
    exist.
    """

    def __init__(
        self,
        http: HttpClient,
        *,
        user_agent: str,
        ttl_seconds: float = DEFAULT_ROBOTS_TTL_SECONDS,
        monotonic: Callable[[], float] = time.monotonic,
        limiter: RateLimiter | None = None,
    ) -> None:
        self._http = http
        self._user_agent = user_agent
        self._ttl_seconds = ttl_seconds
        self._monotonic = monotonic
        # Fetching robots.txt is itself a request to that host. Sharing the
        # limiter keeps the permission check inside the pacing rather than
        # beside it -- otherwise a run over 25 stories on 25 hosts issues 50
        # requests while believing it issued 25.
        self._limiter = limiter
        self._cache: dict[str, _CachedRobots] = {}

    def is_allowed(self, url: str) -> bool:
        """Whether ``robots.txt`` permits us to fetch ``url``."""
        cached = self._robots_for(origin_of(url))
        if cached.parser is None:
            # No usable file, so every URL on this origin gets the same answer,
            # decided when the fetch failed. `blanket` is always set in this
            # branch -- the two fields are populated together.
            return bool(cached.blanket)
        return cached.parser.can_fetch(self._user_agent, url)

    def crawl_delay(self, url: str) -> float | None:
        """The site's requested gap between requests, if it states one.

        Read from the same user-agent group that governs permission, so a site
        that addresses us by name gets to set both -- and a site whose named
        group omits ``Crawl-delay`` is not silently given the wildcard's.
        """
        cached = self._robots_for(origin_of(url))
        if cached.parser is None:
            return None
        declared = cached.parser.crawl_delay(self._user_agent)
        if declared is None:
            return None
        return min(float(declared), MAX_HONOURED_CRAWL_DELAY_SECONDS)

    def _robots_for(self, origin: str) -> _CachedRobots:
        cached = self._cache.get(origin)
        if cached is not None and (self._monotonic() - cached.fetched_at) < self._ttl_seconds:
            return cached

        fetched = self._fetch(origin)
        self._cache[origin] = fetched
        return fetched

    def _fetch(self, origin: str) -> _CachedRobots:
        url = f"{origin}/robots.txt"
        if self._limiter is not None:
            self._limiter.wait(origin)
        now = self._monotonic()
        try:
            response = self._http.get(url)
        except HttpError as exc:
            # The distinction RFC 9309 draws, and the one that matters: a site
            # that says "no such file" has no rules, while a site we could not
            # reach has rules we failed to read. Guessing "allowed" in the
            # second case is how a crawler ends up ignoring a disallow during
            # exactly the outage that made the server fragile.
            missing = exc.status_code is not None and 400 <= exc.status_code < 500
            logger.info(
                "robots.unavailable",
                origin=origin,
                status_code=exc.status_code,
                decision="allow" if missing else "disallow",
            )
            return _CachedRobots(parser=None, fetched_at=now, blanket=missing)

        parser = RobotFileParser()
        parser.parse(response.text.splitlines())
        logger.debug("robots.loaded", origin=origin)
        return _CachedRobots(parser=parser, fetched_at=now)


class PoliteHttpClient:
    """An :class:`HttpClient` that asks permission and paces itself.

    Wraps another client rather than replacing it, so retries, backoff and
    timeouts continue to live in one place and this decorator only adds what it
    is named for.
    """

    def __init__(
        self,
        inner: HttpClient,
        *,
        robots: RobotsPolicy,
        limiter: RateLimiter,
        enforce_robots: bool = True,
    ) -> None:
        self._inner = inner
        self._robots = robots
        self._limiter = limiter
        self._enforce_robots = enforce_robots

    def get(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> HttpResponse:
        """Fetch ``url``, after checking permission and waiting our turn.

        Raises:
            RobotsDisallowedError: If ``robots.txt`` refuses this URL.
            HttpError: Whatever the wrapped client raises.
        """
        if self._enforce_robots and not self._robots.is_allowed(url):
            logger.info("robots.disallowed", url=url)
            raise RobotsDisallowedError(url)

        origin = origin_of(url)
        # Checking robots may itself have hit this host, and the limiter was
        # updated when it did -- so the gap is measured from the last real
        # request either way, and the permission check cannot be used to
        # sidestep the pacing it exists alongside.
        waited = self._limiter.wait(origin, min_interval=self._robots.crawl_delay(url))
        if waited > 0:
            logger.debug("ratelimit.waited", origin=origin, seconds=round(waited, 3))

        return self._inner.get(url, params=params, headers=headers)

    def close(self) -> None:
        """Close the wrapped client, if it can be closed."""
        closer = getattr(self._inner, "close", None)
        if callable(closer):
            closer()
