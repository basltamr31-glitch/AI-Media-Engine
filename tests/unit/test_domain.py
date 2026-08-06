"""Domain invariants.

These tests target the rules that encode business and legal policy, not
Pydantic's own behaviour.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from engine.core.domain.content import ContentAsset
from engine.core.domain.enums import (
    Channel,
    ContentKind,
    ContentState,
    EvidenceKind,
    Language,
    can_transition,
)
from engine.core.domain.policy import ScoringWeights
from engine.core.domain.source import compute_content_hash
from engine.core.domain.story import MAX_QUOTE_WORDS, Evidence, Story
from engine.core.domain.workspace import Workspace


class TestWorkspace:
    def test_slug_is_normalised(self):
        workspace = Workspace(account_id="acc_x", slug="  Default-Tenant  ", name="X")
        assert workspace.slug == "default-tenant"

    @pytest.mark.parametrize("slug", ["ab", "-leading", "trailing-", "has space", "UPPER!"])
    def test_invalid_slugs_are_rejected(self, slug: str):
        with pytest.raises(ValidationError):
            Workspace(account_id="acc_x", slug=slug, name="X")

    def test_duplicate_languages_are_rejected(self):
        with pytest.raises(ValidationError):
            Workspace(
                account_id="acc_x",
                slug="valid-slug",
                name="X",
                languages=[Language.AR, Language.AR],
            )


class TestEvidence:
    """The fair-use quote ceiling is a legal control, so it is a hard invariant."""

    def test_short_quote_is_accepted(self):
        evidence = Evidence(
            workspace_id="ws_1",
            story_id="sty_1",
            claim="The model was released today.",
            kind=EvidenceKind.PRIMARY,
            source_url="https://example.com/post",
            quote="available starting today",
        )
        assert evidence.quote == "available starting today"

    def test_overlong_quote_is_rejected(self):
        with pytest.raises(ValidationError, match="fair-use limit"):
            Evidence(
                workspace_id="ws_1",
                story_id="sty_1",
                claim="Something happened.",
                kind=EvidenceKind.AGGREGATOR,
                source_url="https://example.com",
                quote=" ".join(["word"] * (MAX_QUOTE_WORDS + 1)),
            )

    def test_evidence_is_immutable(self):
        evidence = Evidence(
            workspace_id="ws_1",
            story_id="sty_1",
            claim="A claim.",
            kind=EvidenceKind.PRIMARY,
            source_url="https://example.com",
        )
        with pytest.raises(ValidationError):
            # Statically rejected too, which is the belt to this braces -- but the
            # runtime guarantee is what protects the audit trail.
            evidence.claim = "changed"  # type: ignore[misc]


class TestStory:
    def test_is_not_publishable_without_primary_source(self):
        """The core editorial rule: newsletters are tips, not text."""
        story = Story(workspace_id="ws_1", canonical_headline="Something shipped")
        assert not story.is_publishable

    def test_source_count_deduplicates(self):
        story = Story(
            workspace_id="ws_1",
            canonical_headline="Corroborated story",
            source_ids=["src_a", "src_b", "src_a"],
        )
        assert story.source_count == 2


class TestContentStateMachine:
    def test_review_happy_path(self):
        asset = ContentAsset(
            workspace_id="ws_1",
            story_id="sty_1",
            run_id="run_1",
            kind=ContentKind.SOCIAL_POST,
            channel=Channel.LINKEDIN,
            language=Language.EN,
            brand_profile_key="default_en",
        )
        asset.transition_to(ContentState.IN_REVIEW)
        asset.transition_to(ContentState.APPROVED, notes="looks good")
        asset.transition_to(ContentState.PUBLISHED)
        assert asset.state is ContentState.PUBLISHED
        assert asset.review_notes == "looks good"

    def test_draft_cannot_skip_review(self):
        """Nothing reaches a channel without passing the human gate."""
        asset = ContentAsset(
            workspace_id="ws_1",
            story_id="sty_1",
            run_id="run_1",
            kind=ContentKind.SOCIAL_POST,
            channel=Channel.X,
            language=Language.AR,
            brand_profile_key="default_ar",
        )
        with pytest.raises(ValueError, match="illegal content transition"):
            asset.transition_to(ContentState.PUBLISHED)

    def test_archived_is_terminal(self):
        assert not can_transition(ContentState.ARCHIVED, ContentState.DRAFT)


class TestScoringWeights:
    def test_defaults_sum_to_one(self):
        assert pytest.approx(sum(ScoringWeights().as_dict().values())) == 1.0

    def test_unbalanced_weights_are_rejected(self):
        with pytest.raises(ValidationError, match=r"must sum to 1\.0"):
            ScoringWeights(
                corroboration=0.9,
                source_authority=0.9,
                recency=0.1,
                prominence=0.1,
                audience_fit=0.1,
            )


class TestContentHash:
    def test_is_stable_and_whitespace_insensitive(self):
        assert compute_content_hash("Title", "Body") == compute_content_hash(" Title ", "Body ")

    def test_distinguishes_field_boundaries(self):
        """Guards against 'ab'+'c' colliding with 'a'+'bc'."""
        assert compute_content_hash("ab", "c") != compute_content_hash("a", "bc")
