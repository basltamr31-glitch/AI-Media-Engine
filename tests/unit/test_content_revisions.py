"""Tests for the content revision chain (ADR 0013).

The guarantee these protect: what the model wrote is never overwritten by what
the human published, because that difference is the style dataset and it cannot
be reconstructed once lost.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from engine.core.domain.content import ContentAsset, ContentRevision, RevisionOrigin
from engine.core.domain.enums import Channel, ContentKind, ContentState, Language
from engine.core.domain.ids import IdPrefix, is_valid_id

SYSTEM = "act_system"
HUMAN = "act_alice"


@pytest.fixture
def asset() -> ContentAsset:
    return ContentAsset(
        workspace_id="ws_1",
        story_id="sty_1",
        run_id="run_1",
        kind=ContentKind.SOCIAL_POST,
        channel=Channel.LINKEDIN,
        language=Language.EN,
        brand_profile_key="default_en",
    )


class TestChainConstruction:
    def test_first_revision_starts_at_one_with_no_parent(self, asset: ContentAsset):
        revision = asset.next_revision("draft", origin=RevisionOrigin.GENERATED, actor_id=SYSTEM)
        assert revision.revision_number == 1
        assert revision.parent_revision_id is None
        assert is_valid_id(revision.id, IdPrefix.REVISION)

    def test_each_revision_links_to_its_parent(self, asset: ContentAsset):
        first = asset.next_revision("draft", origin=RevisionOrigin.GENERATED, actor_id=SYSTEM)
        second = asset.next_revision(
            "edited", origin=RevisionOrigin.EDITED, actor_id=HUMAN, previous=first
        )
        assert second.revision_number == 2
        assert second.parent_revision_id == first.id

    def test_revisions_inherit_the_assets_tenancy(self, asset: ContentAsset):
        revision = asset.next_revision("draft", origin=RevisionOrigin.GENERATED, actor_id=SYSTEM)
        assert revision.workspace_id == asset.workspace_id
        assert revision.asset_id == asset.id

    def test_generated_revisions_record_their_prompt_version(self, asset: ContentAsset):
        """Without this, a style regression after a prompt change is undiagnosable."""
        revision = asset.next_revision(
            "draft", origin=RevisionOrigin.GENERATED, actor_id=SYSTEM, prompt_version="post_en@v3"
        )
        assert revision.prompt_version == "post_en@v3"

    def test_building_a_revision_does_not_move_the_pointer(self, asset: ContentAsset):
        """The pointer advances only once the revision is durable."""
        asset.next_revision("draft", origin=RevisionOrigin.GENERATED, actor_id=SYSTEM)
        assert asset.current_revision_id is None


class TestImmutability:
    def test_a_revision_cannot_be_edited(self, asset: ContentAsset):
        revision = asset.next_revision("draft", origin=RevisionOrigin.GENERATED, actor_id=SYSTEM)
        with pytest.raises(ValidationError):
            revision.body = "rewritten"  # type: ignore[misc]

    def test_revision_numbers_must_be_positive(self):
        with pytest.raises(ValidationError):
            ContentRevision(
                workspace_id="ws_1",
                asset_id="ast_1",
                revision_number=0,
                body="x",
                origin=RevisionOrigin.GENERATED,
                actor_id=SYSTEM,
            )


class TestTheDatasetItProtects:
    def test_the_generated_text_survives_a_human_edit(self, asset: ContentAsset):
        """The whole point: both halves of the preference pair remain readable."""
        generated = asset.next_revision(
            "Big news: OpenAI shipped a thing!!!",
            origin=RevisionOrigin.GENERATED,
            actor_id=SYSTEM,
        )
        published = asset.next_revision(
            "OpenAI shipped a thing. Here is why it matters.",
            origin=RevisionOrigin.EDITED,
            actor_id=HUMAN,
            previous=generated,
        )

        assert generated.body != published.body
        assert generated.body == "Big news: OpenAI shipped a thing!!!"
        assert generated.actor_id == SYSTEM
        assert published.actor_id == HUMAN

    def test_a_restore_appends_rather_than_rewinding(self, asset: ContentAsset):
        """History reads as what happened, not as what we wish had happened."""
        first = asset.next_revision("original", origin=RevisionOrigin.GENERATED, actor_id=SYSTEM)
        second = asset.next_revision(
            "regrettable edit", origin=RevisionOrigin.EDITED, actor_id=HUMAN, previous=first
        )
        third = asset.next_revision(
            first.body, origin=RevisionOrigin.RESTORED, actor_id=HUMAN, previous=second
        )

        assert third.revision_number == 3
        assert third.body == first.body
        assert third.origin is RevisionOrigin.RESTORED
        assert third.parent_revision_id == second.id


class TestWorkflowStateIsSeparateFromText:
    def test_state_transitions_still_work(self, asset: ContentAsset):
        asset.transition_to(ContentState.IN_REVIEW)
        asset.transition_to(ContentState.APPROVED, notes="ship it")
        assert asset.state is ContentState.APPROVED
        assert asset.review_notes == "ship it"

    def test_illegal_transitions_are_still_rejected(self, asset: ContentAsset):
        with pytest.raises(ValueError, match="illegal content transition"):
            asset.transition_to(ContentState.PUBLISHED)

    def test_the_asset_no_longer_carries_text(self, asset: ContentAsset):
        """Text lives in the chain; a body field here would let it be overwritten."""
        assert not hasattr(asset, "body")
