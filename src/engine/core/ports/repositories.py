"""Persistence ports.

The domain declares what it needs from storage; ``engine.adapters.persistence``
supplies it. Consequences:

* Domain logic and tests never import SQLAlchemy.
* Swapping SQLite for Postgres, or adding a cache or read replica, is an adapter
  change with no reach into business logic.
* Every method is workspace-scoped by signature, so tenant isolation is enforced
  by the type checker rather than by remembering to add a WHERE clause.
"""

from __future__ import annotations

from types import TracebackType
from typing import Protocol, runtime_checkable

from engine.core.domain.account import Account, Actor, Membership
from engine.core.domain.enums import SubjectType
from engine.core.domain.ledger import RunLedger, StageRecord
from engine.core.domain.signal import Signal
from engine.core.domain.source import Source, SourceDocument
from engine.core.domain.workspace import Workspace


@runtime_checkable
class AccountRepository(Protocol):
    def add(self, account: Account) -> Account: ...

    def get(self, account_id: str) -> Account | None: ...

    def get_by_slug(self, slug: str) -> Account | None: ...

    def list_all(self, *, include_inactive: bool = False) -> list[Account]: ...

    def update(self, account: Account) -> Account: ...


@runtime_checkable
class ActorRepository(Protocol):
    def add(self, actor: Actor) -> Actor: ...

    def get(self, account_id: str, actor_id: str) -> Actor | None: ...

    def get_by_key(self, account_id: str, key: str) -> Actor | None: ...

    def list_for_account(self, account_id: str, *, only_active: bool = True) -> list[Actor]: ...

    def add_membership(self, membership: Membership) -> Membership: ...

    def list_memberships(self, account_id: str) -> list[Membership]: ...


@runtime_checkable
class SignalRepository(Protocol):
    """Append-only. No update or delete method exists, by design (ADR 0012)."""

    def emit(self, signal: Signal) -> Signal: ...

    def emit_many(self, signals: list[Signal]) -> int: ...

    def for_subject(
        self, workspace_id: str, subject_type: SubjectType, subject_id: str
    ) -> list[Signal]: ...

    def by_type(
        self, workspace_id: str, signal_type: str, *, limit: int = 1000
    ) -> list[Signal]: ...

    def count_by_type(self, workspace_id: str) -> dict[str, int]: ...

    def count_for_run(self, workspace_id: str, run_id: str) -> int: ...


@runtime_checkable
class WorkspaceRepository(Protocol):
    def add(self, workspace: Workspace) -> Workspace: ...

    def get(self, workspace_id: str) -> Workspace | None: ...

    def get_by_slug(self, slug: str) -> Workspace | None: ...

    def list_all(self, *, include_inactive: bool = False) -> list[Workspace]: ...

    def update(self, workspace: Workspace) -> Workspace: ...


@runtime_checkable
class SourceRepository(Protocol):
    def add(self, source: Source) -> Source: ...

    def get(self, workspace_id: str, source_id: str) -> Source | None: ...

    def get_by_identifier(self, workspace_id: str, kind: str, identifier: str) -> Source | None: ...

    def list_for_workspace(
        self, workspace_id: str, *, only_active: bool = True
    ) -> list[Source]: ...

    def update(self, source: Source) -> Source: ...


@runtime_checkable
class SourceDocumentRepository(Protocol):
    def add(self, document: SourceDocument) -> SourceDocument: ...

    def get(self, workspace_id: str, document_id: str) -> SourceDocument | None: ...

    def exists_by_hash(self, workspace_id: str, content_hash: str) -> bool:
        """Idempotency check: has this exact content already been ingested?"""
        ...

    def list_for_source(
        self, workspace_id: str, source_id: str, *, limit: int = 100
    ) -> list[SourceDocument]: ...

    def count_for_source(self, workspace_id: str, source_id: str) -> int:
        """Total documents ingested from a source. Powers `engine ingest status`."""
        ...


@runtime_checkable
class RunRepository(Protocol):
    def add(self, run: RunLedger) -> RunLedger: ...

    def get(self, workspace_id: str, run_id: str) -> RunLedger | None: ...

    def update(self, run: RunLedger) -> RunLedger: ...

    def list_recent(self, workspace_id: str, *, limit: int = 20) -> list[RunLedger]: ...

    def add_stage(self, stage: StageRecord) -> StageRecord: ...

    def update_stage(self, stage: StageRecord) -> StageRecord: ...

    def list_stages(self, workspace_id: str, run_id: str) -> list[StageRecord]: ...


@runtime_checkable
class UnitOfWork(Protocol):
    """Transactional boundary.

    A pipeline run either records its work coherently or not at all; partial
    ledger state is worse than none. Used as a context manager, committing on
    clean exit and rolling back on exception.

    The repositories are read-only properties rather than plain attributes
    deliberately: mutable protocol attributes are *invariant*, which would force
    every implementation to annotate its repositories with the abstract port
    type instead of its own concrete class. Properties are covariant, so a
    concrete adapter satisfies the protocol structurally with no annotation
    gymnastics.
    """

    @property
    def accounts(self) -> AccountRepository: ...

    @property
    def actors(self) -> ActorRepository: ...

    @property
    def workspaces(self) -> WorkspaceRepository: ...

    @property
    def sources(self) -> SourceRepository: ...

    @property
    def documents(self) -> SourceDocumentRepository: ...

    @property
    def runs(self) -> RunRepository: ...

    @property
    def signals(self) -> SignalRepository: ...

    def __enter__(self) -> UnitOfWork: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...
