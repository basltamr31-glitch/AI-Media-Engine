"""Rendering tests.

The package is what another program reads, so its shape and its determinism are
the contract. The Markdown is a view of it -- what matters there is that a
person can act on it, and that Arabic reads correctly.
"""

from __future__ import annotations

import json
import unicodedata
from datetime import UTC, datetime
from typing import Any

import pytest

from engine.adapters.rendering.markdown import is_rtl, render_body, render_markdown
from engine.adapters.rendering.package import SCHEMA_VERSION, build_package
from engine.core.domain.content import ContentAsset, ContentRevision, RevisionOrigin
from engine.core.domain.enums import EvidenceKind, StoryStatus
from engine.core.domain.story import EditorialScore, EnrichedSource, Evidence, Story

WHEN = datetime(2026, 8, 8, 6, 30, tzinfo=UTC)
ARABIC = "يقدم MatrAIx بنية تحتية لتقييم أنظمة الذكاء الاصطناعي."


def a_story(headline: str = "An open model was released") -> Story:
    story = Story(
        workspace_id="ws_1",
        canonical_headline=headline,
        primary_url="https://arxiv.org/abs/1",
        status=StoryStatus.SELECTED,
        selected_at=WHEN,
    )
    story.enrichment = EnrichedSource(
        url="https://arxiv.org/abs/1", title="A paper", text="The abstract in full."
    )
    return story


def an_asset(story: Story, *, language: str = "en", channel: str = "linkedin") -> ContentAsset:
    return ContentAsset(
        workspace_id="ws_1",
        story_id=story.id,
        run_id="run_1",
        product_key="social_post",
        channel_key=channel,
        language=language,
        brand_profile_key=f"house_{language}",
        meta={"hook": "A hook.", "hashtags": ["ai"]},
    )


def a_revision(asset: ContentAsset, body: str) -> ContentRevision:
    return asset.next_revision(
        body,
        origin=RevisionOrigin.GENERATED,
        actor_id="actor_system",
        prompt_version="gen-4",
        model="test-model",
        cost_usd=0.01,
    )


def a_score(story: Story, total: float = 0.7) -> EditorialScore:
    return EditorialScore(
        workspace_id="ws_1",
        story_id=story.id,
        run_id="run_1",
        total=total,
        components={"recency": 0.2, "corroboration": 0.3},
        scored_at=WHEN,
    )


#: One story's worth of input to the package builder.
Entry = tuple[
    Story,
    EditorialScore | None,
    list[Evidence],
    list[tuple[ContentAsset, ContentRevision | None]],
]


def a_package(*entries: Entry, edition: str = "2026-08-08") -> dict[str, Any]:
    return build_package(
        workspace_slug="default",
        edition_date=edition,
        generated_at=WHEN,
        run_id="run_1",
        engine_version="0.1.0",
        stories=list(entries),
    )


def one_story_package(
    *, body: str = "A post.", language: str = "en", score: float = 0.7
) -> dict[str, Any]:
    story = a_story()
    asset = an_asset(story, language=language)
    return a_package((story, a_score(story, score), [], [(asset, a_revision(asset, body))]))


class TestPackage:
    def test_it_declares_its_schema_version_for_consumers(self):
        assert one_story_package()["schema_version"] == SCHEMA_VERSION

    def test_it_carries_the_post_text(self):
        package = one_story_package(body="The lab shipped open weights.")

        asset = package["stories"][0]["assets"][0]
        assert asset["body"] == "The lab shipped open weights."
        assert asset["characters"] == len("The lab shipped open weights.")

    def test_it_carries_the_score_that_selected_the_story(self):
        """A package that cannot explain its own running order is not canonical."""
        score = one_story_package()["stories"][0]["score"]

        assert score["total"] == pytest.approx(0.7)
        assert score["components"] == {"corroboration": 0.3, "recency": 0.2}

    def test_it_carries_provenance_without_the_source_text(self):
        """The URL is what a reader follows; the extracted article is somebody
        else's copyright and has no business being redistributed."""
        story = a_story()
        evidence = [
            Evidence(
                workspace_id="ws_1",
                story_id=story.id,
                claim="The model was released.",
                kind=EvidenceKind.PRIMARY,
                source_url="https://arxiv.org/abs/1",
            )
        ]
        package = a_package((story, None, evidence, []))

        record = package["stories"][0]
        assert record["evidence"][0]["source_url"] == "https://arxiv.org/abs/1"
        assert record["source"]["characters_extracted"] == len("The abstract in full.")
        assert "The abstract in full." not in json.dumps(package)

    def test_stories_are_ordered_best_first(self):
        weak, strong = a_story("Weak"), a_story("Strong")
        package = a_package(
            (weak, a_score(weak, 0.4), [], []), (strong, a_score(strong, 0.9), [], [])
        )

        assert [item["headline"] for item in package["stories"]] == ["Strong", "Weak"]

    def test_it_is_byte_identical_across_renders(self):
        """An edition that churns on re-render cannot be diffed, and a diff is
        how "what changed?" gets answered.

        The same *inputs* twice, not two equivalent ones: ids and timestamps
        are inputs here, and re-generating them would be testing the fixture.
        """
        story = a_story()
        asset = an_asset(story)
        entry: Entry = (story, a_score(story), [], [(asset, a_revision(asset, "A post."))])

        first = json.dumps(a_package(entry), sort_keys=True)
        second = json.dumps(a_package(entry), sort_keys=True)

        assert first == second

    def test_reordering_the_input_does_not_reorder_the_output(self):
        """Determinism has to survive the database returning rows differently."""
        one, two = a_story("Alpha"), a_story("Beta")
        entries: list[Entry] = [
            (one, a_score(one, 0.5), [], []),
            (two, a_score(two, 0.5), [], []),
        ]

        forward = a_package(*entries)
        backward = a_package(*reversed(entries))

        assert [item["headline"] for item in forward["stories"]] == [
            item["headline"] for item in backward["stories"]
        ]

    def test_it_totals_the_cost(self):
        assert one_story_package()["cost_usd"] == pytest.approx(0.01)

    def test_hashtags_lose_a_leading_hash(self):
        """The model supplies '#ai' sometimes; renderers add their own, and the
        result was '##ai'."""
        story = a_story()
        asset = an_asset(story)
        asset.meta["hashtags"] = ["#ai", "eval", "#ai"]
        package = a_package((story, None, [], [(asset, a_revision(asset, "A post."))]))

        assert package["stories"][0]["assets"][0]["hashtags"] == ["ai", "eval"]

    def test_an_empty_day_is_still_a_valid_package(self):
        package = a_package()

        assert package["counts"] == {"stories": 0, "assets": 0}
        assert package["stories"] == []


