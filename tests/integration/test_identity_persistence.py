"""Persistence tests for the account, actor and signal layers.

Run against real SQLite: the things worth testing here are constraint
enforcement and index-backed queries, neither of which a mock can exercise.
"""

from __future__ import annotations

import pytest
from sqlalchemy.exc import IntegrityError

from engine.adapters.persistence.unit_of_work import SqlAlchemyUnitOfWork
from engine.core.domain.account import Account, Actor, Membership
from engine.core.domain.enums import ActorKind, SubjectType
from engine.core.domain.signal import (
    SIGNAL_CONTENT_EDITED,
    SIGNAL_ENGAGEMENT,
    SIGNAL_STORY_REJECTED,
    Signal,
)
from engine.core.domain.workspace import Workspace

pytestmark = pytest.mark.integration


def make_signal(workspace: Workspace, actor: Actor, **overrides) -> Signal:
    payload = {
        "account_id": workspace.account_id,
        "workspace_id": workspace.id,
        "actor_id": actor.id,
        "subject_type": SubjectType.STORY,
        "subject_id": "sty_example",
        "signal_type": SIGNAL_STORY_REJECTED,
        "label": "off-topic",
    }
    payload.update(overrides)
    return Signal(**payload)


class TestAccountRepository:
    def test_round_trips(self, uow: SqlAlchemyUnitOfWork, account: Account):
        with uow as active:
            active.accounts.add(account)
            active.commit()

        with uow as active:
            loaded = active.accounts.get_by_slug(account.slug)
        assert loaded is not None
        assert loaded.id == account.id
        assert loaded.quota.max_workspaces == account.quota.max_workspaces

    def test_slug_is_unique(self, uow: SqlAlchemyUnitOfWork, account: Account):
        with uow as active:
            active.accounts.add(account)
            active.commit()

        with pytest.raises(IntegrityError), uow as active:
            active.accounts.add(Account(slug=account.slug, name="Duplicate"))
            active.commit()

    def test_inactive_accounts_are_hidden_by_default(
        self, uow: SqlAlchemyUnitOfWork, account: Account
    ):
        account.is_active = False
        with uow as active:
            active.accounts.add(account)
            active.commit()

        with uow as active:
            assert active.accounts.list_all() == []
            assert len(active.accounts.list_all(include_inactive=True)) == 1


class TestActorRepository:
    def test_key_is_unique_per_account(self, uow: SqlAlchemyUnitOfWork, persisted_account: Account):
        with uow as active:
            active.actors.add(
                Actor(account_id=persisted_account.id, key="alice", display_name="Alice")
            )
            active.commit()

        with pytest.raises(IntegrityError), uow as active:
            active.actors.add(
                Actor(account_id=persisted_account.id, key="alice", display_name="Alice Again")
            )
            active.commit()

    def test_same_key_is_allowed_in_a_different_account(self, uow: SqlAlchemyUnitOfWork):
        """One person at two agencies is two actors, deliberately."""
        first = Account(slug="agency-one", name="One")
        second = Account(slug="agency-two", name="Two")
        with uow as active:
            active.accounts.add(first)
            active.accounts.add(second)
            active.actors.add(Actor(account_id=first.id, key="alice", display_name="Alice"))
            active.actors.add(Actor(account_id=second.id, key="alice", display_name="Alice"))
            active.commit()

        with uow as active:
            assert active.actors.get_by_key(first.id, "alice") is not None
            assert active.actors.get_by_key(second.id, "alice") is not None

    def test_lookup_is_account_scoped(self, uow: SqlAlchemyUnitOfWork, system_actor: Actor):
        with uow as active:
            assert active.actors.get("acc_other", system_actor.id) is None
            assert active.actors.get(system_actor.account_id, system_actor.id) is not None

    def test_membership_round_trips(self, uow: SqlAlchemyUnitOfWork, system_actor: Actor):
        with uow as active:
            active.actors.add_membership(
                Membership(account_id=system_actor.account_id, actor_id=system_actor.id)
            )
            active.commit()

        with uow as active:
            memberships = active.actors.list_memberships(system_actor.account_id)
        assert len(memberships) == 1
        assert memberships[0].actor_id == system_actor.id


