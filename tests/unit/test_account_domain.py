"""Tests for the account, actor and membership domain models (ADR 0011)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from engine.core.domain.account import Account, Actor, Membership, Quota
from engine.core.domain.enums import ActorKind, MembershipRole, PlanTier
from engine.core.domain.ids import IdPrefix, is_valid_id


class TestAccount:
    def test_defaults_to_the_free_plan(self):
        assert Account(slug="acme", name="Acme").plan is PlanTier.FREE

    def test_generates_a_prefixed_id(self):
        assert is_valid_id(Account(slug="acme", name="Acme").id, IdPrefix.ACCOUNT)

    def test_slug_is_normalised(self):
        assert Account(slug="  Acme-Corp  ", name="Acme").slug == "acme-corp"

    @pytest.mark.parametrize("slug", ["ab", "-leading", "trailing-", "has space", "UPPER!"])
    def test_invalid_slugs_are_rejected(self, slug: str):
        with pytest.raises(ValidationError):
            Account(slug=slug, name="Acme")

    def test_unlimited_quota_is_available_for_self_hosting(self):
        quota = Quota.unlimited()
        assert quota.max_workspaces >= 1000
        assert quota.max_cost_usd_per_month >= 1000

    def test_quota_rejects_nonsense_values(self):
        with pytest.raises(ValidationError):
            Quota(max_workspaces=0)


class TestActor:
    def test_defaults_to_human(self):
        actor = Actor(account_id="acc_x", key="alice", display_name="Alice")
        assert actor.kind is ActorKind.HUMAN
        assert actor.is_system is False

    def test_system_actor_is_flagged(self):
        actor = Actor(
            account_id="acc_x", key="system", display_name="System", kind=ActorKind.SYSTEM
        )
        assert actor.is_system is True

    def test_key_is_normalised(self):
        assert Actor(account_id="acc_x", key="  Alice  ", display_name="A").key == "alice"

    def test_empty_key_is_rejected(self):
        with pytest.raises(ValidationError):
            Actor(account_id="acc_x", key="   ", display_name="A")

    def test_actors_are_account_scoped(self):
        """The same person at two agencies is two actors: preferences differ."""
        one = Actor(account_id="acc_a", key="alice", display_name="Alice")
        two = Actor(account_id="acc_b", key="alice", display_name="Alice")
        assert one.id != two.id
        assert one.account_id != two.account_id


class TestMembership:
    @pytest.mark.parametrize(
        ("role", "expected"),
        [
            (MembershipRole.OWNER, True),
            (MembershipRole.ADMIN, True),
            (MembershipRole.EDITOR, True),
            (MembershipRole.VIEWER, False),
        ],
    )
    def test_approval_rights_by_role(self, role: MembershipRole, expected: bool):
        membership = Membership(account_id="acc_x", actor_id="act_x", role=role)
        assert membership.can_approve is expected

    def test_defaults_to_editor(self):
        assert Membership(account_id="acc_x", actor_id="act_x").role is MembershipRole.EDITOR
