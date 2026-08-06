"""Tests for the configuration-driven JSON API fetcher.

These are the tests that back the central extensibility claim: Hacker News,
Reddit and GitHub Releases have three completely different response shapes, and
all three are ingested by this one class with no code specific to any of them.
If that ever stops being true, these tests fail.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from engine.adapters.sources.json_api import JsonApiFetcher
from engine.core.domain.enums import SourceKind
from engine.core.domain.source import Source
from engine.core.ports.http import HttpError
from tests.fakes import FakeHttpClient, json_fixture

HN_URL = "https://hn.algolia.com/api/v1/search"
REDDIT_URL = "https://www.reddit.com/r/MachineLearning/top.json"
GITHUB_URL = "https://api.github.com/repos/vllm-project/vllm/releases"


def make_source(workspace_id: str, *, name: str, identifier: str, config: dict[str, Any]) -> Source:
    return Source(
        workspace_id=workspace_id,
        kind=SourceKind.API,
        name=name,
        identifier=identifier,
        config=config,
    )


# --- Hacker News: items nested under "hits" ---------------------------------

HN_PAYLOAD = {
    "hits": [
        {
            "objectID": "41234567",
            "title": "Show HN: An open-source inference server",
            "url": "https://github.com/example/server",
            "author": "someuser",
            "points": 342,
            "created_at": "2026-08-04T09:00:00.000Z",
            "story_text": None,
        },
        {
            "objectID": "41234568",
            "title": "A low-scoring story that should be filtered out",
            "url": "https://example.com/low",
            "author": "other",
            "points": 12,
            "created_at": "2026-08-04T08:00:00.000Z",
        },
    ]
}

HN_CONFIG = {
    "items_path": "hits",
    "request": {"params": {"tags": "front_page"}},
    "field_map": {
        "external_id": "objectID",
        "title": "title",
        "url": "url",
        "author": "author",
        "published_at": "created_at",
        "summary": "story_text",
    },
    "filters": {"min": {"points": 100}, "require": ["title"]},
}


class TestHackerNews:
    def test_extracts_items_from_a_nested_path(self, persisted_workspace, clock):
        source = make_source(persisted_workspace.id, name="HN", identifier=HN_URL, config=HN_CONFIG)
        http = FakeHttpClient({HN_URL: json_fixture(HN_PAYLOAD)})
        documents = JsonApiFetcher(http=http, clock=clock).fetch(source)

        assert len(documents) == 1  # the 12-point story is filtered out
        document = documents[0]
        assert document.title == "Show HN: An open-source inference server"
        assert document.url == "https://github.com/example/server"
        assert document.external_id == "41234567"
        assert document.author == "someuser"
        assert document.published_at is not None

    def test_forwards_configured_query_parameters(self, persisted_workspace, clock):
        source = make_source(persisted_workspace.id, name="HN", identifier=HN_URL, config=HN_CONFIG)
        http = FakeHttpClient({HN_URL: json_fixture(HN_PAYLOAD)})
        JsonApiFetcher(http=http, clock=clock).fetch(source)
        assert http.calls[0][1] == {"tags": "front_page"}

    def test_preserves_the_whole_item_as_raw_content(self, persisted_workspace, clock):
        """Phase 2 must be able to reach fields this map never mentioned."""
        source = make_source(persisted_workspace.id, name="HN", identifier=HN_URL, config=HN_CONFIG)
        http = FakeHttpClient({HN_URL: json_fixture(HN_PAYLOAD)})
        document = JsonApiFetcher(http=http, clock=clock).fetch(source)[0]
        assert json.loads(document.raw_content)["points"] == 342


# --- Reddit: double nesting, epoch dates ------------------------------------

REDDIT_PAYLOAD = {
    "data": {
        "children": [
            {
                "kind": "t3",
                "data": {
                    "id": "1abcdef",
                    "title": "New results on long-context retrieval",
                    "url": "https://arxiv.org/abs/2608.09999",
                    "author": "researcher",
                    "score": 412,
                    "created_utc": 1785920400.0,
                    "selftext": "Discussion of the paper.",
                },
            },
            {
                "kind": "t3",
                "data": {
                    "id": "2ghijkl",
                    "title": "Low-score post",
                    "url": "https://example.com/meh",
                    "author": "someone",
                    "score": 3,
                    "created_utc": 1785920400.0,
                    "selftext": "",
                },
            },
        ]
    }
}

REDDIT_CONFIG = {
    "items_path": "data.children",
    "field_map": {
        "external_id": "data.id",
        "title": "data.title",
        "url": "data.url",
        "author": "data.author",
        "published_at": "data.created_utc",
        "summary": "data.selftext",
    },
    "filters": {"min": {"data.score": 50}},
}


class TestReddit:
    def test_handles_double_nesting_with_no_code_changes(self, persisted_workspace, clock):
        """The whole point: a new provider is configuration, not a new class."""
        source = make_source(
            persisted_workspace.id, name="r/ML", identifier=REDDIT_URL, config=REDDIT_CONFIG
        )
        http = FakeHttpClient({REDDIT_URL: json_fixture(REDDIT_PAYLOAD)})
        documents = JsonApiFetcher(http=http, clock=clock).fetch(source)

        assert len(documents) == 1
        assert documents[0].title == "New results on long-context retrieval"
        assert documents[0].external_id == "1abcdef"

    def test_parses_epoch_timestamps(self, persisted_workspace, clock):
        source = make_source(
            persisted_workspace.id, name="r/ML", identifier=REDDIT_URL, config=REDDIT_CONFIG
        )
        http = FakeHttpClient({REDDIT_URL: json_fixture(REDDIT_PAYLOAD)})
        document = JsonApiFetcher(http=http, clock=clock).fetch(source)[0]
        assert document.published_at is not None
        assert document.published_at.year == 2026

    def test_filters_on_a_nested_numeric_field(self, persisted_workspace, clock):
        source = make_source(
            persisted_workspace.id, name="r/ML", identifier=REDDIT_URL, config=REDDIT_CONFIG
        )
        http = FakeHttpClient({REDDIT_URL: json_fixture(REDDIT_PAYLOAD)})
        titles = [d.title for d in JsonApiFetcher(http=http, clock=clock).fetch(source)]
        assert "Low-score post" not in titles


# --- GitHub Releases: a bare top-level array --------------------------------

GITHUB_PAYLOAD = [
    {
        "id": 987654,
        "name": "v0.9.0",
        "html_url": "https://github.com/vllm-project/vllm/releases/tag/v0.9.0",
        "author": {"login": "maintainer"},
        "published_at": "2026-08-04T09:00:00Z",
        "body": "Adds speculative decoding.",
        "draft": False,
        "prerelease": False,
    },
    {
        "id": 987655,
        "name": "v0.9.1-rc1",
        "html_url": "https://github.com/vllm-project/vllm/releases/tag/v0.9.1-rc1",
        "author": {"login": "maintainer"},
        "published_at": "2026-08-05T09:00:00Z",
        "body": "Release candidate.",
        "draft": False,
        "prerelease": True,
    },
]

GITHUB_CONFIG = {
    "items_path": "",
    "field_map": {
        "external_id": "id",
        "title": "name",
        "url": "html_url",
        "author": "author.login",
        "published_at": "published_at",
        "summary": "body",
    },
    "filters": {"exclude_true": ["draft", "prerelease"]},
}


class TestGitHubReleases:
    def test_handles_a_bare_top_level_array(self, persisted_workspace, clock):
        source = make_source(
            persisted_workspace.id, name="vLLM", identifier=GITHUB_URL, config=GITHUB_CONFIG
        )
        http = FakeHttpClient({GITHUB_URL: json_fixture(GITHUB_PAYLOAD)})
        documents = JsonApiFetcher(http=http, clock=clock).fetch(source)

        assert len(documents) == 1  # the prerelease is excluded
        assert documents[0].title == "v0.9.0"
        assert documents[0].author == "maintainer"  # resolved through author.login

    def test_exclude_true_drops_flagged_items(self, persisted_workspace, clock):
        source = make_source(
            persisted_workspace.id, name="vLLM", identifier=GITHUB_URL, config=GITHUB_CONFIG
        )
        http = FakeHttpClient({GITHUB_URL: json_fixture(GITHUB_PAYLOAD)})
        titles = [d.title for d in JsonApiFetcher(http=http, clock=clock).fetch(source)]
        assert "v0.9.1-rc1" not in titles


# --- Shared behaviour --------------------------------------------------------


class TestGeneralBehaviour:
    def test_declares_the_api_transport(self, clock):
        assert JsonApiFetcher(http=FakeHttpClient(), clock=clock).kind is SourceKind.API

    def test_bad_items_path_fails_with_an_actionable_message(self, persisted_workspace, clock):
        source = make_source(
            persisted_workspace.id,
            name="Broken",
            identifier=HN_URL,
            config={"items_path": "nope"},
        )
        http = FakeHttpClient({HN_URL: json_fixture(HN_PAYLOAD)})
        with pytest.raises(HttpError, match="did not resolve to a list"):
            JsonApiFetcher(http=http, clock=clock).fetch(source)

    def test_items_without_title_or_url_are_skipped(self, persisted_workspace, clock):
        payload = {"hits": [{"objectID": "1", "points": 500}]}
        source = make_source(persisted_workspace.id, name="HN", identifier=HN_URL, config=HN_CONFIG)
        http = FakeHttpClient({HN_URL: json_fixture(payload)})
        assert JsonApiFetcher(http=http, clock=clock).fetch(source) == []

    def test_missing_numeric_field_fails_the_minimum(self, persisted_workspace, clock):
        """Filters are a floor: absent evidence does not clear the bar."""
        payload = {"hits": [{"objectID": "1", "title": "No points field", "url": "https://e.com"}]}
        source = make_source(persisted_workspace.id, name="HN", identifier=HN_URL, config=HN_CONFIG)
        http = FakeHttpClient({HN_URL: json_fixture(payload)})
        assert JsonApiFetcher(http=http, clock=clock).fetch(source) == []

    def test_respects_the_limit(self, persisted_workspace, clock):
        payload = {
            "hits": [
                {
                    "objectID": str(i),
                    "title": f"Story {i}",
                    "url": f"https://e.com/{i}",
                    "points": 500,
                }
                for i in range(10)
            ]
        }
        source = make_source(persisted_workspace.id, name="HN", identifier=HN_URL, config=HN_CONFIG)
        http = FakeHttpClient({HN_URL: json_fixture(payload)})
        assert len(JsonApiFetcher(http=http, clock=clock).fetch(source, limit=4)) == 4

    def test_content_hash_is_stable_across_refetches(self, persisted_workspace, clock):
        source = make_source(persisted_workspace.id, name="HN", identifier=HN_URL, config=HN_CONFIG)
        http = FakeHttpClient({HN_URL: json_fixture(HN_PAYLOAD)})
        fetcher = JsonApiFetcher(http=http, clock=clock)
        assert fetcher.fetch(source)[0].content_hash == fetcher.fetch(source)[0].content_hash

    def test_invalid_json_is_reported_as_a_transport_error(self, persisted_workspace, clock):
        source = make_source(persisted_workspace.id, name="HN", identifier=HN_URL, config=HN_CONFIG)
        http = FakeHttpClient({HN_URL: "<html>an error page</html>"})
        with pytest.raises(HttpError):
            JsonApiFetcher(http=http, clock=clock).fetch(source)
