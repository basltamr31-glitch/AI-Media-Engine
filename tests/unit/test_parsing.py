"""Tests for the shared ingestion parsing helpers.

Date handling gets disproportionate attention here because a naive or wrong
timestamp corrupts recency ranking silently -- the failure produces plausible
output, which is the expensive kind.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from engine.adapters.sources.parsing import (
    coerce_text,
    get_path,
    parse_datetime,
    truncate,
)


class TestParseDatetime:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("2026-08-04T09:00:00Z", datetime(2026, 8, 4, 9, 0, tzinfo=UTC)),
            ("2026-08-04T09:00:00+00:00", datetime(2026, 8, 4, 9, 0, tzinfo=UTC)),
            # +03:00 must be converted to UTC, not merely stored.
            ("2026-08-04T12:00:00+03:00", datetime(2026, 8, 4, 9, 0, tzinfo=UTC)),
            ("2026-08-04", datetime(2026, 8, 4, 0, 0, tzinfo=UTC)),
        ],
    )
    def test_parses_iso_8601(self, raw, expected):
        assert parse_datetime(raw) == expected

    def test_parses_rfc_2822_as_used_by_rss_and_email(self):
        assert parse_datetime("Mon, 04 Aug 2026 09:00:00 +0000") == datetime(
            2026, 8, 4, 9, 0, tzinfo=UTC
        )

    def test_parses_epoch_seconds(self):
        # Reddit's created_utc is epoch seconds, often as a float.
        assert parse_datetime(1785834000.0) == datetime(2026, 8, 4, 9, 0, tzinfo=UTC)

    def test_parses_epoch_milliseconds(self):
        """Gmail's internalDate is milliseconds; the magnitude disambiguates."""
        assert parse_datetime(1785834000000) == datetime(2026, 8, 4, 9, 0, tzinfo=UTC)

    def test_seconds_and_milliseconds_agree(self):
        """The heuristic must not silently place the same instant a day apart."""
        assert parse_datetime(1785834000) == parse_datetime(1785834000000)

    def test_naive_datetime_is_assumed_utc(self):
        result = parse_datetime(datetime(2026, 8, 4, 9, 0))
        assert result == datetime(2026, 8, 4, 9, 0, tzinfo=UTC)
        assert result.tzinfo is not None

    @pytest.mark.parametrize(
        "raw",
        [None, "", "   ", "not a date", [], {}, True, 12, 99_999_999_999_999_999],
    )
    def test_returns_none_rather_than_raising(self, raw):
        """A bad date must cost the timestamp, never the whole item."""
        assert parse_datetime(raw) is None

    def test_result_is_always_timezone_aware(self):
        for raw in ("2026-08-04T09:00:00Z", "Mon, 04 Aug 2026 09:00:00 +0000", 1785834000):
            parsed = parse_datetime(raw)
            assert parsed is not None
            assert parsed.tzinfo is not None


class TestGetPath:
    def test_reads_nested_dict_keys(self):
        assert get_path({"a": {"b": {"c": 7}}}, "a.b.c") == 7

    def test_reads_list_indices(self):
        assert get_path({"items": [{"n": "first"}]}, "items.0.n") == "first"

    def test_empty_path_returns_the_payload(self):
        """How a bare top-level array (GitHub Releases) is expressed in config."""
        payload = [{"id": 1}]
        assert get_path(payload, "") is payload

    @pytest.mark.parametrize("path", ["a.missing", "a.b.c.d", "items.9", "a.0"])
    def test_missing_segments_return_the_default(self, path):
        assert get_path({"a": {"b": 1}, "items": []}, path, default="fallback") == "fallback"

    def test_handles_the_reddit_double_nesting(self):
        payload = {"data": {"children": [{"data": {"title": "Hello"}}]}}
        assert get_path(payload, "data.children.0.data.title") == "Hello"


class TestCoerceText:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("  hello  ", "hello"),
            (42, "42"),
            (3.5, "3.5"),
            (True, "true"),
            (None, None),
            ("", None),
            ("   ", None),
            ([], None),
        ],
    )
    def test_coercion(self, raw, expected):
        assert coerce_text(raw) == expected


class TestTruncate:
    def test_leaves_short_values_untouched(self):
        assert truncate("short", 10) == "short"

    def test_trims_to_the_limit(self):
        assert truncate("abcdefghij", 4) == "abcd"

    def test_passes_none_through(self):
        assert truncate(None, 10) is None
