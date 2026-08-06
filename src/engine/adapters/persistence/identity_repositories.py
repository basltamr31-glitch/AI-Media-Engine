"""Repositories for the account, identity and signal layers.

Kept in their own module rather than appended to ``repositories.py`` because
they answer a different question. ``repositories.py`` serves the *editorial*
domain (workspaces, sources, documents, runs); this serves the *commercial and
feedback* domain (accounts, actors, signals). The two grow at different rates
and are read by different phases.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from engine.adapters.persistence import mappers
from engine.adapters.persistence.models import (
    AccountRow,
    ActorRow,
    MembershipRow,
    SignalRow,
)
from engine.core.domain.account import Account, Actor, Membership
from engine.core.domain.enums import SubjectType
from engine.core.domain.signal import Signal


class EntityNotFoundError(LookupError):
    """Raised when an update targets a row that does not exist."""


class SqlAlchemyAccountRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, account: Account) -> Account:
        self._session.add(mappers.account_to_row(account))
        self._session.flush()
        return account

    def get(self, account_id: str) -> Account | None:
        row = self._session.get(AccountRow, account_id)
        return mappers.account_from_row(row) if row else None

    def get_by_slug(self, slug: str) -> Account | None:
        row = self._session.scalar(select(AccountRow).where(AccountRow.slug == slug))
        return mappers.account_from_row(row) if row else None

    def list_all(self, *, include_inactive: bool = False) -> list[Account]:
        statement = select(AccountRow).order_by(AccountRow.slug)
        if not include_inactive:
            statement = statement.where(AccountRow.is_active.is_(True))
        return [mappers.account_from_row(row) for row in self._session.scalars(statement)]

    def update(self, account: Account) -> Account:
        row = self._session.get(AccountRow, account.id)
        if row is None:
            msg = f"account {account.id} not found"
            raise EntityNotFoundError(msg)
        mappers.apply_account(row, account)
        self._session.flush()
        return account


class SqlAlchemyActorRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, actor: Actor) -> Actor:
        self._session.add(mappers.actor_to_row(actor))
        self._session.flush()
        return actor

    def get(self, account_id: str, actor_id: str) -> Actor | None:
        row = self._session.scalar(
            select(ActorRow).where(
                ActorRow.id == actor_id,
                ActorRow.account_id == account_id,
            )
        )
        return mappers.actor_from_row(row) if row else None

    def get_by_key(self, account_id: str, key: str) -> Actor | None:
        row = self._session.scalar(
            select(ActorRow).where(
                ActorRow.account_id == account_id,
                ActorRow.key == key,
            )
        )
        return mappers.actor_from_row(row) if row else None

    def list_for_account(self, account_id: str, *, only_active: bool = True) -> list[Actor]:
        statement = select(ActorRow).where(ActorRow.account_id == account_id).order_by(ActorRow.key)
        if only_active:
            statement = statement.where(ActorRow.is_active.is_(True))
        return [mappers.actor_from_row(row) for row in self._session.scalars(statement)]

    def add_membership(self, membership: Membership) -> Membership:
        self._session.add(mappers.membership_to_row(membership))
        self._session.flush()
        return membership

    def list_memberships(self, account_id: str) -> list[Membership]:
        statement = select(MembershipRow).where(MembershipRow.account_id == account_id)
        return [mappers.membership_from_row(row) for row in self._session.scalars(statement)]


class SqlAlchemySignalRepository:
    """Append-only signal store.

    There is deliberately **no update or delete method**. ADR 0012 makes the log
    immutable, and the cheapest way to guarantee that is to give callers no way
    to violate it.
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    def emit(self, signal: Signal) -> Signal:
        self._session.add(mappers.signal_to_row(signal))
        self._session.flush()
        return signal

    def emit_many(self, signals: list[Signal]) -> int:
        if not signals:
            return 0
        self._session.add_all([mappers.signal_to_row(signal) for signal in signals])
        self._session.flush()
        return len(signals)

    def for_subject(
        self,
        workspace_id: str,
        subject_type: SubjectType,
        subject_id: str,
    ) -> list[Signal]:
        statement = (
            select(SignalRow)
            .where(
                SignalRow.workspace_id == workspace_id,
                SignalRow.subject_type == subject_type.value,
                SignalRow.subject_id == subject_id,
            )
            .order_by(SignalRow.occurred_at)
        )
        return [mappers.signal_from_row(row) for row in self._session.scalars(statement)]

    def by_type(self, workspace_id: str, signal_type: str, *, limit: int = 1000) -> list[Signal]:
        statement = (
            select(SignalRow)
            .where(
                SignalRow.workspace_id == workspace_id,
                SignalRow.signal_type == signal_type,
            )
            .order_by(SignalRow.occurred_at.desc())
            .limit(limit)
        )
        return [mappers.signal_from_row(row) for row in self._session.scalars(statement)]

    def count_by_type(self, workspace_id: str) -> dict[str, int]:
        statement = (
            select(SignalRow.signal_type, func.count())
            .where(SignalRow.workspace_id == workspace_id)
            .group_by(SignalRow.signal_type)
            .order_by(func.count().desc())
        )
        return dict(self._session.execute(statement).all())  # type: ignore[arg-type]

    def count_for_run(self, workspace_id: str, run_id: str) -> int:
        total = self._session.scalar(
            select(func.count())
            .select_from(SignalRow)
            .where(SignalRow.workspace_id == workspace_id, SignalRow.run_id == run_id)
        )
        return int(total or 0)
