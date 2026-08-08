"""Integration tests for the generate stage.

The behaviours worth pinning are the ones that cost money or lose work:
the matrix expanding to one asset per channel and language, regeneration
appending to the revision chain instead of duplicating a row, and refusals
being counted rather than papered over.
"""

from __future__ import annotations

from typing import Any

import pytest

from engine.adapters.generation.prompts import PROMPT_VERSION, GeneratedContent
from engine.core.domain.content import BrandProfile, RevisionOrigin
from engine.core.domain.enums import ActorKind, StoryStatus
from engine.core.domain.ledger import RunLedger
from engine.core.domain.pack import ChannelDef, DomainPack, ProductDef, ProductKind
from engine.core.domain.story import EnrichedSource, Story
from engine.pipeline.runner import PipelineRunner
from engine.pipeline.stages.generate import GenerateStage
from tests.fakes import FakeLLM

pytestmark = pytest.mark.integration

ARTICLE = "The laboratory released an open-weights model and published the training recipe."


def a_pack(*, channels: list[ChannelDef] | None = None) -> DomainPack:
    return DomainPack(
        key="test-pack",
        version=1,
        name="Test pack",
        products=[
            ProductDef(key="social_post", name="Social post", kind=ProductKind.POST),
            ProductDef(key="caption", name="Caption", kind=ProductKind.ATTACHMENT),
        ],
        channels=channels
        or [
            ChannelDef(
                key="linkedin",
                name="LinkedIn",
                max_characters=3000,
                max_hashtags=3,
                preferred_products=["social_post"],
            ),
            ChannelDef(
                key="x",
                name="X",
                max_characters=280,
                max_hashtags=2,
                preferred_products=["social_post"],
            ),
        ],
    )


def brands(workspace_id: str, *languages: str) -> list[BrandProfile]:
    return [
        BrandProfile(
            workspace_id=workspace_id,
            key=f"house_{language}",
            name=f"House {language}",
            language=language,
            audience="Engineers who already read AI news.",
        )
        for language in (languages or ("en",))
    ]


def answering(body: str = "A short, factual post about the release.") -> FakeLLM:
    return FakeLLM(
        value=GeneratedContent(
            body=body,
            hashtags=["ai"],
            source_url="https://example.test/model",
        )
    )


def store_selected_story(uow, workspace, clock, *, enriched: bool = True) -> Story:
    story = Story(
        workspace_id=workspace.id,
        canonical_headline="An open model was released",
        primary_url="https://example.test/model",
        status=StoryStatus.SELECTED,
        first_seen_at=clock.now(),
        last_seen_at=clock.now(),
        selected_at=clock.now(),
    )
    if enriched:
        story.enrichment = EnrichedSource(
            url=story.primary_url or "", text=ARTICLE, fetched_at=clock.now()
        )
    with uow as active:
        active.stories.add(story)
        active.commit()
    return story


def run_stage(
    uow, clock, settings, workspace_id: str, stage, *, dry_run: bool = False
) -> RunLedger:
    runner = PipelineRunner(uow=uow, clock=clock, settings=settings)
    return runner.run(
        workspace_id=workspace_id, pipeline="generate", stages=[stage], dry_run=dry_run
    )


def stage_meta(uow, workspace_id: str, ledger: RunLedger) -> dict[str, Any]:
    with uow as active:
        record = next(
            r for r in active.runs.list_stages(workspace_id, ledger.id) if r.stage == "generate"
        )
    meta: dict[str, Any] = record.meta
    return meta


def assets_of(uow, workspace_id: str):
    with uow as active:
        return active.assets.list_recent(workspace_id, limit=100)


