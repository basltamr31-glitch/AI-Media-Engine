"""Persistence tests for publications and performance metrics.

Both tables are append-only and both are joined through by the feedback loop,
so the properties under test are the ones that would corrupt the dataset
silently: workspace scoping, the natural keys that stop double-counting, and
the read model collapsing a series of readings to the right one.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.exc import IntegrityError

from engine.adapters.persistence.unit_of_work import SqlAlchemyUnitOfWork
from engine.core.domain.content import (
    ContentAsset,
    PerformanceMetric,
    Publication,
    RevisionOrigin,
)
from engine.core.domain.enums import StoryStatus
from engine.core.domain.story import Story
from engine.core.domain.workspace import Workspace

pytestmark = pytest.mark.integration

NOW = datetime(2026, 8, 8, 12, 0, tzinfo=UTC)


@pytest.fixture
def asset(uow: SqlAlchemyUnitOfWork, persisted_workspace: Workspace, system_actor) -> ContentAsset:
    story = Story(
        workspace_id=persisted_workspace.id,
        canonical_headline="An open model was released",
        primary_url="https://example.test/model",
        status=StoryStatus.SELECTED,
        first_seen_at=NOW,
        last_seen_at=NOW,
    )
    record = ContentAsset(
        workspace_id=persisted_workspace.id,
        story_id=story.id,
        run_id="run_test",
        product_key="social_post",
        channel_key="linkedin",
        language="en",
        brand_profile_key="house_en",
    )
    revision = record.next_revision(
        "A post.", origin=RevisionOrigin.GENERATED, actor_id=system_actor.id
    )
    record.current_revision_id = revision.id
    with uow as active:
        active.stories.add(story)
        active.assets.add(record)
        active.assets.add_revision(revision)
        active.commit()
    return record


def a_publication(workspace_id: str, asset_id: str, **overrides) -> Publication:
    defaults = {
        "workspace_id": workspace_id,
        "asset_id": asset_id,
        "channel_key": "linkedin",
        "external_url": "https://linkedin.test/posts/1",
        "published_at": NOW,
    }
    return Publication(**{**defaults, **overrides})


class TestPublicationRepository:
    def test_it_round_trips(self, uow, persisted_workspace, asset):
        publication = a_publication(persisted_workspace.id, asset.id, external_id="urn:1")

        with uow as active:
            active.publications.add(publication)
            active.commit()

        with uow as active:
            stored = active.publications.get(persisted_workspace.id, publication.id)
        assert stored is not None
        assert stored.external_url == "https://linkedin.test/posts/1"
        assert stored.external_id == "urn:1"
        assert stored.published_at == NOW
        assert stored.published_by == "manual"

    def test_lookup_is_workspace_scoped(self, uow, persisted_workspace, asset):
        publication = a_publication(persisted_workspace.id, asset.id)
        with uow as active:
            active.publications.add(publication)
            active.commit()

        with uow as active:
            assert active.publications.get("ws_other", publication.id) is None

    def test_recent_is_newest_first(self, uow, persisted_workspace, asset):
        older = a_publication(
            persisted_workspace.id, asset.id, channel_key="x", published_at=NOW - timedelta(days=2)
        )
        newer = a_publication(persisted_workspace.id, asset.id, channel_key="linkedin")

        with uow as active:
            active.publications.add(older)
            active.publications.add(newer)
            active.commit()

        with uow as active:
            recent = active.publications.list_recent(persisted_workspace.id)
        assert [record.id for record in recent] == [newer.id, older.id]

    def test_one_publication_per_asset_and_channel(self, uow, persisted_workspace, asset):
        """Recording twice would double every metric that joins through it."""
        with uow as active:
            active.publications.add(a_publication(persisted_workspace.id, asset.id))
            active.commit()

        with pytest.raises(IntegrityError), uow as active:
            active.publications.add(a_publication(persisted_workspace.id, asset.id))
            active.commit()

    def test_the_same_asset_may_go_live_on_two_channels(self, uow, persisted_workspace, asset):
        with uow as active:
            active.publications.add(a_publication(persisted_workspace.id, asset.id))
            active.publications.add(
                a_publication(persisted_workspace.id, asset.id, channel_key="x")
            )
            active.commit()

        with uow as active:
            assert len(active.publications.list_for_asset(persisted_workspace.id, asset.id)) == 2

    def test_counting(self, uow, persisted_workspace, asset):
        with uow as active:
            assert active.publications.count_for_workspace(persisted_workspace.id) == 0
            active.publications.add(a_publication(persisted_workspace.id, asset.id))
            active.commit()

        with uow as active:
            assert active.publications.count_for_workspace(persisted_workspace.id) == 1


class TestPerformanceMetricRepository:
    @pytest.fixture
    def publication(self, uow, persisted_workspace, asset) -> Publication:
        record = a_publication(persisted_workspace.id, asset.id)
        with uow as active:
            active.publications.add(record)
            active.commit()
        return record

    def test_it_round_trips(self, uow, persisted_workspace, publication):
        metric = PerformanceMetric(
            workspace_id=persisted_workspace.id,
            publication_id=publication.id,
            metric="Impressions",
            value=4210,
            captured_at=NOW,
        )

        with uow as active:
            active.metrics.add(metric)
            active.commit()

        with uow as active:
            stored = active.metrics.list_for_publication(persisted_workspace.id, publication.id)
        assert len(stored) == 1
        # The domain normalises the name; storage must not undo it.
        assert stored[0].metric == "impressions"
        assert stored[0].value == 4210.0
        assert stored[0].collected_by == "manual"

    def test_readings_are_returned_oldest_first(self, uow, persisted_workspace, publication):
        for offset, value in ((2, 87), (1, 103)):
            with uow as active:
                active.metrics.add(
                    PerformanceMetric(
                        workspace_id=persisted_workspace.id,
                        publication_id=publication.id,
                        metric="likes",
                        value=value,
                        captured_at=NOW - timedelta(days=offset),
                    )
                )
                active.commit()

        with uow as active:
            stored = active.metrics.list_for_publication(persisted_workspace.id, publication.id)
        assert [record.value for record in stored] == [87.0, 103.0]

    def test_one_reading_per_metric_per_moment(self, uow, persisted_workspace, publication):
        """The natural key of an observation. The same instant twice is a typo."""

        def reading() -> PerformanceMetric:
            return PerformanceMetric(
                workspace_id=persisted_workspace.id,
                publication_id=publication.id,
                metric="likes",
                value=12,
                captured_at=NOW,
            )

        with uow as active:
            active.metrics.add(reading())
            active.commit()

        with pytest.raises(IntegrityError), uow as active:
            active.metrics.add(reading())
            active.commit()


class TestThePerformanceReadModel:
    @pytest.fixture
    def publication(self, uow, persisted_workspace, asset) -> Publication:
        record = a_publication(persisted_workspace.id, asset.id)
        with uow as active:
            active.publications.add(record)
            active.commit()
        return record

    def add_reading(
        self,
        uow: SqlAlchemyUnitOfWork,
        workspace_id: str,
        publication_id: str,
        metric: str,
        value: float,
        when: datetime,
    ) -> None:
        with uow as active:
            active.metrics.add(
                PerformanceMetric(
                    workspace_id=workspace_id,
                    publication_id=publication_id,
                    metric=metric,
                    value=value,
                    captured_at=when,
                )
            )
            active.commit()

    def test_a_metric_collapses_to_its_latest_reading(self, uow, persisted_workspace, publication):
        """'How is this post doing' is a question about now."""
        self.add_reading(
            uow, persisted_workspace.id, publication.id, "likes", 87, NOW - timedelta(days=2)
        )
        self.add_reading(
            uow, persisted_workspace.id, publication.id, "likes", 103, NOW - timedelta(days=1)
        )

        with uow as active:
            rows = active.metrics.performance(persisted_workspace.id)

        assert rows[0].value_of("likes") == 103.0
        # Both readings survive in the log; only the view collapses them.
        assert rows[0].observations == 2

    def test_out_of_order_insertion_still_picks_the_latest(
        self, uow, persisted_workspace, publication
    ):
        """Rows arrive in entry order, not capture order."""
        self.add_reading(
            uow, persisted_workspace.id, publication.id, "likes", 103, NOW - timedelta(days=1)
        )
        self.add_reading(
            uow, persisted_workspace.id, publication.id, "likes", 87, NOW - timedelta(days=2)
        )

        with uow as active:
            rows = active.metrics.performance(persisted_workspace.id)

        assert rows[0].value_of("likes") == 103.0
        assert rows[0].last_captured_at == NOW - timedelta(days=1)

    def test_several_metrics_appear_side_by_side(self, uow, persisted_workspace, publication):
        self.add_reading(uow, persisted_workspace.id, publication.id, "impressions", 4210, NOW)
        self.add_reading(uow, persisted_workspace.id, publication.id, "likes", 87, NOW)

        with uow as active:
            rows = active.metrics.performance(persisted_workspace.id)

        assert rows[0].metrics == {"impressions": 4210.0, "likes": 87.0}

    def test_an_unmeasured_publication_still_appears(self, uow, persisted_workspace, publication):
        """Knowing what has *not* been measured is the point of the report."""
        with uow as active:
            rows = active.metrics.performance(persisted_workspace.id)

        assert len(rows) == 1
        assert not rows[0].has_data
        assert rows[0].value_of("likes") == 0.0
        assert rows[0].last_captured_at is None

    def test_no_publications_at_all(self, uow, persisted_workspace):
        with uow as active:
            assert active.metrics.performance(persisted_workspace.id) == []

    def test_it_is_workspace_scoped(self, uow, persisted_workspace, publication):
        self.add_reading(uow, persisted_workspace.id, publication.id, "likes", 87, NOW)

        with uow as active:
            assert active.metrics.performance("ws_other") == []

    def test_publications_come_back_newest_first(self, uow, persisted_workspace, asset):
        older = a_publication(
            persisted_workspace.id, asset.id, channel_key="x", published_at=NOW - timedelta(days=3)
        )
        newer = a_publication(persisted_workspace.id, asset.id, channel_key="linkedin")
        with uow as active:
            active.publications.add(older)
            active.publications.add(newer)
            active.commit()

        with uow as active:
            rows = active.metrics.performance(persisted_workspace.id)

        assert [row.publication_id for row in rows] == [newer.id, older.id]
