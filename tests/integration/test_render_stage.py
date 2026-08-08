"""Integration tests for the render stage.

The behaviours that matter are the ones a publisher would notice: the edition
lands under the right date, re-rendering is safe, and the folder is complete
enough to publish from without the database.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from engine.core.domain.content import ContentAsset, RevisionOrigin
from engine.core.domain.enums import StoryStatus
from engine.core.domain.ledger import RunLedger
from engine.core.domain.story import EditorialScore, EnrichedSource, Story
from engine.pipeline.runner import PipelineRunner
from engine.pipeline.stages.render import PACKAGE_NAME, POST_NAME, RenderStage

pytestmark = pytest.mark.integration

ARABIC = "يقدم MatrAIx بنية تحتية لتقييم أنظمة الذكاء الاصطناعي."


def store_selected_story(uow, workspace, clock, *, headline: str = "A story") -> Story:
    story = Story(
        workspace_id=workspace.id,
        canonical_headline=headline,
        primary_url="https://arxiv.org/abs/1",
        status=StoryStatus.SELECTED,
        first_seen_at=clock.now(),
        last_seen_at=clock.now(),
        selected_at=clock.now(),
    )
    story.enrichment = EnrichedSource(
        url="https://arxiv.org/abs/1", text="The abstract.", fetched_at=clock.now()
    )
    with uow as active:
        active.stories.add(story)
        active.scores.add_many(
            [
                EditorialScore(
                    workspace_id=workspace.id,
                    story_id=story.id,
                    run_id="run_seed",
                    total=0.8,
                    components={"recency": 0.8},
                    scored_at=clock.now(),
                )
            ]
        )
        active.commit()
    return story


def store_asset(uow, workspace, clock, story: Story, *, body: str, language: str = "en") -> None:
    asset = ContentAsset(
        workspace_id=workspace.id,
        story_id=story.id,
        run_id="run_seed",
        product_key="social_post",
        channel_key="linkedin",
        language=language,
        brand_profile_key=f"house_{language}",
        meta={"hook": "A hook.", "hashtags": ["#ai"]},
    )
    with uow as active:
        active.assets.add(asset)
        revision = asset.next_revision(
            body,
            origin=RevisionOrigin.GENERATED,
            actor_id="actor_system",
            prompt_version="gen-4",
            model="test-model",
            cost_usd=0.02,
        )
        active.assets.add_revision(revision)
        asset.current_revision_id = revision.id
        active.assets.update(asset)
        active.commit()


def render(uow, clock, settings, workspace_id, stage, *, dry_run: bool = False) -> RunLedger:
    runner = PipelineRunner(uow=uow, clock=clock, settings=settings)
    return runner.run(workspace_id=workspace_id, pipeline="render", stages=[stage], dry_run=dry_run)


def a_stage(output: Path, **overrides: Any) -> RenderStage:
    defaults: dict[str, Any] = {
        "output_dir": output,
        "workspace_slug": "default",
        "timezone": "UTC",
    }
    return RenderStage(**(defaults | overrides))


def package_of(output: Path, edition: str) -> dict[str, Any]:
    payload: dict[str, Any] = json.loads(
        (output / "default" / edition / PACKAGE_NAME).read_text(encoding="utf-8")
    )
    return payload


class TestEditionFolder:
    def test_it_writes_both_artifacts(self, uow, clock, settings, persisted_workspace, tmp_path):
        story = store_selected_story(uow, persisted_workspace, clock)
        store_asset(uow, persisted_workspace, clock, story, body="A post.")

        render(uow, clock, settings, persisted_workspace.id, a_stage(tmp_path))

        edition = clock.now().date().isoformat()
        directory = tmp_path / "default" / edition
        assert (directory / PACKAGE_NAME).is_file()
        assert (directory / POST_NAME).is_file()

    def test_the_edition_date_follows_the_workspace_timezone(
        self, uow, clock, settings, persisted_workspace, tmp_path
    ):
        """A late-evening batch in Asia/Riyadh belongs to that day, not the
        next one UTC has already started."""
        store_selected_story(uow, persisted_workspace, clock)

        render(
            uow,
            clock,
            settings,
            persisted_workspace.id,
            a_stage(tmp_path, timezone="Asia/Riyadh"),
        )

        expected = clock.now().astimezone(ZoneInfo("Asia/Riyadh")).date()
        assert (tmp_path / "default" / expected.isoformat()).is_dir()

    def test_an_unknown_timezone_still_produces_an_edition(
        self, uow, clock, settings, persisted_workspace, tmp_path
    ):
        """A typo in config must not stop the day going out."""
        store_selected_story(uow, persisted_workspace, clock)

        ledger = render(
            uow, clock, settings, persisted_workspace.id, a_stage(tmp_path, timezone="Mars/Olympus")
        )

        assert ledger.status.value == "succeeded"
        assert (tmp_path / "default" / clock.now().date().isoformat()).is_dir()

    def test_the_edition_date_can_be_overridden(
        self, uow, clock, settings, persisted_workspace, tmp_path
    ):
        store_selected_story(uow, persisted_workspace, clock)

        render(
            uow, clock, settings, persisted_workspace.id, a_stage(tmp_path, edition="2026-01-01")
        )

        assert (tmp_path / "default" / "2026-01-01").is_dir()

    def test_a_dry_run_writes_nothing(self, uow, clock, settings, persisted_workspace, tmp_path):
        store_selected_story(uow, persisted_workspace, clock)

        render(uow, clock, settings, persisted_workspace.id, a_stage(tmp_path), dry_run=True)

        assert not (tmp_path / "default").exists()


class TestContents:
    def test_the_package_is_publishable_without_the_database(
        self, uow, clock, settings, persisted_workspace, tmp_path
    ):
        """The whole point of the folder: after this, nothing downstream needs
        a connection."""
        story = store_selected_story(uow, persisted_workspace, clock, headline="Open weights")
        store_asset(uow, persisted_workspace, clock, story, body="The lab shipped weights.")

        render(
            uow, clock, settings, persisted_workspace.id, a_stage(tmp_path, edition="2026-08-08")
        )

        package = package_of(tmp_path, "2026-08-08")
        record = package["stories"][0]
        assert record["headline"] == "Open weights"
        assert record["primary_url"] == "https://arxiv.org/abs/1"
        assert record["score"]["total"] == pytest.approx(0.8)
        assert record["assets"][0]["body"] == "The lab shipped weights."
        assert package["cost_usd"] == pytest.approx(0.02)

    def test_arabic_posts_are_written_readably_not_escaped(
        self, uow, clock, settings, persisted_workspace, tmp_path
    ):
        """ensure_ascii would turn the whole post into \\uXXXX soup."""
        story = store_selected_story(uow, persisted_workspace, clock)
        store_asset(uow, persisted_workspace, clock, story, body=ARABIC, language="ar")

        render(
            uow, clock, settings, persisted_workspace.id, a_stage(tmp_path, edition="2026-08-08")
        )

        directory = tmp_path / "default" / "2026-08-08"
        assert ARABIC in (directory / PACKAGE_NAME).read_text(encoding="utf-8")
        assert 'dir="rtl"' in (directory / POST_NAME).read_text(encoding="utf-8")

    def test_a_thin_day_still_produces_a_readable_edition(
        self, uow, clock, settings, persisted_workspace, tmp_path
    ):
        ledger = render(
            uow, clock, settings, persisted_workspace.id, a_stage(tmp_path, edition="2026-08-08")
        )

        assert ledger.status.value == "succeeded"
        post = (tmp_path / "default" / "2026-08-08" / POST_NAME).read_text(encoding="utf-8")
        assert "Nothing was selected" in post


class TestRerendering:
    def test_re_rendering_replaces_rather_than_accumulates(
        self, uow, clock, settings, persisted_workspace, tmp_path
    ):
        """An edition is derived data -- keeping stale copies would create two
        answers to the same question."""
        story = store_selected_story(uow, persisted_workspace, clock)
        store_asset(uow, persisted_workspace, clock, story, body="A post.")
        stage = a_stage(tmp_path, edition="2026-08-08")

        render(uow, clock, settings, persisted_workspace.id, stage)
        render(uow, clock, settings, persisted_workspace.id, stage)

        directory = tmp_path / "default" / "2026-08-08"
        assert sorted(path.name for path in directory.iterdir()) == [PACKAGE_NAME, POST_NAME]

    def test_only_generated_at_moves_between_renders(
        self, uow, clock, settings, persisted_workspace, tmp_path
    ):
        """Everything else must be stable, or the edition cannot be diffed."""
        story = store_selected_story(uow, persisted_workspace, clock)
        store_asset(uow, persisted_workspace, clock, story, body="A post.")
        stage = a_stage(tmp_path, edition="2026-08-08")

        render(uow, clock, settings, persisted_workspace.id, stage)
        first = package_of(tmp_path, "2026-08-08")
        render(uow, clock, settings, persisted_workspace.id, stage)
        second = package_of(tmp_path, "2026-08-08")

        for payload in (first, second):
            payload.pop("generated_at")
            payload.pop("run_id")
        assert first == second


class TestWindow:
    def test_stories_selected_outside_the_window_are_excluded(
        self, uow, clock, settings, persisted_workspace, tmp_path
    ):
        """An edition is a day's output, not everything ever selected."""
        story = store_selected_story(uow, persisted_workspace, clock)
        with uow as active:
            story.selected_at = datetime(2020, 1, 1, tzinfo=UTC)
            active.stories.update(story)
            active.commit()

        render(
            uow, clock, settings, persisted_workspace.id, a_stage(tmp_path, edition="2026-08-08")
        )

        assert package_of(tmp_path, "2026-08-08")["counts"]["stories"] == 0
