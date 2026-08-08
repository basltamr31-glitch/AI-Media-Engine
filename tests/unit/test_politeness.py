"""Tests for robots.txt compliance and per-host rate limiting.

The behaviours pinned here are the ones whose absence is invisible: a crawler
that ignores a disallow still returns pages, and one that never pauses still
works -- right up until it is blocked, at which point the symptom looks like an
extraction bug rather than a politeness one.

Time is injected everywhere. A test suite that sleeps for real is one that gets
marked slow and then skipped.
"""

from __future__ import annotations

from typing import Any

import pytest

from engine.adapters.politeness import (
    MAX_HONOURED_CRAWL_DELAY_SECONDS,
    PoliteHttpClient,
    RateLimiter,
    RobotsPolicy,
    origin_of,
)
from engine.core.ports.http import HttpError, HttpResponse, RobotsDisallowedError

ROBOTS = """
User-agent: *
Disallow: /private/
Crawl-delay: 3

User-agent: ai-media-engine
Disallow: /secret/
"""

UA = "ai-media-engine/0.1 (+https://example.test)"


class FakeHttp:
    """Records every URL requested, and answers from a scripted map."""

    def __init__(self, bodies: dict[str, str] | None = None, error: HttpError | None = None):
        self.bodies = bodies or {}
        self.error = error
        self.requested: list[str] = []

    def get(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> HttpResponse:
        self.requested.append(url)
        if url.endswith("/robots.txt"):
            if self.error is not None:
                raise self.error
            if url not in self.bodies:
                raise HttpError(url, "not found", status_code=404)
        return HttpResponse(url=url, status_code=200, text=self.bodies.get(url, "page"))


class FakeClock:
    """A monotonic clock that only moves when something sleeps."""

    def __init__(self) -> None:
        self.now = 1000.0
        self.slept: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


def policy(http: FakeHttp, clock: FakeClock, **kwargs: Any) -> RobotsPolicy:
    return RobotsPolicy(http, user_agent=UA, monotonic=clock.monotonic, **kwargs)


class TestOrigin:
    def test_scheme_and_host_only(self):
        assert origin_of("https://example.test/a/b?c=d#e") == "https://example.test"

    def test_a_port_is_part_of_the_origin(self):
        assert origin_of("http://example.test:8080/x") == "http://example.test:8080"

    def test_schemes_are_separate_origins(self):
        """Different robots.txt files, so they must not share a cache entry."""
        assert origin_of("http://example.test/x") != origin_of("https://example.test/x")


class TestRobotsPermission:
    def test_a_disallowed_path_is_refused(self):
        http = FakeHttp({"https://example.test/robots.txt": ROBOTS})

        assert policy(http, FakeClock()).is_allowed("https://example.test/secret/doc") is False

    def test_an_unlisted_path_is_allowed(self):
        http = FakeHttp({"https://example.test/robots.txt": ROBOTS})

        assert policy(http, FakeClock()).is_allowed("https://example.test/news/1") is True

    def test_our_named_group_wins_over_the_wildcard(self):
        """A site that addresses us by name governs us entirely.

        The wildcard disallows /private/ and our group does not, so a client
        that merged the two rules would refuse a page we are allowed to read.
        """
        http = FakeHttp({"https://example.test/robots.txt": ROBOTS})

        assert policy(http, FakeClock()).is_allowed("https://example.test/private/x") is True

    def test_a_missing_robots_file_allows_everything(self):
        """RFC 9309: 404 means the site has published no rules."""
        http = FakeHttp()

        assert policy(http, FakeClock()).is_allowed("https://example.test/anything") is True

    def test_an_unreachable_robots_file_disallows_everything(self):
        """RFC 9309: 5xx means rules exist and we failed to read them.

        The opposite answer to a 404, from a superficially similar failure.
        Guessing 'allowed' here is how a crawler ignores a disallow during
        exactly the outage that made the server fragile.
        """
        http = FakeHttp(error=HttpError("u", "server error", status_code=503))

        assert policy(http, FakeClock()).is_allowed("https://example.test/anything") is False

    def test_a_transport_failure_disallows_everything(self):
        """No status at all is 'unreachable', not 'missing'."""
        http = FakeHttp(error=HttpError("u", "connection reset"))

        assert policy(http, FakeClock()).is_allowed("https://example.test/anything") is False


class TestRobotsCaching:
    def test_one_fetch_serves_many_urls_on_one_host(self):
        """Without this the politeness mechanism is itself the impolite part."""
        http = FakeHttp({"https://example.test/robots.txt": ROBOTS})
        subject = policy(http, FakeClock())

        for number in range(5):
            subject.is_allowed(f"https://example.test/news/{number}")

        assert http.requested == ["https://example.test/robots.txt"]

    def test_each_origin_is_fetched_separately(self):
        http = FakeHttp(
            {
                "https://a.test/robots.txt": ROBOTS,
                "https://b.test/robots.txt": ROBOTS,
            }
        )
        subject = policy(http, FakeClock())

        subject.is_allowed("https://a.test/x")
        subject.is_allowed("https://b.test/x")

        assert http.requested == ["https://a.test/robots.txt", "https://b.test/robots.txt"]

    def test_the_cache_expires(self):
        http = FakeHttp({"https://example.test/robots.txt": ROBOTS})
        clock = FakeClock()
        subject = policy(http, clock, ttl_seconds=100)

        subject.is_allowed("https://example.test/x")
        clock.now += 101
        subject.is_allowed("https://example.test/x")

        assert len(http.requested) == 2

    def test_a_failed_fetch_is_cached_too(self):
        """Otherwise an unreachable host is re-asked once per story."""
        http = FakeHttp(error=HttpError("u", "boom", status_code=500))
        subject = policy(http, FakeClock())

        subject.is_allowed("https://example.test/a")
        subject.is_allowed("https://example.test/b")

        assert len(http.requested) == 1


class TestCrawlDelay:
    def test_the_wildcard_delay_applies_when_no_named_group_matches(self):
        http = FakeHttp({"https://example.test/robots.txt": "User-agent: *\nCrawl-delay: 7\n"})

        assert policy(http, FakeClock()).crawl_delay("https://example.test/x") == 7.0

    def test_a_named_group_without_a_delay_does_not_inherit_the_wildcard(self):
        """Our group governs us; the wildcard's delay is addressed to others."""
        http = FakeHttp({"https://example.test/robots.txt": ROBOTS})

        assert policy(http, FakeClock()).crawl_delay("https://example.test/x") is None

    def test_an_absurd_delay_is_capped(self):
        """Past the cap the right response is fewer pages, not a stalled run."""
        http = FakeHttp({"https://example.test/robots.txt": "User-agent: *\nCrawl-delay: 600\n"})

        delay = policy(http, FakeClock()).crawl_delay("https://example.test/x")

        assert delay == MAX_HONOURED_CRAWL_DELAY_SECONDS

    def test_no_robots_file_means_no_declared_delay(self):
        assert policy(FakeHttp(), FakeClock()).crawl_delay("https://example.test/x") is None


class TestRateLimiter:
    def test_the_first_request_to_a_host_does_not_wait(self):
        clock = FakeClock()
        limiter = RateLimiter(min_interval_seconds=2, monotonic=clock.monotonic, sleep=clock.sleep)

        assert limiter.wait("https://example.test") == 0.0
        assert clock.slept == []

    def test_a_second_request_waits_out_the_interval(self):
        clock = FakeClock()
        limiter = RateLimiter(min_interval_seconds=2, monotonic=clock.monotonic, sleep=clock.sleep)

        limiter.wait("https://example.test")
        waited = limiter.wait("https://example.test")

        assert waited == pytest.approx(2.0)
        assert clock.slept == [pytest.approx(2.0)]

    def test_time_already_elapsed_counts_towards_the_interval(self):
        """Pacing, not a fixed toll: work done between requests is not wasted."""
        clock = FakeClock()
        limiter = RateLimiter(min_interval_seconds=5, monotonic=clock.monotonic, sleep=clock.sleep)

        limiter.wait("https://example.test")
        clock.now += 4
        waited = limiter.wait("https://example.test")

        assert waited == pytest.approx(1.0)

    def test_hosts_are_paced_independently(self):
        clock = FakeClock()
        limiter = RateLimiter(min_interval_seconds=2, monotonic=clock.monotonic, sleep=clock.sleep)

        limiter.wait("https://a.test")

        assert limiter.wait("https://b.test") == 0.0

    def test_a_declared_delay_overrides_the_default(self):
        clock = FakeClock()
        limiter = RateLimiter(min_interval_seconds=1, monotonic=clock.monotonic, sleep=clock.sleep)

        limiter.wait("https://example.test")
        waited = limiter.wait("https://example.test", min_interval=6)

        assert waited == pytest.approx(6.0)


class TestPoliteHttpClient:
    def build(self, http: FakeHttp, clock: FakeClock, *, enforce: bool = True) -> PoliteHttpClient:
        limiter = RateLimiter(min_interval_seconds=2, monotonic=clock.monotonic, sleep=clock.sleep)
        return PoliteHttpClient(
            http,
            robots=policy(http, clock, limiter=limiter),
            limiter=limiter,
            enforce_robots=enforce,
        )

    def test_an_allowed_page_is_fetched(self):
        http = FakeHttp({"https://example.test/robots.txt": ROBOTS})

        response = self.build(http, FakeClock()).get("https://example.test/news/1")

        assert response.status_code == 200
        assert "https://example.test/news/1" in http.requested

    def test_a_disallowed_page_raises_and_is_never_requested(self):
        http = FakeHttp({"https://example.test/robots.txt": ROBOTS})
        client = self.build(http, FakeClock())

        with pytest.raises(RobotsDisallowedError):
            client.get("https://example.test/secret/doc")

        assert http.requested == ["https://example.test/robots.txt"]

    def test_the_refusal_is_an_http_error_too(self):
        """Callers that treat all transport problems alike keep working."""
        http = FakeHttp({"https://example.test/robots.txt": ROBOTS})
        client = self.build(http, FakeClock())

        with pytest.raises(HttpError):
            client.get("https://example.test/secret/doc")

    def test_robots_can_be_disabled_for_your_own_sites(self):
        http = FakeHttp({"https://example.test/robots.txt": ROBOTS})

        response = self.build(http, FakeClock(), enforce=False).get(
            "https://example.test/secret/doc"
        )

        assert response.status_code == 200

    def test_fetching_robots_counts_towards_the_host_pacing(self):
        """Otherwise a run over 25 hosts issues 50 requests believing it made 25."""
        http = FakeHttp({"https://example.test/robots.txt": ROBOTS})
        clock = FakeClock()

        self.build(http, clock).get("https://example.test/news/1")

        # The robots fetch was request one; the page waited its turn behind it.
        assert clock.slept == [pytest.approx(2.0)]

    def test_a_declared_crawl_delay_paces_the_page_fetch(self):
        http = FakeHttp({"https://example.test/robots.txt": "User-agent: *\nCrawl-delay: 9\n"})
        clock = FakeClock()

        self.build(http, clock).get("https://example.test/news/1")

        assert clock.slept == [pytest.approx(9.0)]
