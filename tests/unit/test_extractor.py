"""Tests for primary-source extraction.

The quality gates matter more than the extraction itself. Trafilatura will
happily return the two sentences a paywall shows you, and a story built from
those reads as though we had read the article. Every gate here exists because
the failure it prevents is silent.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from engine.adapters.clock import FixedClock
from engine.adapters.enrichment.extractor import EXTRACTION_METHOD, TrafilaturaExtractor
from engine.core.ports.enrichment import ExtractionError

PARAGRAPH = (
    "The company said the new model reduces inference cost by roughly forty percent "
    "while matching the previous generation on standard benchmarks, according to the "
    "technical report published alongside the announcement."
)


def _paragraphs(count: int) -> str:
    """Distinct paragraphs: extractors deduplicate repeated blocks, as they should."""
    return "".join(
        f"<p>{PARAGRAPH} Detail number {index} concerned the rollout schedule "
        f"and the regions affected in the first week.</p>"
        for index in range(count)
    )


def article(*, paragraphs: int = 5, title: str = "A genuine announcement") -> str:
    """A page shaped like a real article: navigation, body, footer, comments."""
    body = _paragraphs(paragraphs)
    return f"""
    <html><head><title>{title}</title>
      <meta name="author" content="Jane Reporter">
      <meta property="article:published_time" content="2026-08-01">
    </head>
    <body>
      <nav><a href="/">Home</a><a href="/about">About</a></nav>
      <div class="cookie-banner">We value your privacy. Accept all cookies?</div>
      <article><h1>{title}</h1>{body}</article>
      <aside class="newsletter">Subscribe to our newsletter for more!</aside>
      <div id="comments"><p>First! This is obviously fake news.</p></div>
      <footer>Copyright 2026</footer>
    </body></html>
    """


@pytest.fixture
def extractor() -> TrafilaturaExtractor:
    return TrafilaturaExtractor(clock=FixedClock(datetime(2026, 8, 6, 9, 0, tzinfo=UTC)))


class TestExtraction:
    def test_it_recovers_the_article_text(self, extractor: TrafilaturaExtractor):
        enriched = extractor.extract(article(), url="https://example.com/post")
        assert "reduces inference cost" in enriched.text

    def test_navigation_and_comments_are_left_behind(self, extractor: TrafilaturaExtractor):
        """A sidebar quoted as if it were the article is an accuracy problem."""
        enriched = extractor.extract(article(), url="https://example.com/post")
        for noise in ("Subscribe to our newsletter", "obviously fake news", "Accept all cookies"):
            assert noise not in enriched.text

    def test_the_url_is_recorded(self, extractor: TrafilaturaExtractor):
        enriched = extractor.extract(article(), url="https://example.com/post")
        assert enriched.url == "https://example.com/post"

    def test_the_extraction_method_is_recorded(self, extractor: TrafilaturaExtractor):
        """Which extractor produced this decides whether it is worth redoing."""
        enriched = extractor.extract(article(), url="https://example.com/post")
        assert enriched.extraction_method == EXTRACTION_METHOD

    def test_fetch_time_comes_from_the_clock(self, extractor: TrafilaturaExtractor):
        enriched = extractor.extract(article(), url="https://example.com/post")
        assert enriched.fetched_at == datetime(2026, 8, 6, 9, 0, tzinfo=UTC)

    def test_metadata_is_recovered_when_present(self, extractor: TrafilaturaExtractor):
        enriched = extractor.extract(article(), url="https://example.com/post")
        assert enriched.title == "A genuine announcement"
        assert enriched.author is not None

    def test_a_publication_date_is_timezone_aware(self, extractor: TrafilaturaExtractor):
        """A naive datetime in a scheduled pipeline is a correctness bug."""
        enriched = extractor.extract(article(), url="https://example.com/post")
        if enriched.published_at is not None:
            assert enriched.published_at.tzinfo is not None

    def test_missing_metadata_is_not_fatal(self, extractor: TrafilaturaExtractor):
        """The text is what the story needs; a missing byline is not."""
        body = _paragraphs(5)
        enriched = extractor.extract(
            f"<html><body><article>{body}</article></body></html>",
            url="https://example.com/post",
        )
        assert enriched.text


class TestQualityGates:
    def test_a_paywall_stub_is_rejected(self, extractor: TrafilaturaExtractor):
        """It extracts cleanly, which is exactly what makes it dangerous."""
        stub = (
            "<html><body><article><p>Subscribers only. The first paragraph is free, "
            "and then the rest of this story is behind a paywall.</p></article></body></html>"
        )
        with pytest.raises(ExtractionError, match="characters extracted"):
            extractor.extract(stub, url="https://example.com/paywalled")

    def test_a_page_with_no_article_is_rejected(self, extractor: TrafilaturaExtractor):
        shell = "<html><body><div id='root'></div><script>render()</script></body></html>"
        with pytest.raises(ExtractionError, match="no article text"):
            extractor.extract(shell, url="https://example.com/spa")

    def test_an_empty_document_is_rejected(self, extractor: TrafilaturaExtractor):
        with pytest.raises(ExtractionError, match="empty document"):
            extractor.extract("   ", url="https://example.com/nothing")

    def test_the_threshold_is_configurable(self):
        """A workspace tracking release notes may legitimately want it lower."""
        lenient = TrafilaturaExtractor(
            clock=FixedClock(datetime(2026, 8, 6, tzinfo=UTC)), min_text_length=10
        )
        stub = (
            "<html><body><article><p>Subscribers only. The first paragraph is free, "
            "and then the rest is behind a paywall.</p></article></body></html>"
        )
        assert lenient.extract(stub, url="https://example.com/x").text

    def test_very_long_articles_are_truncated(self):
        """Unbounded text becomes unbounded prompt cost in Phase 3."""
        capped = TrafilaturaExtractor(
            clock=FixedClock(datetime(2026, 8, 6, tzinfo=UTC)), max_text_length=500
        )
        enriched = capped.extract(article(paragraphs=200), url="https://example.com/long")
        assert len(enriched.text) == 500

    def test_the_error_names_the_url(self, extractor: TrafilaturaExtractor):
        """ "Which story failed?" has to be answerable from the message alone."""
        with pytest.raises(ExtractionError, match=r"example\.com/spa"):
            extractor.extract("<html><body></body></html>", url="https://example.com/spa")