class TestTheMatrix:
    def test_it_writes_one_asset_per_channel_and_language(
        self, uow, clock, settings, persisted_workspace
    ):
        """Two channels times two languages is four addressable assets."""
        store_selected_story(uow, persisted_workspace, clock)
        stage = GenerateStage(
            llm=answering(),
            pack=a_pack(),
            brand_profiles=brands(persisted_workspace.id, "en", "ar"),
            model="test-model",
        )

        run_stage(uow, clock, settings, persisted_workspace.id, stage)

        written = assets_of(uow, persisted_workspace.id)
        assert len(written) == 4
        assert {(a.channel_key, str(a.language)) for a in written} == {
            ("linkedin", "en"),
            ("linkedin", "ar"),
            ("x", "en"),
            ("x", "ar"),
        }

    def test_each_asset_records_the_voice_that_wrote_it(
        self, uow, clock, settings, persisted_workspace
    ):
        store_selected_story(uow, persisted_workspace, clock)
        stage = GenerateStage(
            llm=answering(),
            pack=a_pack(),
            brand_profiles=brands(persisted_workspace.id, "en", "ar"),
            model="test-model",
        )

        run_stage(uow, clock, settings, persisted_workspace.id, stage)

        by_language = {
            str(a.language): a.brand_profile_key for a in assets_of(uow, persisted_workspace.id)
        }
        assert by_language == {"en": "house_en", "ar": "house_ar"}

    def test_channels_can_be_restricted(self, uow, clock, settings, persisted_workspace):
        store_selected_story(uow, persisted_workspace, clock)
        stage = GenerateStage(
            llm=answering(),
            pack=a_pack(),
            brand_profiles=brands(persisted_workspace.id, "en"),
            model="test-model",
            channels=["x"],
        )

        run_stage(uow, clock, settings, persisted_workspace.id, stage)

        written = assets_of(uow, persisted_workspace.id)
        assert [a.channel_key for a in written] == ["x"]

    def test_the_channel_chooses_its_own_product(self, uow, clock, settings, persisted_workspace):
        """Instagram wants a caption where LinkedIn wants a post -- pack data,
        not a branch in the stage."""
        pack = a_pack(
            channels=[
                ChannelDef(
                    key="instagram",
                    name="Instagram",
                    max_characters=2200,
                    max_hashtags=12,
                    supports_links=False,
                    preferred_products=["caption"],
                )
            ]
        )
        store_selected_story(uow, persisted_workspace, clock)
        stage = GenerateStage(
            llm=answering(),
            pack=pack,
            brand_profiles=brands(persisted_workspace.id, "en"),
            model="test-model",
        )

        run_stage(uow, clock, settings, persisted_workspace.id, stage)

        assert [a.product_key for a in assets_of(uow, persisted_workspace.id)] == ["caption"]


