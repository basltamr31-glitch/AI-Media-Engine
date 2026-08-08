"""Tests for the TLDR newsletter parser.

Half of these run against `tests/fixtures/tldr_ai_issue.html` -- a real captured
issue, sanitised of the per-send tracking token and the subscriber's address.
Synthetic markup would let the parser pass while failing on the only input that
matters; the shape of real email HTML is the whole difficulty.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from engine.adapters.clock import FixedClock
from engine.adapters.parsers.tldr import (
    PARSER_KEY,
    PARSER_VERSION,
    TldrNewsletterParser,
    untrack,
)
from engine.core.domain.source import SourceDocument

FIXTURE = Path(__file__).parent.parent / "fixtures" / "tldr_ai_issue.html"

TRACKED = (
    "https://tracking.tldrnewsletter.com/CL0/"
    "https:%2F%2Fexample.com%2Fpost%3Futm_source=tldrai/1/SEND-TOKEN-REDACTED"
)


def document(markup: str, *, content_type: str = "text/html") -> SourceDocument:
    return SourceDocument(
        workspace_id="ws_test",
        source_id="src_test",
        content_hash="hash",
        raw_content=markup,
        content_type=content_type,
    )


def page(body: str, *, title: str = "TLDR AI") -> str:
    return f"<html><head><title>{title}</title></head><body>{body}</body></html>"


def story_block(headline: str, marker: str, summary: str, *, href: str = TRACKED) -> str:
    """One story, in the nesting TLDR actually emits."""
    return (
        '<table><tr><td><div class="text-block"><span>'
        f'<a href="{href}"><span><strong>{headline} ({marker})</strong></span></a>'
        f"<br><br><span>{summary}</span>"
        "</span></div></td></tr></table>"
    )


@pytest.fixture
def parser() -> TldrNewsletterParser:
    return TldrNewsletterParser(clock=FixedClock(datetime(2026, 8, 6, 9, 0, tzinfo=UTC)))


@pytest.fixture
def issue() -> SourceDocument:
    return document(FIXTURE.read_text(encoding="utf-8"))


class TestCanParse:
    def test_html_is_accepted(self, parser: TldrNewsletterParser):
        assert parser.can_parse(document(page("<p>hi</p>")))

    def test_an_empty_document_is_declined(self, parser: TldrNewsletterParser):
        assert not parser.can_parse(document("   "))

    def test_plain_text_without_markup_is_declined(self, parser: TldrNewsletterParser):
        assert not parser.can_parse(document("just words", content_type="text/plain"))


class TestRealIssue:
    def test_it_extracts_every_story(self, parser: TldrNewsletterParser, issue: SourceDocument):
        assert len(parser.parse(issue)) == 15

    def test_every_candidate_carries_a_headline_and_a_link(
        self, parser: TldrNewsletterParser, issue: SourceDocument
    ):
        """The link is the point: it is the lead enrichment follows."""
        candidates = parser.parse(issue)
        assert all(candidate.headline for candidate in candidates)
        assert all(candidate.primary_url for candidate in candidates)

    def test_the_reading_time_marker_is_not_left_in_the_headline(
        self, parser: TldrNewsletterParser, issue: SourceDocument
    ):
        for candidate in parser.parse(issue):
            assert "minute read" not in candidate.headline

    def test_candidates_are_attributed_to_their_section(
        self, parser: TldrNewsletterParser, issue: SourceDocument
    ):
        sections = {candidate.meta["section"] for candidate in parser.parse(issue)}
        assert "Headlines & Launches" in sections
        assert "Quick Links" in sections

    def test_the_edition_is_recorded(self, parser: TldrNewsletterParser, issue: SourceDocument):
        """One source receives eight editions, so it is a document property."""
        assert {c.meta["edition"] for c in parser.parse(issue)} == {"TLDR AI"}

    def test_ordinals_reflect_position(self, parser: TldrNewsletterParser, issue: SourceDocument):
        """Newsletters lead with what they rank highest."""
        candidates = parser.parse(issue)
        assert [c.ordinal for c in candidates] == list(range(len(candidates)))

    def test_no_tracking_token_survives(self, parser: TldrNewsletterParser, issue: SourceDocument):
        """The token identifies the send, and therefore the subscriber."""
        for candidate in parser.parse(issue):
            assert "tracking.tldrnewsletter.com" not in (candidate.primary_url or "")
            assert "SEND-TOKEN" not in (candidate.primary_url or "")

    def test_sponsored_items_are_removed(self, parser: TldrNewsletterParser, issue: SourceDocument):
        """The fixture carries three (Sponsor) placements."""
        headlines = " ".join(c.headline for c in parser.parse(issue))
        assert "(Sponsor)" not in headlines
        assert len(parser.parse(issue)) == 15

    def test_footer_links_are_not_stories(
        self, parser: TldrNewsletterParser, issue: SourceDocument
    ):
        """Referral, jobs and unsubscribe links carry no marker."""
        headlines = {c.headline.casefold() for c in parser.parse(issue)}
        for noise in ("unsubscribe", "apply here", "track your referrals here."):
            assert noise not in headlines

    def test_parsing_is_deterministic(self, parser: TldrNewsletterParser, issue: SourceDocument):
        first = parser.parse(issue)
        second = parser.parse(issue)
        assert [c.headline for c in first] == [c.headline for c in second]
        assert [c.primary_url for c in first] == [c.primary_url for c in second]


class TestMarkers:
    @pytest.mark.parametrize(
        "marker",
        ["5 minute read", "1 minute read", "20 minute podcast", "47 minute video"],
    )
    def test_duration_markers_are_stories(self, parser: TldrNewsletterParser, marker: str):
        assert len(parser.parse(document(page(story_block("A story", marker, "Body."))))) == 1

    @pytest.mark.parametrize("marker", ["Website", "GitHub Repo", "Hugging Face Repo", "Tool"])
    def test_resource_markers_are_stories(self, parser: TldrNewsletterParser, marker: str):
        assert len(parser.parse(document(page(story_block("A tool", marker, "Body."))))) == 1

    @pytest.mark.parametrize(
        "marker",
        ["Sponsor", "Sponsored", "Advertisement"],
    )
    def test_sponsored_markers_are_dropped(self, parser: TldrNewsletterParser, marker: str):
        assert parser.parse(document(page(story_block("Buy this", marker, "Body.")))) == []

    @pytest.mark.parametrize(
        "marker",
        ["Webinar", "FREE", "TLDR Curator, ~3 hrs/week"],
    )
    def test_promotions_and_job_adverts_are_not_stories(
        self, parser: TldrNewsletterParser, marker: str
    ):
        assert parser.parse(document(page(story_block("Join us", marker, "Body.")))) == []

    def test_a_link_with_no_marker_is_not_a_story(self, parser: TldrNewsletterParser):
        body = '<div class="text-block"><a href="https://x.test">unsubscribe</a></div>'
        assert parser.parse(document(page(body))) == []

    def test_a_headline_keeps_its_own_parentheses(self, parser: TldrNewsletterParser):
        block = story_block("GPT-5 (finally) ships", "3 minute read", "Body.")
        candidate = parser.parse(document(page(block)))[0]
        assert candidate.headline == "GPT-5 (finally) ships"


class TestSectionStripping:
    def _issue(self) -> str:
        return page(
            "<h1>Sponsor</h1>"
            + story_block("Paid placement", "5 minute read", "Body.")
            + "<h1>Big Tech</h1>"
            + story_block("Real news", "5 minute read", "Body.")
        )

    def test_a_stripped_section_takes_its_stories_with_it(self, parser: TldrNewsletterParser):
        candidates = parser.parse(document(self._issue()))
        assert [c.headline for c in candidates] == ["Real news"]

    def test_the_strip_list_comes_from_source_config(self, parser: TldrNewsletterParser):
        """Two sources sharing a format must be able to differ."""
        candidates = parser.parse(document(self._issue()), config={"strip_sections": ["big tech"]})
        assert [c.headline for c in candidates] == ["Paid placement"]

    def test_an_empty_config_falls_back_to_the_defaults(self, parser: TldrNewsletterParser):
        candidates = parser.parse(document(self._issue()), config={})
        assert [c.headline for c in candidates] == ["Real news"]


class TestSummaryAndMetadata:
    def test_the_summary_is_the_text_beside_the_link(self, parser: TldrNewsletterParser):
        block = story_block("A story", "5 minute read", "What actually happened.")
        candidate = parser.parse(document(page(block)))[0]
        assert candidate.summary == "What actually happened."

    def test_the_excerpt_keeps_the_block_verbatim(self, parser: TldrNewsletterParser):
        block = story_block("A story", "5 minute read", "What actually happened.")
        candidate = parser.parse(document(page(block)))[0]
        assert "A story" in (candidate.raw_excerpt or "")
        assert "What actually happened." in (candidate.raw_excerpt or "")

    def test_reading_time_is_captured_as_a_number(self, parser: TldrNewsletterParser):
        block = story_block("A story", "12 minute read", "Body.")
        candidate = parser.parse(document(page(block)))[0]
        assert candidate.meta["read_minutes"] == 12
        assert candidate.meta["medium"] == "read"

    def test_the_parser_stamps_its_identity_and_version(self, parser: TldrNewsletterParser):
        """A re-parsed backlog has to be distinguishable from an old one."""
        block = story_block("A story", "5 minute read", "Body.")
        candidate = parser.parse(document(page(block)))[0]
        assert candidate.meta["parser"] == PARSER_KEY
        assert candidate.meta["parser_version"] == PARSER_VERSION

    def test_the_same_link_is_not_extracted_twice(self, parser: TldrNewsletterParser):
        block = story_block("A story", "5 minute read", "Body.")
        assert len(parser.parse(document(page(block + block)))) == 1

    def test_extraction_time_comes_from_the_clock(self, parser: TldrNewsletterParser):
        block = story_block("A story", "5 minute read", "Body.")
        candidate = parser.parse(document(page(block)))[0]
        assert candidate.extracted_at == datetime(2026, 8, 6, 9, 0, tzinfo=UTC)


class TestUntrack:
    def test_it_recovers_the_destination(self):
        assert untrack(TRACKED) == "https://example.com/post"

    def test_it_drops_campaign_parameters(self):
        """The same article from two newsletters must produce one URL."""
        url = "https://example.com/a?utm_source=tldrai&utm_medium=email&id=7"
        assert untrack(url) == "https://example.com/a?id=7"

    def test_it_drops_a_subscriber_address(self):
        """Newsletter footer links carry the reader's own email."""
        url = "https://example.com/manage?email=someone@example.com"
        assert untrack(url) == "https://example.com/manage"

    def test_an_undecodable_tracking_url_yields_nothing(self):
        """Better no link than a link that leaks who asked for it."""
        assert untrack("https://tracking.tldrnewsletter.com/opaque") == ""

    def test_a_tracking_url_wrapping_a_non_url_yields_nothing(self):
        assert untrack("https://tracking.tldrnewsletter.com/CL0/mailto:a@b.test/1/tok") == ""

    def test_an_ordinary_url_passes_through(self):
        assert untrack("https://example.com/a") == "https://example.com/a"

    def test_an_empty_url_is_handled(self):
        assert untrack("") == ""


class TestUnresolvableLinks:
    def test_a_story_whose_link_cannot_be_decoded_is_still_a_candidate(
        self, parser: TldrNewsletterParser
    ):
        """Someone else may corroborate it with a usable link."""
        block = story_block(
            "A story", "5 minute read", "Body.", href="https://tracking.tldrnewsletter.com/opaque"
        )
        candidate = parser.parse(document(page(block)))[0]
        assert candidate.primary_url is None
        assert candidate.meta["unresolved_link"] is True
