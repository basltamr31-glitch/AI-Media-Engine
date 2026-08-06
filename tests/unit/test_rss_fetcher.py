"""Tests for the RSS/Atom fetcher, driven by a saved arXiv fixture."""

from __future__ import annotations

from pathlib import Path

import pytest

from engine.adapters.sources.rss import RssFetcher
from engine.core.domain.enums import SourceKind
from engine.core.domain.source import Source
from engine.core.ports.http import HttpError
from tests.fakes import FakeHttpClient

FIXTURES = Path(__file__).parent.parent / "fixtures"
FEED_URL = "http://export.arxiv.org/rss/cs.AI"


@pytest.fixture
def feed_xml() -> str:
    return (FIXTURES / "arxiv_cs_ai.xml").read_text(encoding="utf-8")


@pytest.fixture
def rss_source(persisted_workspace):
    return Source(
        workspace_id=persisted_workspace.id,
        kind=SourceKind.RSS,
        name="arXiv cs.AI",
        identifier=FEED_URL,
        parser_key="arxiv_rss",
        trust_weight=0.75,
    )


class TestFetch:
    def test_declares_the_rss_transport(self, clock):
        assert RssFetcher(http=FakeHttpClient(), clock=clock).kind is SourceKind.RSS

    def test_converts_entries_into_documents(self, rss_source, clock, feed_xml):
        fetcher = RssFetcher(http=FakeHttpClient({FEED_URL: feed_xml}), clock=clock)
        documents = fetcher.fetch(rss_source)

        # Three items in the fixture, one of which has neither title nor link.
        assert len(documents) == 2
        first = documents[0]
        assert first.title == "Scaling Laws for Retrieval-Augmented Reasoning"
        assert first.url == "https://arxiv.org/abs/2608.01234"
        assert first.external_id == "oai:arXiv.org:2608.01234v1"
        assert "power-law relationship" in first.raw_content
        assert first.published_at is not None
        assert first.published_at.tzinfo is not None

    def test_skips_entries_with_neither_title_nor_link(self, rss_source, clock, feed_xml):
        fetcher = RssFetcher(http=FakeHttpClient({FEED_URL: feed_xml}), clock=clock)
        assert all(d.title or d.url for d in fetcher.fetch(rss_source))

    def test_captures_categories_as_tags(self, rss_source, clock, feed_xml):
        fetcher = RssFetcher(http=FakeHttpClient({FEED_URL: feed_xml}), clock=clock)
        documents = fetcher.fetch(rss_source)
        assert "cs.AI" in documents[0].meta["tags"]

    def test_respects_the_limit(self, rss_source, clock, feed_xml):
        fetcher = RssFetcher(http=FakeHttpClient({FEED_URL: feed_xml}), clock=clock)
        assert len(fetcher.fetch(rss_source, limit=1)) == 1

    def test_source_config_can_cap_items(self, rss_source, clock, feed_xml):
        rss_source.config = {"max_items": 1}
        fetcher = RssFetcher(http=FakeHttpClient({FEED_URL: feed_xml}), clock=clock)
        assert len(fetcher.fetch(rss_source)) == 1

    def test_content_hash_is_stable_across_refetches(self, rss_source, clock, feed_xml):
        fetcher = RssFetcher(http=FakeHttpClient({FEED_URL: feed_xml}), clock=clock)
        first = fetcher.fetch(rss_source)
        second = fetcher.fetch(rss_source)
        assert [d.content_hash for d in first] == [d.content_hash for d in second]

    def test_hashes_differ_between_entries(self, rss_source, clock, feed_xml):
        fetcher = RssFetcher(http=FakeHttpClient({FEED_URL: feed_xml}), clock=clock)
        documents = fetcher.fetch(rss_source)
        assert documents[0].content_hash != documents[1].content_hash


class TestResilience:
    def test_transport_errors_propagate_for_the_stage_to_isolate(self, rss_source, clock):
        """The fetcher does not swallow failures; IngestStage decides the policy."""
        fetcher = RssFetcher(
            http=FakeHttpClient(error=HttpError(FEED_URL, "connection reset")), clock=clock
        )
        with pytest.raises(HttpError):
            fetcher.fetch(rss_source)

    def test_malformed_feeds_still_yield_recoverable_entries(self, rss_source, clock):
        """Refusing slightly non-compliant XML would exclude much of the real web."""
        broken = """<?xml version="1.0"?><rss version="2.0"><channel>
            <title>Broken</title>
            <item><title>Survivor</title><link>https://example.com/1</link></item>
        """  # unclosed channel/rss
        fetcher = RssFetcher(http=FakeHttpClient({FEED_URL: broken}), clock=clock)
        documents = fetcher.fetch(rss_source)
        assert len(documents) == 1
        assert documents[0].title == "Survivor"

    def test_empty_feed_yields_no_documents(self, rss_source, clock):
        empty = (
            '<?xml version="1.0"?><rss version="2.0"><channel><title>Empty</title></channel></rss>'
        )
        fetcher = RssFetcher(http=FakeHttpClient({FEED_URL: empty}), clock=clock)
        assert fetcher.fetch(rss_source) == []