class TestRevisions:
    def test_the_first_generation_creates_revision_one(
        self, uow, clock, settings, persisted_workspace
    ):
        store_selected_story(uow, persisted_workspace, clock)
        stage = GenerateStage(
            llm=answering(),
            pack=a_pack(channels=[a_pack().channels[0]]),
            brand_profiles=brands(persisted_workspace.id, "en"),
            model="test-model",
        )

        run_stage(uow, clock, settings, persisted_workspace.id, stage)

        asset = assets_of(uow, persisted_workspace.id)[0]
        with uow as active:
            revisions = active.assets.list_revisions(persisted_workspace.id, asset.id)
        assert len(revisions) == 1
        assert revisions[0].revision_number == 1
        assert revisions[0].origin is RevisionOrigin.GENERATED
        assert revisions[0].prompt_version == PROMPT_VERSION

    def test_the_author_is_the_real_system_actor(
        self, uow, clock, settings, persisted_workspace, system_actor
    ):
        """Attribution must resolve to an actor row, not to a plausible string.

        The edit-delta dataset is built by joining revisions to actors to tell a
        machine draft from a human rewrite. A hard-coded id looks correct in
        every test that only reads it back, and joins to nothing.
        """
        store_selected_story(uow, persisted_workspace, clock)
        stage = GenerateStage(
            llm=answering(),
            pack=a_pack(channels=[a_pack().channels[0]]),
            brand_profiles=brands(persisted_workspace.id, "en"),
            model="test-model",
        )

        run_stage(uow, clock, settings, persisted_workspace.id, stage)

        asset = assets_of(uow, persisted_workspace.id)[0]
        with uow as active:
            revision = active.assets.list_revisions(persisted_workspace.id, asset.id)[0]
            author = active.actors.get(system_actor.account_id, revision.actor_id)
        assert revision.actor_id == system_actor.id
        assert author is not None and author.kind is ActorKind.SYSTEM

    def test_regenerating_appends_rather_than_duplicating(
        self, uow, clock, settings, persisted_workspace
    ):
        """The slot is the identity: a second run must not create a second row."""
        store_selected_story(uow, persisted_workspace, clock)
        pack = a_pack(channels=[a_pack().channels[0]])
        voices = brands(persisted_workspace.id, "en")

        for body in ("First attempt.", "Second attempt, better."):
            run_stage(
                uow,
                clock,
                settings,
                persisted_workspace.id,
                GenerateStage(
                    llm=answering(body), pack=pack, brand_profiles=voices, model="test-model"
                ),
            )

        written = assets_of(uow, persisted_workspace.id)
        assert len(written) == 1
        with uow as active:
            revisions = active.assets.list_revisions(persisted_workspace.id, written[0].id)
            current = active.assets.get_revision(
                persisted_workspace.id, written[0].current_revision_id or ""
            )
        assert [r.revision_number for r in revisions] == [1, 2]
        assert revisions[1].parent_revision_id == revisions[0].id
        assert current is not None and current.body == "Second attempt, better."

    def test_the_earlier_revision_survives_regeneration(
        self, uow, clock, settings, persisted_workspace
    ):
        """The chain is the training signal; overwriting would discard it."""
        store_selected_story(uow, persisted_workspace, clock)
        pack = a_pack(channels=[a_pack().channels[0]])
        voices = brands(persisted_workspace.id, "en")

        for body in ("First attempt.", "Second attempt."):
            run_stage(
                uow,
                clock,
                settings,
                persisted_workspace.id,
                GenerateStage(
                    llm=answering(body), pack=pack, brand_profiles=voices, model="test-model"
                ),
            )

        asset = assets_of(uow, persisted_workspace.id)[0]
        with uow as active:
            bodies = [
                r.body for r in active.assets.list_revisions(persisted_workspace.id, asset.id)
            ]
        assert bodies == ["First attempt.", "Second attempt."]


