"""Tests for the Hacker News parser.

`tests/fixtures/hackernews_item.json` is a real front-page item, trimmed of its
comment-id list. The rest are constructed to cover shapes the capture does not
contain -- text posts, job placements, malformed payloads -- all of which the
API returns in normal operation.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from engine.adapters.clock import FixedClock
from engine.adapters.parsers.hackernews import (
    PARSER_KEY,
    PARSER_VERSION,
    HackerNewsParser,
)
from engine.core.domain.source import SourceDocument

FIXTURE = Path(__file__).parent.parent / "fixtures" / "hackernews_item.json"


def item(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "objectID": "49184960",
        "title": "Discovery Loop",
        "url": "https://www.discoveryloop.com/",
        "author": "xtreak29",
        "points": 430,
        "num_comments": 270,
        "story_text": None,
        "_tags": ["story", "author_xtreak29", "front_page"],
    }
    payload.update(overrides)
    return payload


def document(payload: dict[str, Any] | str) -> SourceDocument:
    raw = payload if isinstance(payload, str) else json.dumps(payload)
    parsed = payload if isinstance(payload, dict) else {}
    return SourceDocument(
        workspace_id="ws_test",
        source_id="src_test",
        content_hash="hash",
        external_id=str(parsed.get("objectID") or ""),
        title=parsed.get("title"),
        author=parsed.get("author"),
        url=parsed.get("url"),
        content_type="application/json",
        raw_content=raw,
    )


@pytest.fixture
def parser() -> HackerNewsParser:
    return HackerNewsParser(clock=FixedClock(datetime(2026, 8, 6, 9, 0, tzinfo=UTC)))


@pytest.fixture
def captured() -> SourceDocument:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return document(payload)


class TestCanParse:
    def test_json_is_accepted(self, parser: HackerNewsParser):
        assert parser.can_parse(document(item()))

    def test_a_non_json_payload_is_declined(self, parser: HackerNewsParser):
        assert not parser.can_parse(document("<html></html>"))


class TestCapturedItem:
    def test_it_yields_one_candidate(self, parser: HackerNewsParser, captured: SourceDocument):
        assert len(parser.parse(captured)) == 1

    def test_the_submitted_link_is_the_primary_url(
        self, parser: HackerNewsParser, captured: SourceDocument
    ):
        candidate = parser.parse(captured)[0]
        assert candidate.primary_url is not None
        assert "news.ycombinator.com" not in candidate.primary_url

    def test_attention_numbers_are_preserved(
        self, parser: HackerNewsParser, captured: SourceDocument
    ):
        """The API returns *current* counts; today's number is unrecoverable later."""
        meta = parser.parse(captured)[0].meta
        assert meta["points"] == 430
        assert meta["comments"] == 270

    def test_the_discussion_is_always_reachable(
        self, parser: HackerNewsParser, captured: SourceDocument
    ):
        meta = parser.parse(captured)[0].meta
        assert meta["discussion_url"] == "https://news.ycombinator.com/item?id=49184960"


class TestExtraction:
    def test_the_headline_is_the_item_title(self, parser: HackerNewsParser):
        assert parser.parse(document(item()))[0].headline == "Discovery Loop"

    def test_campaign_parameters_are_stripped_from_the_link(self, parser: HackerNewsParser):
        """The same article submitted twice must produce one URL."""
        candidate = parser.parse(document(item(url="https://x.test/a?utm_source=hn&id=2")))[0]
        assert candidate.primary_url == "https://x.test/a?id=2"

    def test_the_parser_stamps_its_identity(self, parser: HackerNewsParser):
        meta = parser.parse(document(item()))[0].meta
        assert meta["parser"] == PARSER_KEY
        assert meta["parser_version"] == PARSER_VERSION

    def test_submitter_text_becomes_the_summary_without_its_markup(self, parser: HackerNewsParser):
        """Stray HTML would otherwise be quoted verbatim in a generated post."""
        candidate = parser.parse(
            document(item(story_text="<p>We built <i>this</i> over a weekend.</p>"))
        )[0]
        assert candidate.summary == "We built this over a weekend."

    def test_an_item_with_no_text_has_no_summary(self, parser: HackerNewsParser):
        assert parser.parse(document(item()))[0].summary is None

    def test_extraction_time_comes_from_the_clock(self, parser: HackerNewsParser):
        candidate = parser.parse(document(item()))[0]
        assert candidate.extracted_at == datetime(2026, 8, 6, 9, 0, tzinfo=UTC)


class TestTextPosts:
    def test_a_post_with_no_link_points_at_its_own_discussion(self, parser: HackerNewsParser):
        """For an Ask HN, the thread *is* the primary source."""
        candidate = parser.parse(document(item(title="Ask HN: How do you test?", url=None)))[0]
        assert candidate.primary_url == "https://news.ycombinator.com/item?id=49184960"
        assert candidate.meta["self_post"] is True

    def test_a_linked_story_is_not_marked_as_a_self_post(self, parser: HackerNewsParser):
        assert "self_post" not in parser.parse(document(item()))[0].meta


class TestAdvertRemoval:
    def test_job_placements_are_dropped(self, parser: HackerNewsParser):
        """HN's equivalent of a sponsor block."""
        payload = item(title="Acme (YC W21) is hiring", _tags=["job", "author_acme"])
        assert parser.parse(document(payload)) == []

    def test_the_drop_list_comes_from_source_config(self, parser: HackerNewsParser):
        payload = item(_tags=["story", "poll"])
        assert parser.parse(document(payload), config={"drop_tags": ["poll"]}) == []

    def test_an_empty_config_falls_back_to_the_defaults(self, parser: HackerNewsParser):
        assert len(parser.parse(document(item()), config={})) == 1

    def test_tags_are_recorded_for_later_filtering(self, parser: HackerNewsParser):
        assert "front_page" in parser.parse(document(item()))[0].meta["tags"]


class TestDegradedInput:
    def test_a_malformed_payload_raises_for_the_stage_to_isolate(self, parser: HackerNewsParser):
        with pytest.raises(json.JSONDecodeError):
            parser.parse(document("{not json"))

    def test_a_json_array_is_rejected_with_a_clear_message(self, parser: HackerNewsParser):
        with pytest.raises(ValueError, match="expected a JSON object"):
            parser.parse(document("[1, 2, 3]"))

    def test_an_item_without_a_title_yields_nothing(self, parser: HackerNewsParser):
        assert parser.parse(document(item(title=""))) == []

    def test_missing_attention_numbers_are_simply_absent(self, parser: HackerNewsParser):
        meta = parser.parse(document(item(points=None, num_comments=None)))[0].meta
        assert "points" not in meta
        assert "comments" not in meta

    def test_an_item_with_neither_link_nor_id_has_no_url(self, parser: HackerNewsParser):
        payload = item(url=None, objectID="")
        assert parser.parse(document(payload))[0].primary_url is None
