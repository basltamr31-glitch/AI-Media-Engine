"""Integration tests for the content lifecycle after generation.

The behaviours worth pinning are the ones that lose something irrecoverable if
they regress: the review gate being unskippable, the human rewrite surviving
next to the model's draft, publication being recorded once, and every action
leaving a signal that attributes to a real actor.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from engine.cli import lifecycle
from engine.cli.lifecycle import LifecycleError
from engine.core.domain.account import OWNER_ACTOR_KEY, SYSTEM_ACTOR_KEY, Actor
from engine.core.domain.content import ContentAsset, RevisionOrigin
from engine.core.domain.enums import ActorKind, ContentState, SignalPolarity, StoryStatus
from engine.core.domain.signal import (
    SIGNAL_CONTENT_APPROVED,
    SIGNAL_CONTENT_EDITED,
    SIGNAL_CONTENT_PUBLISHED,
    SIGNAL_CONTENT_REJECTED,
    SIGNAL_ENGAGEMENT,
)
from engine.core.domain.story import Story

pytestmark = pytest.mark.integration

DRAFT_BODY = "The lab published the training recipe, so small teams can reproduce the result."


@pytest.fixture
def owner(uow, persisted_account) -> Actor:
    """The human whose decisions these are. Provisioned with the account."""
    with uow as active:
        actor: Actor | None = active.actors.get_by_key(persisted_account.id, OWNER_ACTOR_KEY)
    assert actor is not None, "provisioning should create the owner actor"
    return actor


@pytest.fixture
def story(uow, persisted_workspace, clock) -> Story:
    record = Story(
        workspace_id=persisted_workspace.id,
        canonical_headline="An open model was released",
        primary_url="https://example.test/model",
        status=StoryStatus.SELECTED,
        first_seen_at=clock.now(),
        last_seen_at=clock.now(),
    )
    with uow as active:
        active.stories.add(record)
        active.commit()
    return record


@pytest.fixture
def asset(uow, persisted_workspace, story, system_actor, clock) -> ContentAsset:
    """A generated draft, exactly as the generate stage would leave it."""
    record = ContentAsset(
        workspace_id=persisted_workspace.id,
        story_id=story.id,
        run_id="run_test",
        product_key="social_post",
        channel_key="linkedin",
        language="en",
        brand_profile_key="house_en",
        meta={"hashtags": ["ai"]},
    )
    revision = record.next_revision(
        DRAFT_BODY,
        origin=RevisionOrigin.GENERATED,
        actor_id=system_actor.id,
        prompt_version="gen-4",
        model="test-model",
    )
    record.current_revision_id = revision.id
    with uow as active:
        active.assets.add(record)
        active.assets.add_revision(revision)
        active.commit()
    return record


def signals_of(uow, workspace_id: str, signal_type: str):
    with uow as active:
        return active.signals.by_type(workspace_id, signal_type)


def state_of(uow, workspace_id: str, asset_id: str) -> ContentState:
    with uow as active:
        stored: ContentAsset | None = active.assets.get(workspace_id, asset_id)
    assert stored is not None
    return stored.state


class TestTheQueue:
    def test_it_shows_what_is_waiting(self, uow, persisted_workspace, asset):
        with uow as active:
            items = lifecycle.queue(active, persisted_workspace.id)

        assert [item.asset.id for item in items] == [asset.id]
        assert items[0].body == DRAFT_BODY
        assert items[0].headline == "An open model was released"

    def test_approved_content_leaves_the_queue(self, uow, persisted_workspace, asset, owner, clock):
        with uow as active:
            lifecycle.approve(active, persisted_workspace.id, asset.id, actor=owner, clock=clock)
            active.commit()

        with uow as active:
            assert lifecycle.queue(active, persisted_workspace.id) == []
            approved = lifecycle.queue(active, persisted_workspace.id, state=ContentState.APPROVED)
        assert [item.asset.id for item in approved] == [asset.id]

    def test_showing_a_missing_asset_is_a_user_error(self, uow, persisted_workspace):
        with uow as active, pytest.raises(LifecycleError, match="not found"):
            lifecycle.show(active, persisted_workspace.id, "ast_nonexistent")


class TestApproval:
    def test_a_draft_is_walked_through_review(self, uow, persisted_workspace, asset, owner, clock):
        """The gate cannot be jumped, only passed through."""
        with uow as active:
            lifecycle.approve(active, persisted_workspace.id, asset.id, actor=owner, clock=clock)
            active.commit()

        assert state_of(uow, persisted_workspace.id, asset.id) is ContentState.APPROVED

    def test_it_emits_a_positive_signal_attributed_to_the_human(
        self, uow, persisted_workspace, asset, owner, clock
    ):
        with uow as active:
            lifecycle.approve(active, persisted_workspace.id, asset.id, actor=owner, clock=clock)
            active.commit()

        signals = signals_of(uow, persisted_workspace.id, SIGNAL_CONTENT_APPROVED)
        assert len(signals) == 1
        assert signals[0].actor_id == owner.id
        assert signals[0].polarity is SignalPolarity.POSITIVE

    def test_the_signal_records_whether_the_model_or_a_human_wrote_it(
        self, uow, persisted_workspace, asset, owner, clock
    ):
        """Approving untouched output is the strongest evidence generation works."""
        with uow as active:
            lifecycle.approve(active, persisted_workspace.id, asset.id, actor=owner, clock=clock)
            active.commit()

        signal = signals_of(uow, persisted_workspace.id, SIGNAL_CONTENT_APPROVED)[0]
        assert signal.context["revision_origin"] == RevisionOrigin.GENERATED.value

    def test_a_note_is_kept_on_the_asset_and_the_signal(
        self, uow, persisted_workspace, asset, owner, clock
    ):
        with uow as active:
            lifecycle.approve(
                active,
                persisted_workspace.id,
                asset.id,
                actor=owner,
                clock=clock,
                notes="good enough to ship",
            )
            active.commit()

        with uow as active:
            stored = active.assets.get(persisted_workspace.id, asset.id)
        assert stored is not None and stored.review_notes == "good enough to ship"
        assert (
            signals_of(uow, persisted_workspace.id, SIGNAL_CONTENT_APPROVED)[0].label
            == "good enough to ship"
        )

    def test_published_content_cannot_be_approved_again(
        self, uow, persisted_workspace, asset, owner, clock
    ):
        with uow as active:
            lifecycle.approve(active, persisted_workspace.id, asset.id, actor=owner, clock=clock)
            lifecycle.record_publication(
                active, persisted_workspace.id, asset.id, actor=owner, clock=clock
            )
            active.commit()

        with uow as active, pytest.raises(LifecycleError, match="cannot approve"):
            lifecycle.approve(active, persisted_workspace.id, asset.id, actor=owner, clock=clock)


class TestRejection:
    def test_it_records_the_reason(self, uow, persisted_workspace, asset, owner, clock):
        with uow as active:
            lifecycle.reject(
                active,
                persisted_workspace.id,
                asset.id,
                actor=owner,
                clock=clock,
                reason="reads like the abstract",
            )
            active.commit()

        assert state_of(uow, persisted_workspace.id, asset.id) is ContentState.REJECTED
        signal = signals_of(uow, persisted_workspace.id, SIGNAL_CONTENT_REJECTED)[0]
        assert signal.label == "reads like the abstract"
        assert signal.polarity is SignalPolarity.NEGATIVE

    @pytest.mark.parametrize("reason", ["", "   "])
    def test_a_reasonless_rejection_is_refused(
        self, uow, persisted_workspace, asset, owner, clock, reason: str
    ):
        """A rejection with no reason is the one feedback shape nothing can learn from."""
        with uow as active, pytest.raises(LifecycleError, match="needs a reason"):
            lifecycle.reject(
                active, persisted_workspace.id, asset.id, actor=owner, clock=clock, reason=reason
            )

    def test_the_draft_survives_rejection(self, uow, persisted_workspace, asset, owner, clock):
        """Rejected text is training data, not rubbish."""
        with uow as active:
            lifecycle.reject(
                active,
                persisted_workspace.id,
                asset.id,
                actor=owner,
                clock=clock,
                reason="too long",
            )
            active.commit()

        with uow as active:
            revisions = active.assets.list_revisions(persisted_workspace.id, asset.id)
        assert [revision.body for revision in revisions] == [DRAFT_BODY]


class TestEditing:
    def test_the_model_draft_survives_next_to_the_human_one(
        self, uow, persisted_workspace, asset, owner, clock
    ):
        """The pair is the asset. Overwriting would discard it permanently."""
        with uow as active:
            lifecycle.edit(
                active,
                persisted_workspace.id,
                asset.id,
                actor=owner,
                clock=clock,
                body="Small teams can now reproduce this. The recipe is public.",
            )
            active.commit()

        with uow as active:
            revisions = active.assets.list_revisions(persisted_workspace.id, asset.id)
        assert [revision.revision_number for revision in revisions] == [1, 2]
        assert revisions[0].body == DRAFT_BODY
        assert revisions[0].origin is RevisionOrigin.GENERATED
        assert revisions[1].origin is RevisionOrigin.EDITED

    def test_the_human_edit_attributes_to_the_human(
        self, uow, persisted_workspace, asset, owner, system_actor, clock
    ):
        with uow as active:
            outcome = lifecycle.edit(
                active,
                persisted_workspace.id,
                asset.id,
                actor=owner,
                clock=clock,
                body="A rewrite.",
            )
            active.commit()

        assert outcome.revision.actor_id == owner.id
        assert owner.kind is ActorKind.HUMAN
        assert owner.id != system_actor.id

    def test_the_delta_reaches_the_signal_log(self, uow, persisted_workspace, asset, owner, clock):
        with uow as active:
            lifecycle.edit(
                active,
                persisted_workspace.id,
                asset.id,
                actor=owner,
                clock=clock,
                body="Completely different words entirely, nothing shared.",
            )
            active.commit()

        signal = signals_of(uow, persisted_workspace.id, SIGNAL_CONTENT_EDITED)[0]
        assert signal.context["is_rewrite"] is True
        assert signal.context["prompt_version"] == "gen-4"
        assert signal.context["model"] == "test-model"
        # Value runs the same direction as every other cost: bigger is worse.
        assert signal.value is not None and signal.value > 0.5

    def test_a_tiny_correction_is_not_reported_as_a_rewrite(
        self, uow, persisted_workspace, asset, owner, clock
    ):
        with uow as active:
            outcome = lifecycle.edit(
                active,
                persisted_workspace.id,
                asset.id,
                actor=owner,
                clock=clock,
                body=DRAFT_BODY.replace("small teams", "small teams everywhere"),
            )
            active.commit()

        assert not outcome.delta.is_rewrite
        assert outcome.delta.characters_changed > 0

    def test_editing_approved_content_returns_it_to_draft(
        self, uow, persisted_workspace, asset, owner, clock
    ):
        """An approval refers to the text that was approved."""
        with uow as active:
            lifecycle.approve(active, persisted_workspace.id, asset.id, actor=owner, clock=clock)
            outcome = lifecycle.edit(
                active,
                persisted_workspace.id,
                asset.id,
                actor=owner,
                clock=clock,
                body="Second thoughts about the opening.",
            )
            active.commit()

        assert outcome.returned_to_draft
        assert state_of(uow, persisted_workspace.id, asset.id) is ContentState.DRAFT

    def test_editing_a_draft_does_not_claim_it_lost_its_approval(
        self, uow, persisted_workspace, asset, owner, clock
    ):
        with uow as active:
            outcome = lifecycle.edit(
                active,
                persisted_workspace.id,
                asset.id,
                actor=owner,
                clock=clock,
                body="Tightened.",
            )
            active.commit()

        assert not outcome.returned_to_draft

    def test_published_content_cannot_be_edited(
        self, uow, persisted_workspace, asset, owner, clock
    ):
        """The post is live; editing the record would make the archive lie."""
        with uow as active:
            lifecycle.approve(active, persisted_workspace.id, asset.id, actor=owner, clock=clock)
            lifecycle.record_publication(
                active, persisted_workspace.id, asset.id, actor=owner, clock=clock
            )
            active.commit()

        with uow as active, pytest.raises(LifecycleError, match="cannot be edited"):
            lifecycle.edit(
                active,
                persisted_workspace.id,
                asset.id,
                actor=owner,
                clock=clock,
                body="Too late.",
            )

    @pytest.mark.parametrize("body", ["", "   \n  "])
    def test_a_blank_edit_is_refused(
        self, uow, persisted_workspace, asset, owner, clock, body: str
    ):
        with uow as active, pytest.raises(LifecycleError, match="needs a body"):
            lifecycle.edit(
                active, persisted_workspace.id, asset.id, actor=owner, clock=clock, body=body
            )


class TestRecordingPublication:
    def test_only_approved_content_can_be_published(
        self, uow, persisted_workspace, asset, owner, clock
    ):
        """The gate the whole state machine exists to hold."""
        with uow as active, pytest.raises(LifecycleError, match="not approved"):
            lifecycle.record_publication(
                active, persisted_workspace.id, asset.id, actor=owner, clock=clock
            )

    def test_it_stores_where_the_post_can_be_read(
        self, uow, persisted_workspace, asset, owner, clock
    ):
        with uow as active:
            lifecycle.approve(active, persisted_workspace.id, asset.id, actor=owner, clock=clock)
            publication = lifecycle.record_publication(
                active,
                persisted_workspace.id,
                asset.id,
                actor=owner,
                clock=clock,
                external_url="https://linkedin.test/posts/1",
                external_id="urn:li:share:1",
            )
            active.commit()

        with uow as active:
            stored = active.publications.get(persisted_workspace.id, publication.id)
        assert stored is not None
        assert stored.external_url == "https://linkedin.test/posts/1"
        assert stored.external_id == "urn:li:share:1"
        assert stored.published_by == "manual"
        assert state_of(uow, persisted_workspace.id, asset.id) is ContentState.PUBLISHED

    def test_it_can_be_backdated(self, uow, persisted_workspace, asset, owner, clock):
        """Posts get recorded after the fact; the timeline must stay true."""
        yesterday = clock.now() - timedelta(days=1)

        with uow as active:
            lifecycle.approve(active, persisted_workspace.id, asset.id, actor=owner, clock=clock)
            publication = lifecycle.record_publication(
                active,
                persisted_workspace.id,
                asset.id,
                actor=owner,
                clock=clock,
                published_at=yesterday,
            )
            active.commit()

        assert publication.published_at == yesterday
        signal = signals_of(uow, persisted_workspace.id, SIGNAL_CONTENT_PUBLISHED)[0]
        assert signal.occurred_at == yesterday
        assert signal.recorded_at == clock.now()

    def test_a_future_publication_time_is_refused_clearly(
        self, uow, persisted_workspace, asset, owner, clock
    ):
        with uow as active:
            lifecycle.approve(active, persisted_workspace.id, asset.id, actor=owner, clock=clock)
            with pytest.raises(LifecycleError, match="in the future"):
                lifecycle.record_publication(
                    active,
                    persisted_workspace.id,
                    asset.id,
                    actor=owner,
                    clock=clock,
                    published_at=clock.now() + timedelta(hours=1),
                )

    def test_recording_the_same_publication_twice_is_refused(
        self, uow, persisted_workspace, asset, owner, clock
    ):
        """Double-counting would double every metric that joins through it.

        The state machine is what stops it: ``published`` leads only to
        ``archived``, so an asset can never be approved a second time. The
        refusal therefore reads as "not approved", which is the accurate
        reason.
        """
        with uow as active:
            lifecycle.approve(active, persisted_workspace.id, asset.id, actor=owner, clock=clock)
            lifecycle.record_publication(
                active, persisted_workspace.id, asset.id, actor=owner, clock=clock
            )
            active.commit()

        with uow as active, pytest.raises(LifecycleError, match="not approved"):
            lifecycle.record_publication(
                active, persisted_workspace.id, asset.id, actor=owner, clock=clock
            )

        with uow as active:
            assert len(active.publications.list_for_asset(persisted_workspace.id, asset.id)) == 1

    def test_the_database_refuses_a_duplicate_that_bypasses_the_state_machine(
        self, uow, persisted_workspace, asset, owner, clock
    ):
        """The backstop, for any path that does not go through the use case."""
        from sqlalchemy.exc import IntegrityError

        from engine.core.domain.content import Publication

        with uow as active:
            lifecycle.approve(active, persisted_workspace.id, asset.id, actor=owner, clock=clock)
            lifecycle.record_publication(
                active, persisted_workspace.id, asset.id, actor=owner, clock=clock
            )
            active.commit()

        with pytest.raises(IntegrityError), uow as active:
            active.publications.add(
                Publication(
                    workspace_id=persisted_workspace.id,
                    asset_id=asset.id,
                    channel_key="linkedin",
                )
            )
            active.commit()

    def test_an_asset_with_no_channel_has_nowhere_to_have_gone_live(
        self, uow, persisted_workspace, story, system_actor, owner, clock
    ):
        record = ContentAsset(
            workspace_id=persisted_workspace.id,
            story_id=story.id,
            run_id="run_test",
            product_key="newsletter_item",
            channel_key=None,
            language="en",
            brand_profile_key="house_en",
        )
        with uow as active:
            active.assets.add(record)
            active.commit()

        with uow as active:
            lifecycle.approve(active, persisted_workspace.id, record.id, actor=owner, clock=clock)
            with pytest.raises(LifecycleError, match="no channel"):
                lifecycle.record_publication(
                    active, persisted_workspace.id, record.id, actor=owner, clock=clock
                )


class TestRecordingPerformance:
    @pytest.fixture
    def publication(self, uow, persisted_workspace, asset, owner, clock):
        with uow as active:
            lifecycle.approve(active, persisted_workspace.id, asset.id, actor=owner, clock=clock)
            record = lifecycle.record_publication(
                active, persisted_workspace.id, asset.id, actor=owner, clock=clock
            )
            active.commit()
        return record

    def test_an_observation_is_stored_and_signalled(
        self, uow, persisted_workspace, publication, owner, clock
    ):
        with uow as active:
            lifecycle.record_metric(
                active,
                persisted_workspace.id,
                publication.id,
                actor=owner,
                clock=clock,
                metric="impressions",
                value=4210,
            )
            active.commit()

        with uow as active:
            stored = active.metrics.list_for_publication(persisted_workspace.id, publication.id)
        assert [(record.metric, record.value) for record in stored] == [("impressions", 4210.0)]

        signal = signals_of(uow, persisted_workspace.id, SIGNAL_ENGAGEMENT)[0]
        assert signal.value == 4210.0
        assert signal.label == "impressions"
        assert signal.context["channel_key"] == "linkedin"

    def test_two_readings_of_one_metric_are_two_facts(
        self, uow, persisted_workspace, publication, owner, clock
    ):
        """Overwriting would collapse the only series growth is recoverable from."""
        first = clock.now() - timedelta(days=2)
        second = clock.now() - timedelta(days=1)

        with uow as active:
            for when, value in ((first, 87), (second, 103)):
                lifecycle.record_metric(
                    active,
                    persisted_workspace.id,
                    publication.id,
                    actor=owner,
                    clock=clock,
                    metric="likes",
                    value=value,
                    captured_at=when,
                )
            active.commit()

        with uow as active:
            stored = active.metrics.list_for_publication(persisted_workspace.id, publication.id)
        assert [record.value for record in stored] == [87.0, 103.0]

    def test_when_it_was_read_is_kept_apart_from_when_we_learned(
        self, uow, persisted_workspace, publication, owner, clock
    ):
        """Collapsing the two makes every engagement reading look instantaneous."""
        last_week = clock.now() - timedelta(days=7)

        with uow as active:
            lifecycle.record_metric(
                active,
                persisted_workspace.id,
                publication.id,
                actor=owner,
                clock=clock,
                metric="likes",
                value=12,
                captured_at=last_week,
            )
            active.commit()

        signal = signals_of(uow, persisted_workspace.id, SIGNAL_ENGAGEMENT)[0]
        assert signal.occurred_at == last_week
        assert signal.recorded_at == clock.now()
        assert signal.latency_seconds == timedelta(days=7).total_seconds()

    def test_a_future_reading_is_refused_clearly(
        self, uow, persisted_workspace, publication, owner, clock
    ):
        """Nobody has read a number that has not happened yet."""
        with uow as active, pytest.raises(LifecycleError, match="in the future"):
            lifecycle.record_metric(
                active,
                persisted_workspace.id,
                publication.id,
                actor=owner,
                clock=clock,
                metric="likes",
                value=1,
                captured_at=clock.now() + timedelta(hours=6),
            )

    def test_a_metric_for_an_unknown_publication_is_refused(
        self, uow, persisted_workspace, owner, clock
    ):
        with uow as active, pytest.raises(LifecycleError, match="not found"):
            lifecycle.record_metric(
                active,
                persisted_workspace.id,
                "pub_nonexistent",
                actor=owner,
                clock=clock,
                metric="likes",
                value=1,
            )


class TestTheWholeLoop:
    def test_a_draft_becomes_a_measured_publication(
        self, uow, persisted_workspace, asset, owner, clock
    ):
        """Phase 5's definition of done, as one test.

        Generated, edited, approved, published, measured -- and the outcome is
        joinable back to the story that produced it, which is the property the
        whole feedback loop exists for.
        """
        with uow as active:
            lifecycle.edit(
                active,
                persisted_workspace.id,
                asset.id,
                actor=owner,
                clock=clock,
                body="Small teams can reproduce this now. The recipe is public.",
            )
            lifecycle.approve(active, persisted_workspace.id, asset.id, actor=owner, clock=clock)
            publication = lifecycle.record_publication(
                active,
                persisted_workspace.id,
                asset.id,
                actor=owner,
                clock=clock,
                external_url="https://linkedin.test/posts/1",
            )
            lifecycle.record_metric(
                active,
                persisted_workspace.id,
                publication.id,
                actor=owner,
                clock=clock,
                metric="impressions",
                value=4210,
            )
            active.commit()

        with uow as active:
            performance = active.metrics.performance(persisted_workspace.id)
            stored_asset = active.assets.get(persisted_workspace.id, asset.id)

        assert len(performance) == 1
        assert performance[0].value_of("impressions") == 4210.0
        assert performance[0].asset_id == asset.id
        assert stored_asset is not None and stored_asset.story_id == asset.story_id
        assert stored_asset.state is ContentState.PUBLISHED

    def test_every_step_left_an_attributed_signal(
        self, uow, persisted_workspace, asset, owner, clock
    ):
        with uow as active:
            lifecycle.edit(
                active,
                persisted_workspace.id,
                asset.id,
                actor=owner,
                clock=clock,
                body="A human wrote this.",
            )
            lifecycle.approve(active, persisted_workspace.id, asset.id, actor=owner, clock=clock)
            publication = lifecycle.record_publication(
                active, persisted_workspace.id, asset.id, actor=owner, clock=clock
            )
            lifecycle.record_metric(
                active,
                persisted_workspace.id,
                publication.id,
                actor=owner,
                clock=clock,
                metric="likes",
                value=9,
            )
            active.commit()

        with uow as active:
            counts = active.signals.count_by_type(persisted_workspace.id)
            actors = {
                signal.actor_id
                for signal_type in counts
                for signal in active.signals.by_type(persisted_workspace.id, signal_type)
            }

        assert counts == {
            SIGNAL_CONTENT_EDITED: 1,
            SIGNAL_CONTENT_APPROVED: 1,
            SIGNAL_CONTENT_PUBLISHED: 1,
            SIGNAL_ENGAGEMENT: 1,
        }
        assert actors == {owner.id}


class TestAttribution:
    def test_the_owner_is_provisioned_as_a_human(self, owner):
        """A review decision attributed to 'the system' erases the training signal."""
        assert owner.kind is ActorKind.HUMAN
        assert owner.key == OWNER_ACTOR_KEY

    def test_the_system_actor_is_still_separate(self, system_actor, owner):
        assert system_actor.key == SYSTEM_ACTOR_KEY
        assert system_actor.kind is ActorKind.SYSTEM
        assert system_actor.id != owner.id

    def test_every_signal_actor_resolves_to_a_real_row(
        self, uow, persisted_workspace, asset, owner, clock
    ):
        """An id that joins to nothing is not attribution."""
        with uow as active:
            lifecycle.approve(active, persisted_workspace.id, asset.id, actor=owner, clock=clock)
            active.commit()

        with uow as active:
            signal = active.signals.by_type(persisted_workspace.id, SIGNAL_CONTENT_APPROVED)[0]
            actor = active.actors.get(signal.account_id, signal.actor_id)
        assert actor is not None


class TestTenantIsolation:
    def test_another_workspace_cannot_reach_this_asset(
        self, uow, persisted_workspace, asset, owner, clock
    ):
        with uow as active, pytest.raises(LifecycleError, match="not found"):
            lifecycle.approve(active, "ws_other", asset.id, actor=owner, clock=clock)


def test_the_clock_is_the_only_source_of_now(uow, persisted_workspace, asset, owner, clock):
    """Wall-clock reads inside a use case make time untestable."""
    with uow as active:
        approved = lifecycle.approve(
            active, persisted_workspace.id, asset.id, actor=owner, clock=clock
        )
        record = lifecycle.record_publication(
            active, persisted_workspace.id, approved.id, actor=owner, clock=clock
        )
        active.commit()

    assert record.published_at == clock.now()
    assert clock.now() != datetime.now(UTC)