class TestRefusals:
    def test_a_story_with_no_primary_source_is_refused_and_counted(
        self, uow, clock, settings, persisted_workspace
    ):
        store_selected_story(uow, persisted_workspace, clock, enriched=False)
        stage = GenerateStage(
            llm=answering(),
            pack=a_pack(channels=[a_pack().channels[0]]),
            brand_profiles=brands(persisted_workspace.id, "en"),
            model="test-model",
        )

        ledger = run_stage(uow, clock, settings, persisted_workspace.id, stage)

        assert stage_meta(uow, persisted_workspace.id, ledger)["failures"] == {
            "no_primary_source": 1
        }
        assert assets_of(uow, persisted_workspace.id) == []

    def test_output_past_the_channel_limit_is_refused_not_truncated(
        self, uow, clock, settings, persisted_workspace
    ):
        """A post cut off mid-word reads as broken; the fix is the prompt."""
        store_selected_story(uow, persisted_workspace, clock)
        stage = GenerateStage(
            llm=answering("x" * 500),
            pack=a_pack(channels=[a_pack().channels[1]]),  # X, 280 characters
            brand_profiles=brands(persisted_workspace.id, "en"),
            model="test-model",
        )

        ledger = run_stage(uow, clock, settings, persisted_workspace.id, stage)

        assert stage_meta(uow, persisted_workspace.id, ledger)["failures"] == {
            "over_channel_limit": 1
        }
        assert assets_of(uow, persisted_workspace.id) == []

    def test_a_model_failure_is_counted_rather_than_aborting_the_run(
        self, uow, clock, settings, persisted_workspace
    ):
        store_selected_story(uow, persisted_workspace, clock)
        stage = GenerateStage(
            llm=FakeLLM(fails_with="connection refused"),
            pack=a_pack(),
            brand_profiles=brands(persisted_workspace.id, "en"),
            model="test-model",
        )

        ledger = run_stage(uow, clock, settings, persisted_workspace.id, stage)

        assert stage_meta(uow, persisted_workspace.id, ledger)["failures"] == {
            "model_failed:llm_failed": 2
        }

    def test_provider_failures_are_counted_apart(self, uow, clock, settings, persisted_workspace):
        """A timeout and a schema violation need different fixes.

        Collapsed into one "model_failed: 4", an operator learns nothing
        actionable: raise the timeout, install the model, and lower the
        token ceiling are three different mornings.
        """
        store_selected_story(uow, persisted_workspace, clock)
        stage = GenerateStage(
            llm=FakeLLM(fails_with="timed out", fails_code="timeout"),
            pack=a_pack(),
            brand_profiles=brands(persisted_workspace.id, "en"),
            model="test-model",
        )

        ledger = run_stage(uow, clock, settings, persisted_workspace.id, stage)

        failures = stage_meta(uow, persisted_workspace.id, ledger)["failures"]
        assert failures == {"model_failed:timeout": 2}

    def test_an_unselected_story_is_not_written_for(
        self, uow, clock, settings, persisted_workspace
    ):
        """Generating by id must not route around the editorial gates."""
        story = store_selected_story(uow, persisted_workspace, clock)
        with uow as active:
            story.status = StoryStatus.ENRICHED
            story.selected_at = None
            active.stories.update(story)
            active.commit()

        stage = GenerateStage(
            llm=answering(),
            pack=a_pack(),
            brand_profiles=brands(persisted_workspace.id, "en"),
            model="test-model",
            only_story=story.id,
        )
        ledger = run_stage(uow, clock, settings, persisted_workspace.id, stage)

        assert stage_meta(uow, persisted_workspace.id, ledger).get("skip_reason")
        assert assets_of(uow, persisted_workspace.id) == []


class TestAccounting:
    def test_a_dry_run_writes_nothing(self, uow, clock, settings, persisted_workspace):
        store_selected_story(uow, persisted_workspace, clock)
        stage = GenerateStage(
            llm=answering(),
            pack=a_pack(),
            brand_profiles=brands(persisted_workspace.id, "en"),
            model="test-model",
        )

        run_stage(uow, clock, settings, persisted_workspace.id, stage, dry_run=True)

        assert assets_of(uow, persisted_workspace.id) == []

    def test_usage_reaches_the_run_ledger(self, uow, clock, settings, persisted_workspace):
        """Cost accounting is only complete if every call is counted."""
        store_selected_story(uow, persisted_workspace, clock)
        stage = GenerateStage(
            llm=answering(),
            pack=a_pack(),
            brand_profiles=brands(persisted_workspace.id, "en"),
            model="test-model",
        )

        ledger = run_stage(uow, clock, settings, persisted_workspace.id, stage)

        # Two channels, one language, one story: two calls at 100/20 each.
        assert ledger.usage.input_tokens == 200
        assert ledger.usage.output_tokens == 40

    def test_the_prompt_carries_the_cacheable_prefix(
        self, uow, clock, settings, persisted_workspace
    ):
        """Without it every story re-sends the whole brand profile at full price."""
        store_selected_story(uow, persisted_workspace, clock)
        llm = answering()
        stage = GenerateStage(
            llm=llm,
            pack=a_pack(channels=[a_pack().channels[0]]),
            brand_profiles=brands(persisted_workspace.id, "en"),
            model="test-model",
        )

        run_stage(uow, clock, settings, persisted_workspace.id, stage)

        request = llm.requests[0]
        assert request.cacheable_prefix
        assert "House en" in request.cacheable_prefix
        assert ARTICLE in request.prompt
        assert ARTICLE not in request.cacheable_prefix
