"""Tests for the clustering keys and similarity measure.

These encode the asymmetry the resolve stage depends on: a link is strong
evidence of identity, wording is weak evidence. The similarity cases include
the false positive measured on the real corpus -- two different arXiv papers
that overlap 0.67 -- because a threshold that merges them is wrong however
tempting the ratio looks.
"""

from __future__ import annotations

import pytest

from engine.core.domain.matching import similarity, title_key, title_tokens, url_key


class TestUrlKey:
    def test_the_same_article_produces_one_key(self):
        assert url_key("https://example.com/post") == url_key("http://www.example.com/post/")

    @pytest.mark.parametrize(
        "url",
        [
            "https://example.com/post?utm_source=tldr",
            "https://example.com/post#section",
            "https://EXAMPLE.com/POST",
            "https://m.example.com/post",
            "https://amp.example.com/post/amp",
            "https://example.com:443/post",
        ],
    )
    def test_decoration_does_not_change_identity(self, url: str):
        assert url_key(url) == "example.com/post"

    def test_the_path_still_distinguishes(self):
        """Normalisation must not collapse two different articles."""
        assert url_key("https://example.com/a") != url_key("https://example.com/b")

    def test_html_suffixes_are_folded(self):
        assert url_key("https://example.com/post.html") == "example.com/post"

    @pytest.mark.parametrize("value", [None, "", "   ", "not-a-url", "mailto:a@b.test"])
    def test_unusable_links_yield_no_key(self, value: str | None):
        """None means unknown, and two unknowns must never match each other."""
        assert url_key(value) is None

    def test_a_bare_host_keys_on_the_host(self):
        assert url_key("https://example.com") == "example.com"


class TestTitleKey:
    def test_punctuation_and_case_do_not_change_identity(self):
        assert title_key("GPT-5: It's Here!") == title_key("gpt 5 it s here")

    def test_different_wording_produces_different_keys(self):
        """This is the strict fallback, not the fuzzy one."""
        assert title_key("OpenAI cuts prices") != title_key("OpenAI raises prices")

    @pytest.mark.parametrize("value", [None, "", "   ", "!!!"])
    def test_an_empty_headline_yields_no_key(self, value: str | None):
        assert title_key(value) is None


class TestTitleTokens:
    def test_stopwords_are_dropped(self):
        assert title_tokens("The rise of the enterprise") == {"rise", "enterprise"}

    def test_two_letter_subjects_are_kept(self):
        """Regression: a three-character floor deleted "AI" from an AI-news
        engine, so the topic "AI agents" matched on "agents" alone."""
        assert title_tokens("AI agents and the new OS") == {"ai", "agents", "os"}

    def test_single_characters_are_still_dropped(self):
        """Initials and list markers, never subjects."""
        assert "a" not in title_tokens("A b c model")

    def test_numbers_survive(self):
        """Version numbers are often the only thing distinguishing two stories."""
        assert "5" not in title_tokens("GPT-5 ships")
        assert "gpt" in title_tokens("GPT-5 ships")


class TestSimilarity:
    def test_identical_headlines_score_one(self):
        headline = "OpenAI cuts prices for its newest models"
        assert similarity(headline, headline) == 1.0

    def test_unrelated_headlines_score_zero(self):
        assert similarity("Anthropic ships Claude", "Tesla recalls trucks") == 0.0

    def test_too_few_shared_words_scores_zero_however_high_the_ratio(self):
        """Short headlines produce high ratios from coincidence alone."""
        assert similarity("Cloudflare Computer", "Cloudflare OS") == 0.0

    def test_the_measured_false_positive_is_still_below_the_default(self):
        """Two different arXiv papers. A threshold that merges them is wrong."""
        score = similarity(
            "On the missing benchmarks layer and a potential solution",
            "On the missing data layer and a potential solution",
        )
        assert 0.0 < score < 0.9

    def test_genuine_cross_source_pairs_score_lower_than_that(self):
        """Why fuzzy matching is not the primary rule: the ordering is wrong."""
        score = similarity(
            "OpenAI cuts prices for two of its GPT-5.6 AI models as companies grow",
            "OpenAI Cuts GPT-5.6 Prices",
        )
        assert score < 0.9

    def test_a_near_identical_rewording_scores_high(self):
        score = similarity(
            "Stacked pull requests are now in public preview",
            "Stacked Pull Requests Are Now In Public Preview",
        )
        assert score == 1.0

    @pytest.mark.parametrize("value", [None, ""])
    def test_a_missing_headline_scores_zero(self, value: str | None):
        assert similarity(value, "Anything at all here") == 0.0
