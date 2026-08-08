"""Tests for the arXiv feed parser.

One entry, one paper -- the opposite shape from a newsletter. These use the
body text arXiv actually emits, header and all, because the header is the only
part that needs arXiv-specific handling.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from engine.adapters.clock import FixedClock
from engine.adapters.parsers.arxiv import PARSER_KEY, PARSER_VERSION, ArxivFeedParser
from engine.core.domain.source import SourceDocument

ABSTRACT = (
    "LLM-based agents are increasingly deployed for data-related tasks. "
    "However, their performance depends on the clarity of data semantics."
)
BODY = f"arXiv:2608.02604v1 Announce Type: new \nAbstract: {ABSTRACT}"


def entry(
    *,
    body: str = BODY,
    title: str | None = "ISEE: Interactive Semantic Enrichment for Database Fields",
    url: str | None = "https://arxiv.org/abs/2608.02604",
    author: str | None = "Yuan Tian, Yiru Chen, Rakesh R. Menon",
    tags: list[str] | None = None,
) -> SourceDocument:
    return SourceDocument(
        workspace_id="ws_test",
        source_id="src_test",
        content_hash="hash",
        title=title,
        author=author,
        url=url,
        raw_content=body,
        meta={"tags": tags if tags is not None else ["cs.AI", "cs.IR"]},
    )


@pytest.fixture
def parser() -> ArxivFeedParser:
    return ArxivFeedParser(clock=FixedClock(datetime(2026, 8, 6, 9, 0, tzinfo=UTC)))


class TestCanParse:
    def test_an_entry_with_a_title_is_accepted(self, parser: ArxivFeedParser):
        assert parser.can_parse(entry())

    def test_an_entry_without_a_title_is_declined(self, parser: ArxivFeedParser):
        assert not parser.can_parse(entry(title=None))


class TestExtraction:
    def test_one_entry_yields_one_candidate(self, parser: ArxivFeedParser):
        assert len(parser.parse(entry())) == 1

    def test_the_headline_is_the_paper_title(self, parser: ArxivFeedParser):
        candidate = parser.parse(entry())[0]
        assert candidate.headline.startswith("ISEE:")

    def test_the_announcement_header_is_stripped_from_the_summary(self, parser: ArxivFeedParser):
        """Otherwise every summary opens with "arXiv:2608.02604v1 Announce Type"."""
        candidate = parser.parse(entry())[0]
        assert candidate.summary == ABSTRACT
        assert "Announce Type" not in (candidate.summary or "")

    def test_the_abstract_page_is_the_primary_url(self, parser: ArxivFeedParser):
        """A paper is a primary source, so its link needs no enrichment detour."""
        assert parser.parse(entry())[0].primary_url == "https://arxiv.org/abs/2608.02604"

    def test_the_identifier_and_version_are_separated(self, parser: ArxivFeedParser):
        meta = parser.parse(entry())[0].meta
        assert meta["arxiv_id"] == "2608.02604"
        assert meta["arxiv_version"] == 1

    def test_categories_are_carried_through(self, parser: ArxivFeedParser):
        meta = parser.parse(entry())[0].meta
        assert meta["categories"] == ["cs.AI", "cs.IR"]
        assert meta["primary_category"] == "cs.AI"

    def test_authors_are_kept(self, parser: ArxivFeedParser):
        assert "Yuan Tian" in parser.parse(entry())[0].meta["authors"]

    def test_the_parser_stamps_its_identity(self, parser: ArxivFeedParser):
        meta = parser.parse(entry())[0].meta
        assert meta["parser"] == PARSER_KEY
        assert meta["parser_version"] == PARSER_VERSION

    def test_the_raw_entry_is_retained_for_traceability(self, parser: ArxivFeedParser):
        assert parser.parse(entry())[0].raw_excerpt == BODY

    def test_extraction_time_comes_from_the_clock(self, parser: ArxivFeedParser):
        assert parser.parse(entry())[0].extracted_at == datetime(2026, 8, 6, 9, 0, tzinfo=UTC)


class TestAnnouncementTypes:
    def test_a_new_paper_is_a_story(self, parser: ArxivFeedParser):
        assert len(parser.parse(entry())) == 1

    def test_a_cross_listing_is_a_story(self, parser: ArxivFeedParser):
        """First appearance in this category, so first time we would see it."""
        body = BODY.replace("Announce Type: new", "Announce Type: cross")
        assert len(parser.parse(entry(body=body))) == 1

    def test_a_revision_is_not(self, parser: ArxivFeedParser):
        """Covering it would mean publishing the same paper twice."""
        body = BODY.replace("Announce Type: new", "Announce Type: replace")
        assert parser.parse(entry(body=body)) == []

    def test_the_announce_type_is_recorded(self, parser: ArxivFeedParser):
        assert parser.parse(entry())[0].meta["announce_type"] == "new"

    def test_a_workspace_can_ask_for_revisions(self, parser: ArxivFeedParser):
        body = BODY.replace("Announce Type: new", "Announce Type: replace")
        candidates = parser.parse(entry(body=body), config={"announce_types": ["replace"]})
        assert len(candidates) == 1

    def test_an_empty_config_falls_back_to_the_defaults(self, parser: ArxivFeedParser):
        assert len(parser.parse(entry(), config={})) == 1


class TestDegradedInput:
    def test_a_missing_header_still_yields_a_candidate(self, parser: ArxivFeedParser):
        """A feed change must degrade the summary, not lose the paper."""
        candidate = parser.parse(entry(body=f"Abstract: {ABSTRACT}"))[0]
        assert candidate.summary == ABSTRACT

    def test_a_body_with_neither_header_nor_abstract_falls_back_to_the_text(
        self, parser: ArxivFeedParser
    ):
        candidate = parser.parse(entry(body="Some unexpected text."))[0]
        assert candidate.summary == "Some unexpected text."

    def test_an_unversioned_identifier_is_handled(self, parser: ArxivFeedParser):
        body = BODY.replace("2608.02604v1", "2608.02604")
        meta = parser.parse(entry(body=body))[0].meta
        assert meta["arxiv_id"] == "2608.02604"
        assert "arxiv_version" not in meta

    def test_an_entry_without_a_title_yields_nothing(self, parser: ArxivFeedParser):
        assert parser.parse(entry(title="   ")) == []

    def test_an_entry_without_a_link_yields_a_candidate_with_no_url(self, parser: ArxivFeedParser):
        assert parser.parse(entry(url=None))[0].primary_url is None

    def test_missing_categories_are_simply_absent(self, parser: ArxivFeedParser):
        assert "categories" not in parser.parse(entry(tags=[]))[0].meta