class TestSignalRepository:
    def test_emit_and_read_back_by_subject(
        self, uow: SqlAlchemyUnitOfWork, persisted_workspace: Workspace, system_actor: Actor
    ):
        with uow as active:
            active.signals.emit(make_signal(persisted_workspace, system_actor))
            active.commit()

        with uow as active:
            signals = active.signals.for_subject(
                persisted_workspace.id, SubjectType.STORY, "sty_example"
            )
        assert len(signals) == 1
        assert signals[0].label == "off-topic"

    def test_emit_many_is_batched(
        self, uow: SqlAlchemyUnitOfWork, persisted_workspace: Workspace, system_actor: Actor
    ):
        batch = [
            make_signal(persisted_workspace, system_actor, subject_id=f"sty_{index}")
            for index in range(25)
        ]
        with uow as active:
            assert active.signals.emit_many(batch) == 25
            active.commit()

        with uow as active:
            assert active.signals.count_by_type(persisted_workspace.id)[SIGNAL_STORY_REJECTED] == 25

    def test_emit_many_handles_the_empty_case(
        self, uow: SqlAlchemyUnitOfWork, persisted_workspace: Workspace
    ):
        with uow as active:
            assert active.signals.emit_many([]) == 0

    def test_counts_group_by_type(
        self, uow: SqlAlchemyUnitOfWork, persisted_workspace: Workspace, system_actor: Actor
    ):
        with uow as active:
            active.signals.emit_many(
                [
                    make_signal(persisted_workspace, system_actor),
                    make_signal(persisted_workspace, system_actor, subject_id="sty_2"),
                    make_signal(
                        persisted_workspace,
                        system_actor,
                        signal_type=SIGNAL_ENGAGEMENT,
                        label=None,
                        value=42.0,
                    ),
                ]
            )
            active.commit()

        with uow as active:
            counts = active.signals.count_by_type(persisted_workspace.id)
        assert counts == {SIGNAL_STORY_REJECTED: 2, SIGNAL_ENGAGEMENT: 1}

    def test_by_type_returns_newest_first(
        self, uow: SqlAlchemyUnitOfWork, persisted_workspace: Workspace, system_actor: Actor
    ):
        from datetime import UTC, datetime, timedelta

        base = datetime(2026, 8, 1, 9, 0, tzinfo=UTC)
        with uow as active:
            active.signals.emit_many(
                [
                    make_signal(
                        persisted_workspace,
                        system_actor,
                        subject_id=f"sty_{index}",
                        occurred_at=base + timedelta(days=index),
                        recorded_at=base + timedelta(days=index),
                    )
                    for index in range(3)
                ]
            )
            active.commit()

        with uow as active:
            signals = active.signals.by_type(persisted_workspace.id, SIGNAL_STORY_REJECTED)
        assert [s.subject_id for s in signals] == ["sty_2", "sty_1", "sty_0"]

    def test_queries_are_workspace_scoped(
        self, uow: SqlAlchemyUnitOfWork, persisted_workspace: Workspace, system_actor: Actor
    ):
        with uow as active:
            active.signals.emit(make_signal(persisted_workspace, system_actor))
            active.commit()

        with uow as active:
            assert active.signals.for_subject("ws_other", SubjectType.STORY, "sty_example") == []
            assert active.signals.count_by_type("ws_other") == {}

    def test_run_scoped_counting(
        self, uow: SqlAlchemyUnitOfWork, persisted_workspace: Workspace, system_actor: Actor
    ):
        with uow as active:
            active.signals.emit_many(
                [
                    make_signal(persisted_workspace, system_actor, run_id="run_a"),
                    make_signal(
                        persisted_workspace, system_actor, subject_id="sty_2", run_id="run_a"
                    ),
                    make_signal(
                        persisted_workspace, system_actor, subject_id="sty_3", run_id="run_b"
                    ),
                ]
            )
            active.commit()

        with uow as active:
            assert active.signals.count_for_run(persisted_workspace.id, "run_a") == 2
            assert active.signals.count_for_run(persisted_workspace.id, "run_b") == 1

    def test_the_repository_offers_no_mutation_path(self):
        """Append-only is enforced by the absence of methods, not by convention."""
        from engine.adapters.persistence.identity_repositories import SqlAlchemySignalRepository

        for forbidden in ("update", "delete", "remove", "apply"):
            assert not hasattr(SqlAlchemySignalRepository, forbidden)


class TestSignalIsolation:
    def test_two_workspaces_do_not_see_each_others_signals(
        self, uow: SqlAlchemyUnitOfWork, persisted_account: Account, system_actor: Actor
    ):
        first = Workspace(account_id=persisted_account.id, slug="brand-one", name="One")
        second = Workspace(account_id=persisted_account.id, slug="brand-two", name="Two")
        with uow as active:
            active.workspaces.add(first)
            active.workspaces.add(second)
            active.signals.emit(make_signal(first, system_actor))
            active.commit()

        with uow as active:
            assert len(active.signals.count_by_type(first.id)) == 1
            assert active.signals.count_by_type(second.id) == {}


class TestActorKindPersistence:
    def test_kind_survives_the_round_trip(
        self, uow: SqlAlchemyUnitOfWork, persisted_account: Account
    ):
        actor = Actor(
            account_id=persisted_account.id,
            key="scheduler",
            display_name="Scheduler",
            kind=ActorKind.SCHEDULER,
        )
        with uow as active:
            active.actors.add(actor)
            active.commit()

        with uow as active:
            loaded = active.actors.get_by_key(persisted_account.id, "scheduler")
        assert loaded is not None
        assert loaded.kind is ActorKind.SCHEDULER
        assert loaded.is_system is True


class TestContentEditSignal:
    def test_edit_distance_is_recordable(
        self, uow: SqlAlchemyUnitOfWork, persisted_workspace: Workspace, system_actor: Actor
    ):
        """The headline quality metric for generation: how much did the human change?"""
        with uow as active:
            active.signals.emit(
                make_signal(
                    persisted_workspace,
                    system_actor,
                    subject_type=SubjectType.CONTENT_ASSET,
                    subject_id="ast_x",
                    signal_type=SIGNAL_CONTENT_EDITED,
                    label=None,
                    value=0.18,
                    context={"revision_from": "rev_1", "revision_to": "rev_2"},
                )
            )
            active.commit()

        with uow as active:
            signals = active.signals.for_subject(
                persisted_workspace.id, SubjectType.CONTENT_ASSET, "ast_x"
            )
        assert signals[0].value == 0.18
        assert signals[0].context["revision_from"] == "rev_1"
