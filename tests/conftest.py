"""Shared pytest fixtures.

Tests run against a real SQLite database created from the ORM metadata rather
than against mocks. Mocked persistence hides exactly the bugs that matter here:
constraint violations, mapping errors and transaction semantics.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from engine.adapters.clock import FixedClock
from engine.adapters.persistence.models import Base
from engine.adapters.persistence.session import create_db_engine, create_session_factory
from engine.adapters.persistence.unit_of_work import SqlAlchemyUnitOfWork
from engine.cli.provisioning import ensure_account
from engine.cli.provisioning import system_actor as provisioning_system_actor
from engine.core.domain.account import Account, Actor
from engine.core.domain.enums import SourceKind
from engine.core.domain.language import ARABIC, ENGLISH
from engine.core.domain.source import Source
from engine.core.domain.workspace import Workspace
from engine.settings import Settings


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    """Settings pointed at an isolated per-test database and directories."""
    return Settings(
        environment="local",
        database_url=f"sqlite+pysqlite:///{tmp_path / 'test.db'}",
        data_dir=tmp_path / "data",
        output_dir=tmp_path / "output",
        config_dir=Path("config"),
    )


@pytest.fixture
def db_engine(settings: Settings) -> Iterator[Engine]:
    engine = create_db_engine(settings)
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def session_factory(db_engine: Engine) -> sessionmaker[Session]:
    return create_session_factory(db_engine)


@pytest.fixture
def uow(session_factory: sessionmaker[Session]) -> SqlAlchemyUnitOfWork:
    return SqlAlchemyUnitOfWork(session_factory)


@pytest.fixture
def clock() -> FixedClock:
    """A frozen clock, so time-dependent assertions are exact."""
    return FixedClock(datetime(2026, 1, 15, 9, 30, tzinfo=UTC))


@pytest.fixture
def account() -> Account:
    return Account(slug="test-account", name="Test Account")


@pytest.fixture
def persisted_account(uow: SqlAlchemyUnitOfWork, account: Account, clock: FixedClock) -> Account:
    """Provisioned through the production path, system actor included.

    Adding the account row directly would allow a state the application cannot
    produce -- an account with no system actor -- and every test attributing an
    automated action would then be testing against a fiction.
    """
    with uow as active:
        active.accounts.add(account)
        ensure_account(active, slug=account.slug, name=account.name, clock=clock)
        active.commit()
    return account


@pytest.fixture
def system_actor(uow: SqlAlchemyUnitOfWork, persisted_account: Account) -> Actor:
    """Every account has one; automated actions are attributed to it."""
    with uow as active:
        return provisioning_system_actor(active, persisted_account.id)


@pytest.fixture
def workspace(persisted_account: Account) -> Workspace:
    return Workspace(
        account_id=persisted_account.id,
        slug="test-workspace",
        name="Test Workspace",
        timezone="Asia/Riyadh",
        languages=[ARABIC, ENGLISH],
    )


@pytest.fixture
def persisted_workspace(uow: SqlAlchemyUnitOfWork, workspace: Workspace) -> Workspace:
    with uow as active:
        active.workspaces.add(workspace)
        active.commit()
    return workspace


@pytest.fixture
def source(persisted_workspace: Workspace) -> Source:
    return Source(
        workspace_id=persisted_workspace.id,
        kind=SourceKind.RSS,
        name="arXiv cs.AI",
        identifier="http://export.arxiv.org/rss/cs.AI",
        parser_key="arxiv_rss",
        trust_weight=0.75,
    )
