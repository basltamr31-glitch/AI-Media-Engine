"""Tests for the Gmail fetcher.

All of these run with no Google account, no credentials and no network, because
the fetcher depends on the ``GmailApiClient`` protocol rather than on
``googleapiclient``.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from engine.adapters.sources.email.gmail import GmailFetcher
from engine.core.domain.enums import SourceKind
from engine.core.domain.source import Source
from tests.fakes import FakeGmailClient, gmail_message


@pytest.fixture
def email_source(persisted_workspace):
    return Source(
        workspace_id=persisted_workspace.id,
        kind=SourceKind.EMAIL,
        name="TLDR AI",
        identifier="dan@tldrnewsletter.com",
        parser_key="tldr_newsletter",
    )


def build_fetcher(client: FakeGmailClient, clock, lookback_days: int = 7) -> GmailFetcher:
    return GmailFetcher(
        client_factory=lambda: client,
        clock=clock,
        lookback_days=lookback_days,
    )


class TestQueryConstruction:
    def test_filters_by_sender_and_lookback(self, email_source, clock):
        fetcher = build_fetcher(FakeGmailClient(), clock)
        query = fetcher.build_query(email_source)
        assert "from:(dan@tldrnewsletter.com)" in query
        assert "newer_than:7d" in query

    def test_source_config_can_override_lookback(self, email_source, clock):
        email_source.config = {"lookback_days": 2}
        fetcher = build_fetcher(FakeGmailClient(), clock)
        assert "newer_than:2d" in fetcher.build_query(email_source)

    def test_supports_label_subject_and_raw_terms(self, email_source, clock):
        email_source.config = {
            "label": "Newsletters",
            "subject": "TLDR AI",
            "query": "-category:promotions",
        }
        query = build_fetcher(FakeGmailClient(), clock).build_query(email_source)
        assert "label:Newsletters" in query
        assert 'subject:"TLDR AI"' in query
        assert "-category:promotions" in query

    def test_zero_lookback_omits_the_date_filter(self, email_source, clock):
        email_source.config = {"lookback_days": 0}
        assert "newer_than" not in build_fetcher(FakeGmailClient(), clock).build_query(email_source)


class TestFetch:
    def test_declares_the_email_transport(self, clock):
        assert build_fetcher(FakeGmailClient(), clock).kind is SourceKind.EMAIL

    def test_converts_messages_into_documents(self, email_source, clock):
        client = FakeGmailClient([gmail_message(message_id="m1", subject="TLDR AI 2026-08-04")])
        documents = build_fetcher(client, clock).fetch(email_source)

        assert len(documents) == 1
        document = documents[0]
        assert document.title == "TLDR AI 2026-08-04"
        assert document.external_id == "m1"
        assert document.author == "dan@tldrnewsletter.com"
        assert document.workspace_id == email_source.workspace_id
        assert document.source_id == email_source.id
        assert document.fetched_at == clock.now()

    def test_prefers_html_over_plain_text(self, email_source, clock):
        """The HTML part carries the outbound links Phase 2 needs."""
        client = FakeGmailClient([gmail_message(html="<h1>Rich</h1>", plain="Plain fallback")])
        document = build_fetcher(client, clock).fetch(email_source)[0]
        assert document.content_type == "text/html"
        assert document.raw_content == "<h1>Rich</h1>"

    def test_falls_back_to_plain_text(self, email_source, clock):
        client = FakeGmailClient([gmail_message(html=None, plain="Plain only")])
        document = build_fetcher(client, clock).fetch(email_source)[0]
        assert document.content_type == "text/plain"
        assert document.raw_content == "Plain only"

    def test_finds_bodies_in_deeply_nested_mime_trees(self, email_source, clock):
        """Real newsletters nest multipart/alternative inside multipart/mixed."""
        from tests.fakes import encode_part

        message = gmail_message(html=None, plain=None)
        message["payload"]["parts"] = [
            {
                "mimeType": "multipart/alternative",
                "parts": [
                    {"mimeType": "text/plain", "body": {"data": encode_part("nested plain")}},
                    {"mimeType": "text/html", "body": {"data": encode_part("<p>nested</p>")}},
                ],
            }
        ]
        document = build_fetcher(FakeGmailClient([message]), clock).fetch(email_source)[0]
        assert document.raw_content == "<p>nested</p>"

    def test_parses_the_date_header_into_utc(self, email_source, clock):
        client = FakeGmailClient([gmail_message(date="Mon, 04 Aug 2026 12:00:00 +0300")])
        document = build_fetcher(client, clock).fetch(email_source)[0]
        assert document.published_at == datetime(2026, 8, 4, 9, 0, tzinfo=UTC)

    def test_falls_back_to_internal_date(self, email_source, clock):
        client = FakeGmailClient([gmail_message(date="not a date")])
        document = build_fetcher(client, clock).fetch(email_source)[0]
        assert document.published_at == datetime(2026, 8, 4, 9, 0, tzinfo=UTC)

    def test_captures_newsletter_metadata(self, email_source, clock):
        document = build_fetcher(FakeGmailClient([gmail_message()]), clock).fetch(email_source)[0]
        assert document.meta["list_id"] == "<tldr.tldrnewsletter.com>"
        assert document.meta["thread_id"] == "thread-1"
        assert "CATEGORY_UPDATES" in document.meta["label_ids"]

    def test_respects_the_limit(self, email_source, clock):
        client = FakeGmailClient([gmail_message(message_id=f"m{i}") for i in range(10)])
        assert len(build_fetcher(client, clock).fetch(email_source, limit=3)) == 3

    def test_missing_subject_gets_a_placeholder(self, email_source, clock):
        message = gmail_message()
        message["payload"]["headers"] = [
            header for header in message["payload"]["headers"] if header["name"] != "Subject"
        ]
        document = build_fetcher(FakeGmailClient([message]), clock).fetch(email_source)[0]
        assert document.title == "(no subject)"


class TestContentHashing:
    def test_is_stable_across_refetches(self, email_source, clock):
        """The idempotency guarantee: the same message always hashes the same."""
        client = FakeGmailClient([gmail_message(message_id="stable")])
        first = build_fetcher(client, clock).fetch(email_source)[0]
        second = build_fetcher(client, clock).fetch(email_source)[0]
        assert first.content_hash == second.content_hash

    def test_differs_when_content_differs(self, email_source, clock):
        one = build_fetcher(
            FakeGmailClient([gmail_message(message_id="a", html="<p>one</p>")]), clock
        ).fetch(email_source)[0]
        two = build_fetcher(
            FakeGmailClient([gmail_message(message_id="b", html="<p>two</p>")]), clock
        ).fetch(email_source)[0]
        assert one.content_hash != two.content_hash


class TestResilience:
    def test_one_broken_message_does_not_lose_the_batch(self, email_source, clock):
        good = gmail_message(message_id="good")
        broken = gmail_message(message_id="broken")
        del broken["id"]  # forces a ValueError during conversion

        class PartiallyBrokenClient(FakeGmailClient):
            def get_message(self, message_id):
                return broken if message_id == "broken" else good

            def list_message_ids(self, *, query, max_results):
                return ["good", "broken"]

        documents = build_fetcher(PartiallyBrokenClient(), clock).fetch(email_source)
        assert len(documents) == 1
        assert documents[0].external_id == "good"

    def test_credentials_are_only_requested_when_fetching(self, clock):
        """Constructing the fetcher must not require a Google account."""
        calls = []

        def exploding_factory():
            calls.append(1)
            raise AssertionError("credentials must not be loaded eagerly")

        GmailFetcher(client_factory=exploding_factory, clock=clock)
        assert calls == []
