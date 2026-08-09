"""Prompt-construction tests.

The prompt is the product. These pin the two things that would silently cost
money or credibility if they drifted: what ends up inside the cached prefix
(everything stable, nothing per-story), and that generation is fed the
extracted primary source rather than a newsletter's summary of it.
"""

from __future__ import annotations

import pytest

from engine.adapters.generation.prompts import (
    build_cacheable_prefix,
    build_prompt,
    build_system,
    first_sentence,
    word_budget,
)
from engine.core.domain.content import BrandProfile
from engine.core.domain.enums import StoryStatus
from engine.core.domain.pack import ChannelDef, ProductDef, ProductKind
from engine.core.domain.story import EnrichedSource, Story

ARTICLE = "The laboratory released an open-weights model and published the training recipe."


def a_brand(**overrides) -> BrandProfile:
    defaults = {
        "workspace_id": "ws_1",
        "key": "house_en",
        "name": "House voice",
        "language": "en",
        "audience": "Engineers who already read AI news.",
        "tone_rules": ["Direct and specific."],
        "style_rules": ["First sentence carries the news."],
    }
    return BrandProfile(**(defaults | overrides))


def a_channel(**overrides) -> ChannelDef:
    defaults = {
        "key": "linkedin",
        "name": "LinkedIn",
        "max_characters": 3000,
        "supports_hashtags": True,
        "max_hashtags": 3,
        "supports_links": True,
        "preferred_products": ["social_post"],
        "format_notes": ["The first 3 lines appear before 'see more'."],
    }
    return ChannelDef(**(defaults | overrides))


def a_product() -> ProductDef:
    return ProductDef(key="social_post", name="Social post", kind=ProductKind.POST)


def a_story(*, enriched: bool = True, text: str = ARTICLE) -> Story:
    story = Story(
        workspace_id="ws_1",
        canonical_headline="An open model was released",
        primary_url="https://example.test/model",
        status=StoryStatus.SELECTED,
    )
    if enriched:
        story.enrichment = EnrichedSource(
            url="https://example.test/model", title="Open model", text=text
        )
    return story


class TestCacheablePrefix:
    def test_it_carries_the_voice(self):
        prefix = build_cacheable_prefix(brand=a_brand(), channel=a_channel(), product=a_product())

        assert "House voice" in prefix
        assert "Direct and specific." in prefix
        assert "First sentence carries the news." in prefix

    def test_it_states_the_channels_hard_limit(self):
        prefix = build_cacheable_prefix(brand=a_brand(), channel=a_channel(), product=a_product())

        assert "3000 characters" in prefix
        assert "At most 3 hashtags" in prefix

    def test_a_channel_without_links_is_told_to_name_the_source(self):
        """Instagram has no clickable links, so a pasted URL is dead text."""
        channel = a_channel(key="instagram", name="Instagram", supports_links=False)

        prefix = build_cacheable_prefix(brand=a_brand(), channel=channel, product=a_product())

        assert "not clickable" in prefix
        assert "Name the source" in prefix

    def test_a_channel_without_hashtags_is_told_to_return_none(self):
        channel = a_channel(supports_hashtags=False, max_hashtags=0)

        prefix = build_cacheable_prefix(brand=a_brand(), channel=channel, product=a_product())

        assert "does not use hashtags" in prefix

    def test_example_posts_ride_inside_the_cached_half(self):
        """Few-shot exemplars are the strongest lever on voice, and the most
        expensive thing to resend per story if they land on the wrong side."""
        brand = a_brand(example_posts=["A post we published earlier."])

        prefix = build_cacheable_prefix(brand=brand, channel=a_channel(), product=a_product())

        assert "A post we published earlier." in prefix

    def test_it_holds_nothing_story_specific(self):
        """Anything per-story in front of the cache boundary wastes the prefix."""
        prefix = build_cacheable_prefix(brand=a_brand(), channel=a_channel(), product=a_product())

        assert "open model" not in prefix.lower()
        assert ARTICLE not in prefix

    def test_it_is_identical_across_stories(self):
        first = build_cacheable_prefix(brand=a_brand(), channel=a_channel(), product=a_product())
        second = build_cacheable_prefix(brand=a_brand(), channel=a_channel(), product=a_product())

        assert first == second

    def test_the_language_is_named_in_words_not_as_a_tag(self):
        """Regression: gen-1 said "in ar", and a small model read the tag as
        decoration and wrote the Arabic post in English -- silently, because
        the asset was labelled `ar` either way."""
        prefix = build_cacheable_prefix(
            brand=a_brand(language="ar"), channel=a_channel(), product=a_product()
        )

        assert "Arabic" in prefix
        assert "in ar." not in prefix

    def test_an_unusual_language_falls_back_to_its_tag(self):
        """Better a tag than silently writing in the wrong language."""
        prefix = build_cacheable_prefix(
            brand=a_brand(language="mt"), channel=a_channel(), product=a_product()
        )

        assert "mt" in prefix

    def test_arabic_term_substitutions_are_carried(self):
        """The substitution table is what stops Arabic reading as translated."""
        brand = a_brand(language="ar", preferred_terms={"AI": "الذكاء الاصطناعي"})

        prefix = build_cacheable_prefix(brand=brand, channel=a_channel(), product=a_product())

        assert "الذكاء الاصطناعي" in prefix