class TestDirection:
    def test_arabic_is_right_to_left(self):
        assert is_rtl("ar")
        assert is_rtl("ar-SA")

    def test_english_is_not(self):
        assert not is_rtl("en")
        assert not is_rtl("en-GB")

    def test_direction_follows_the_language_not_the_characters(self):
        """A release note thick with Latin product names is still Arabic, and
        sniffing the text would lay it out backwards."""
        rendered = render_body("MatrAIx 8.3B", language="ar")

        assert 'dir="rtl"' in rendered


def auto_paragraph_direction(text: str) -> str:
    """The direction a renderer infers, per Unicode UAX#9 rules P2/P3.

    P2 finds the first strong character; P3 makes the paragraph RTL if that
    character is Arabic or Hebrew and LTR otherwise. Every Markdown renderer
    does this, because Markdown gives it nothing better to go on.
    """
    for character in text:
        bidi = unicodedata.bidirectional(character)
        if bidi in {"AL", "R"}:
            return "rtl"
        if bidi == "L":
            return "ltr"
    return "ltr"


class TestBidiInference:
    """Why `dir="rtl"` is load-bearing rather than decorative."""

    def test_an_arabic_sentence_opening_in_latin_is_inferred_backwards(self):
        """The failure this design exists to prevent. A product name at the
        start is enough to lay the whole paragraph out left-to-right, which
        puts the full stop on the wrong side -- and looks fine to a reader who
        does not read Arabic, so it ships."""
        latin_first = "MatrAIx يُقدم 1,010 مهام تطبيقية تشمل أكثر من 25 مجالًا."

        assert auto_paragraph_direction(latin_first) == "ltr"

    def test_stating_the_direction_does_not_depend_on_the_first_word(self):
        """Both orderings emit the same explicit base direction, so the layout
        no longer depends on which word the model happened to start with."""
        latin_first = "MatrAIx يُقدم مهام تطبيقية."
        arabic_first = "يُقدم MatrAIx مهام تطبيقية."

        assert 'dir="rtl"' in render_body(latin_first, language="ar")
        assert 'dir="rtl"' in render_body(arabic_first, language="ar")
        assert auto_paragraph_direction(latin_first) != auto_paragraph_direction(arabic_first)


class TestRenderBody:
    def test_english_is_left_as_markdown(self):
        """Wrapping it in HTML would stop emphasis and links rendering."""
        assert render_body("A **bold** post.", language="en") == "A **bold** post."

    def test_arabic_states_its_direction_explicitly(self):
        """Renderers guess from the first strong character, and guess wrong on
        a paragraph opening with a Latin product name."""
        rendered = render_body(ARABIC, language="ar")

        assert rendered.startswith('<p dir="rtl" align="right">')
        assert rendered.endswith("</p>")
        assert "MatrAIx" in rendered

    def test_each_arabic_paragraph_is_wrapped(self):
        rendered = render_body(f"{ARABIC}\n\n{ARABIC}", language="ar")

        assert rendered.count('<p dir="rtl"') == 2

    def test_html_in_an_arabic_post_is_escaped(self):
        """Unescaped, a stray '<' swallows the rest of the paragraph."""
        rendered = render_body("<script>x</script> نص", language="ar")

        assert "<script>" not in rendered
        assert "&lt;script&gt;" in rendered

    def test_an_empty_body_is_marked_rather_than_blank(self):
        assert render_body("   ", language="en") == "_(empty)_"


class TestMarkdown:
    def test_it_leads_with_the_edition(self):
        rendered = render_markdown(one_story_package())

        assert rendered.startswith("# default — 2026-08-08")

    def test_it_shows_the_post_and_its_provenance(self):
        rendered = render_markdown(one_story_package(body="The lab shipped weights."))

        assert "The lab shipped weights." in rendered
        assert "https://arxiv.org/abs/1" in rendered
        assert "**Score** 0.7000" in rendered

    def test_a_selected_story_with_no_post_says_so(self):
        """Otherwise the section reads as a broken renderer."""
        story = a_story()
        rendered = render_markdown(a_package((story, a_score(story), [], [])))

        assert "nothing written yet" in rendered
        assert "engine generate run" in rendered

    def test_an_empty_edition_says_so(self):
        """A thin day is a legitimate outcome, not a failed run."""
        rendered = render_markdown(a_package())

        assert "Nothing was selected" in rendered

    def test_arabic_posts_carry_their_direction(self):
        rendered = render_markdown(one_story_package(body=ARABIC, language="ar"))

        assert 'dir="rtl"' in rendered

    def test_it_ends_with_exactly_one_newline(self):
        """A file without one is a diff hunk on every edit."""
        rendered = render_markdown(one_story_package())

        assert rendered.endswith("\n")
        assert not rendered.endswith("\n\n")
