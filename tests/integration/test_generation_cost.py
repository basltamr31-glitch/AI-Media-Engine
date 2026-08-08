"""Cost accounting for generation.

Two claims are under test, and both are commercial rather than technical:
what one post costs must be answerable per asset and per attempt, and the spend
ceiling must bite at the point money is spent rather than after the fact.
"""

from __future__ import annotations

import pytest

from engine.adapters.generation.prompts import GeneratedContent
from engine.core.domain.content import BrandProfile
from engine.core.domain.enums import StoryStatus
from engine.core.domain.ledger import RunLedger, TokenUsage
from engine.core.domain.pack import ChannelDef, DomainPack, ProductDef, ProductKind
from engine.core.domain.story import EnrichedSource, Story
from engine.pipeline.runner import PipelineRunner
from engine.pipeline.stages.generate import GenerateStage
from tests.fakes import FakeLLM

pytestmark = pytest.mark.integration

ARTICLE = "The laboratory released an open-weights model and published the training recipe."


def a_pack(count: int = 2) -> DomainPack:
    channels = [
        ChannelDef(
            key=key,
            name=key.title(),
            max_characters=3000,
            max_hashtags=3,
            preferred_products=["social_post"],
        )
        for key in ("linkedin", "x", "instagram")[:count]
    ]
    return DomainPack(
        key="test-pack",
        version=1,
        name="Test pack",
        products=[ProductDef(key="social_post", name="Social post", kind=ProductKind.POST)],
        channels=channels,
    )


def a_brand(workspace_id: str) -> list[BrandProfile]:
    return [
        BrandProfile(
            workspace_id=workspace_id,
            key="house_en",
            name="House",
            language="en",
            audience="Engineers.",
        )
    ]


def paying(cost_usd: float) -> FakeLLM:
    return FakeLLM(
        value=GeneratedContent(body="A short post.", hashtags=[], source_url="https://x.test"),
        usage=TokenUsage(input_tokens=1000, output_tokens=200),
        cost_usd=cost_usd,
    )


def store_story(uow, workspace, clock, headline: str = "A story") -> Story:
    story = Story(
        workspace_id=workspace.id,
        canonical_headline=headline,
        primary_url=f"https://example.test/{headline.replace(' ', '-')}",
        status=StoryStatus.SELECTED,
        first_seen_at=clock.now(),
        last_seen_at=clock.now(),
        selected_at=clock.now(),
    )
    story.enrichment = EnrichedSource(
        url=story.primary_url or "", text=ARTICLE, fetched_at=clock.now()
    )
    with uow as active:
        active.stories.add(story)
        active.commit()
    return story


def generate(uow, clock, settings, workspace_id, **kwargs) -> RunLedger:
    stage = GenerateStage(model="test-model", **kwargs)
    runner = PipelineRunner(uow=uow, clock=clock, settings=settings)
    return runner.run(workspace_id=workspace_id, pipeline="generate", stages=[stage])


