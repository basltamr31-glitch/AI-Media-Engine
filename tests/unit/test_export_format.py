"""Tests for channel export formatting.

What is under test is the promise the export makes: the file contains exactly
what goes in the box. Everything the channel forbids is left out, everything it
allows is included, and anything an operator must fix is named rather than
silently corrected.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from engine.adapters.rendering.export import export_filename, format_for_channel
from engine.core.domain.content import ContentAsset, ContentRevision, RevisionOrigin
from engine.core.domain.enums import StoryStatus
from engine.core.domain.pack import ChannelDef
from engine.core.domain.story import Story

NOW = datetime(2026, 8, 8, 12, 0, tzinfo=UTC)
BODY = "The lab published the training recipe, so small teams can reproduce the result."


def an_asset(
    *, channel: str | None = "linkedin", hashtags: list[str] | None = None
) -> ContentAsset:
    return ContentAsset(
        workspace_id="ws_test",
        story_id="sty_test",
        run_id="run_test",
        product_key="social_post",
        channel_key=channel,
        language="en",
        brand_profile_key="house_en",
        meta={"hashtags": hashtags if hashtags is not None else ["ai", "openweights"]},
    )


def a_revision(asset: ContentAsset, body: str = BODY) -> ContentRevision:
    return ContentRevision(
        workspace_id=asset.workspace_id,
        asset_id=asset.id,
        revision_number=1,
        body=body,
        origin=RevisionOrigin.GENERATED,
        actor_id="act_system",
        created_at=NOW,
    )


def a_channel(**overrides: object) -> ChannelDef:
    defaults: dict[str, object] = {
        "key": "linkedin",
        "name": "LinkedIn",
        "max_characters": 3000,
        "max_hashtags": 3,
        "supports_links": True,
    }
    return ChannelDef(**{**defaults, **overrides})


def a_story(url: str | None = "https://example.test/model") -> Story:
    return Story(
        workspace_id="ws_test",
        canonical_headline="An open model was released",
        primary_url=url,
        status=StoryStatus.SELECTED,
        first_seen_at=NOW,
        last_seen_at=NOW,
    )


class TestWhatEndsUpInTheFile:
    def test_body_hashtags_and_link_in_that_order(self):
        asset = an_asset()

        export = format_for_channel(asset, a_revision(asset), a_channel(), a_story())

        assert export.text == f"{BODY}\n\n#ai #openweights\n\nhttps://example.test/model"
        assert export.is_publishable

    def test_hashtags_are_written_once(self):
        """The model sometimes returns '#ai'; adding another '#' gives '##ai'."""
        asset = an_asset(hashtags=["#ai", "openweights"])

        export = format_for_channel(asset, a_revision(asset), a_channel(), a_story())

        assert "##" not in export.text
        assert "#ai" in export.text

    def test_a_channel_without_links_omits_the_url_and_says_so(self):
        asset = an_asset(channel="instagram")
        channel = a_channel(key="instagram", name="Instagram", supports_links=False)

        export = format_for_channel(asset, a_revision(asset), channel, a_story())

        assert "https://example.test/model" not in export.text
        assert any("does not support links" in warning for warning in export.warnings)

    def test_a_channel_without_hashtags_drops_them_and_says_so(self):
        channel = a_channel(supports_hashtags=False, max_hashtags=0)
        asset = an_asset()

        export = format_for_channel(asset, a_revision(asset), channel, a_story())

        assert "#" not in export.text
        assert any("does not use hashtags" in warning for warning in export.warnings)

    def test_a_story_with_no_url_simply_has_no_link(self):
        asset = an_asset()

        export = format_for_channel(asset, a_revision(asset), a_channel(), a_story(url=None))

        assert export.text.endswith("#ai #openweights")
        assert export.is_publishable

    def test_no_story_at_all_still_exports(self):
        asset = an_asset()

        export = format_for_channel(asset, a_revision(asset), a_channel(), None)

        assert BODY in export.text
        assert export.headline == ""


class TestLimitsAreReportedNotEnforced:
    def test_extra_hashtags_are_trimmed_and_counted(self):
        asset = an_asset(hashtags=["a", "b", "c", "d", "e"])

        export = format_for_channel(asset, a_revision(asset), a_channel(), a_story())

        assert "#a #b #c" in export.text
        assert "#d" not in export.text
        assert any("5 hashtags generated, 3 allowed" in w for w in export.warnings)

    def test_the_character_limit_counts_the_whole_assembled_post(self):
        """A body that fits before the URL is appended is not a post that fits."""
        channel = a_channel(max_characters=len(BODY) + 5)
        asset = an_asset()

        export = format_for_channel(asset, a_revision(asset), channel, a_story())

        assert any("allowed on LinkedIn" in warning for warning in export.warnings)
        assert not export.is_publishable

    def test_an_over_limit_post_is_still_written_in_full(self):
        """Truncating would end a sentence mid-word in front of an audience."""
        channel = a_channel(max_characters=20)
        asset = an_asset()

        export = format_for_channel(asset, a_revision(asset), channel, a_story())

        assert BODY in export.text
        assert not export.is_publishable


class TestDegradedInputs:
    def test_an_asset_with_no_revision_is_reported_not_skipped(self):
        asset = an_asset()

        export = format_for_channel(asset, None, a_channel(), a_story())

        assert any("no text" in warning for warning in export.warnings)

    def test_a_channel_missing_from_the_pack_exports_the_body_anyway(self):
        """The pack may have changed since the human approved the text."""
        asset = an_asset(channel="threads")

        export = format_for_channel(asset, a_revision(asset), None, a_story())

        assert BODY in export.text
        assert any("not in the active pack" in warning for warning in export.warnings)


class TestFilenames:
    def test_it_carries_channel_language_and_id(self):
        asset = an_asset()

        name = export_filename(asset)

        assert name.startswith("linkedin-en-")
        assert name.endswith(".txt")
        assert asset.id in name

    def test_two_assets_on_one_channel_and_language_do_not_collide(self):
        """Two stories produce two Arabic LinkedIn posts on the same day."""
        first, second = an_asset(), an_asset()

        assert export_filename(first) != export_filename(second)

    @pytest.mark.parametrize("channel", [None, ""])
    def test_a_channelless_asset_still_gets_a_name(self, channel: str | None):
        assert export_filename(an_asset(channel=channel)).startswith("nochannel-")