class TestPrompt:
    def test_it_feeds_the_extracted_primary_source(self):
        """Not the newsletter's summary -- that would make this a republisher."""
        prompt = build_prompt(a_story(), language="English")

        assert ARTICLE in prompt
        assert "https://example.test/model" in prompt

    def test_a_story_with_no_primary_source_is_refused(self):
        with pytest.raises(ValueError, match="no extracted primary source"):
            build_prompt(a_story(enriched=False), language="English")

    def test_whitespace_only_source_text_is_refused(self):
        with pytest.raises(ValueError, match="no extracted primary source"):
            build_prompt(a_story(text="   \n  "), language="English")

    def test_long_sources_are_bounded(self):
        """An unbounded prompt silently loses its tail to the context window."""
        prompt = build_prompt(a_story(text="x" * 50_000), language="English", max_source_chars=100)

        assert len(prompt) < 1000


class TestFirstSentence:
    """The hook is computed, not requested.

    It used to be a schema field, and the golden set showed the model returning
    hashtags in it on every case -- so the fix was to stop asking.
    """

    def test_it_takes_the_opening_sentence(self):
        body = "The lab shipped open weights. A recipe followed. Then a paper."

        assert first_sentence(body) == "The lab shipped open weights."

    def test_an_arabic_full_stop_ends_a_sentence(self):
        """A distinct code point from the Latin one, and the whole Arabic post
        would otherwise come back as a single 'sentence'."""
        stop = "۔"
        body = f"أطلق المختبر نموذجًا مفتوح الأوزان{stop} ثم نشر الوصفة{stop}"

        assert first_sentence(body) == f"أطلق المختبر نموذجًا مفتوح الأوزان{stop}"

    def test_a_single_sentence_is_returned_whole(self):
        assert first_sentence("Only one sentence here.") == "Only one sentence here."

    def test_an_unpunctuated_post_is_bounded(self):
        assert len(first_sentence("word " * 200)) <= 200

    def test_an_empty_body_gives_an_empty_hook(self):
        assert first_sentence("   ") == ""


class TestSystem:
    def test_it_forbids_facts_the_source_does_not_contain(self):
        system = build_system()

        assert "nothing else" in system
        assert "do not estimate numbers" in system


class TestLengthConstraint:
    """gen-5. The limit was stated once, in the cached prefix, thousands of
    tokens before the model began writing -- the same recency failure gen-3
    fixed for language, left in place for length because only language had
    been caught at the time.
    """

    def test_the_limit_is_restated_after_the_source(self):
        prompt = build_prompt(a_story(), language="English", max_characters=280)

        source_at = prompt.index("Primary source text:")
        assert prompt.index("280 characters") > source_at

    def test_it_gives_a_word_count_too(self):
        """A model has no character counter; it can approximately track words."""
        prompt = build_prompt(a_story(), language="English", max_characters=280)

        assert f"{word_budget(280)} words" in prompt

    def test_it_says_what_going_over_costs(self):
        prompt = build_prompt(a_story(), language="English", max_characters=280)

        assert "cannot be posted" in prompt

    def test_the_limit_is_optional(self):
        """Channel-agnostic products have no character limit to state."""
        prompt = build_prompt(a_story(), language="English")

        assert "characters -- about" not in prompt

    def test_language_is_still_restated_last(self):
        """gen-5 must not displace the gen-3 fix it sits beside."""
        prompt = build_prompt(a_story(), language="Arabic", max_characters=280)

        assert prompt.index("Arabic") > prompt.index("Primary source text:")


class TestWordBudget:
    def test_the_budget_fits_inside_the_character_limit(self):
        """Overshooting the budget must still leave the post publishable."""
        for limit in (280, 500, 2200, 3000):
            assert word_budget(limit) * 6 <= limit

    def test_a_tight_limit_still_leaves_room_to_write(self):
        """Below a floor the budget stops being useful guidance."""
        assert word_budget(10) == 15

    def test_a_bigger_limit_allows_more_words(self):
        assert word_budget(3000) > word_budget(280)