class TestPerRevisionCost:
    def test_each_revision_records_what_it_cost(self, uow, clock, settings, persisted_workspace):
        store_story(uow, persisted_workspace, clock)

        generate(
            uow,
            clock,
            settings,
            persisted_workspace.id,
            llm=paying(0.05),
            pack=a_pack(1),
            brand_profiles=a_brand(persisted_workspace.id),
        )

        with uow as active:
            asset = active.assets.list_recent(persisted_workspace.id, limit=1)[0]
            revision = active.assets.latest_revision(persisted_workspace.id, asset.id)
        assert revision is not None
        assert revision.cost_usd == pytest.approx(0.05)
        assert revision.usage.input_tokens == 1000
        assert revision.model == "test-model"

    def test_regenerating_spends_again_and_both_attempts_are_kept(
        self, uow, clock, settings, persisted_workspace
    ):
        """A per-asset total that hid the retries is the number that makes a
        pricing decision wrong."""
        store_story(uow, persisted_workspace, clock)
        for _ in range(2):
            generate(
                uow,
                clock,
                settings,
                persisted_workspace.id,
                llm=paying(0.05),
                pack=a_pack(1),
                brand_profiles=a_brand(persisted_workspace.id),
            )

        with uow as active:
            spend = active.assets.spend_by_asset(persisted_workspace.id)
        assert len(spend) == 1
        assert spend[0].revisions == 2
        assert spend[0].cost_usd == pytest.approx(0.10)
        assert spend[0].cost_per_revision_usd == pytest.approx(0.05)

    def test_the_workspace_total_sums_every_generation(
        self, uow, clock, settings, persisted_workspace
    ):
        store_story(uow, persisted_workspace, clock, "One")
        store_story(uow, persisted_workspace, clock, "Two")

        generate(
            uow,
            clock,
            settings,
            persisted_workspace.id,
            llm=paying(0.01),
            pack=a_pack(2),
            brand_profiles=a_brand(persisted_workspace.id),
        )

        with uow as active:
            total = active.assets.total_spend(persisted_workspace.id)
        # Two stories x two channels x one language.
        assert total.revisions == 4
        assert total.cost_usd == pytest.approx(0.04)

    def test_a_free_provider_reports_zero_rather_than_nothing(
        self, uow, clock, settings, persisted_workspace
    ):
        """Zero is the true number for local inference, not missing data."""
        store_story(uow, persisted_workspace, clock)

        generate(
            uow,
            clock,
            settings,
            persisted_workspace.id,
            llm=paying(0.0),
            pack=a_pack(1),
            brand_profiles=a_brand(persisted_workspace.id),
        )

        with uow as active:
            total = active.assets.total_spend(persisted_workspace.id)
        assert total.revisions == 1
        assert total.cost_usd == 0.0
        assert total.output_tokens == 200


class TestCeiling:
    def test_it_stops_spending_once_the_ceiling_is_reached(
        self, uow, clock, settings, persisted_workspace
    ):
        """The runner only checks between stages, so a wide matrix could spend
        the budget several times over before anyone looked."""
        store_story(uow, persisted_workspace, clock, "One")
        store_story(uow, persisted_workspace, clock, "Two")

        ledger = generate(
            uow,
            clock,
            settings,
            persisted_workspace.id,
            llm=paying(1.0),
            pack=a_pack(3),  # six calls if unchecked
            brand_profiles=a_brand(persisted_workspace.id),
            max_cost_usd=2.0,
        )

        with uow as active:
            record = next(
                r
                for r in active.runs.list_stages(persisted_workspace.id, ledger.id)
                if r.stage == "generate"
            )
            total = active.assets.total_spend(persisted_workspace.id)
        assert record.meta["halted"]
        assert total.cost_usd == pytest.approx(2.0)

    def test_work_already_paid_for_is_kept(self, uow, clock, settings, persisted_workspace):
        """Discarding committed generations would waste the money that bought them."""
        store_story(uow, persisted_workspace, clock)

        generate(
            uow,
            clock,
            settings,
            persisted_workspace.id,
            llm=paying(1.0),
            pack=a_pack(3),
            brand_profiles=a_brand(persisted_workspace.id),
            max_cost_usd=2.0,
        )

        with uow as active:
            assert len(active.assets.list_recent(persisted_workspace.id, limit=10)) == 2

    def test_no_ceiling_generates_the_whole_matrix(self, uow, clock, settings, persisted_workspace):
        store_story(uow, persisted_workspace, clock)

        generate(
            uow,
            clock,
            settings,
            persisted_workspace.id,
            llm=paying(1.0),
            pack=a_pack(3),
            brand_profiles=a_brand(persisted_workspace.id),
            max_cost_usd=None,
        )

        with uow as active:
            assert len(active.assets.list_recent(persisted_workspace.id, limit=10)) == 3

    def test_a_free_provider_never_trips_the_ceiling(
        self, uow, clock, settings, persisted_workspace
    ):
        store_story(uow, persisted_workspace, clock)

        ledger = generate(
            uow,
            clock,
            settings,
            persisted_workspace.id,
            llm=paying(0.0),
            pack=a_pack(3),
            brand_profiles=a_brand(persisted_workspace.id),
            max_cost_usd=0.01,
        )

        with uow as active:
            record = next(
                r
                for r in active.runs.list_stages(persisted_workspace.id, ledger.id)
                if r.stage == "generate"
            )
        assert record.meta["halted"] is None
        assert record.items_out == 3
