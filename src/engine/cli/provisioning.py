"""Account provisioning shared by the CLI commands.

The CLI is the application layer for now (ADR 0002), so the "ensure an account
and its actors exist" use case lives here rather than in a service layer that
would otherwise contain one function.

Every account gets two actors at creation: ``system``, which automated pipeline
actions are attributed to rather than to null, and ``owner``, the human whose
review decisions and edits are attributed to a person. Both exist from the
start so the signal log and audit trail have no holes exactly where unattended
work happens, and so a human edit is never mistaken for machine output.
"""

from __future__ import annotations

from engine.adapters.persistence.unit_of_work import SqlAlchemyUnitOfWork
from engine.core.domain.account import (
    OWNER_ACTOR_KEY,
    SYSTEM_ACTOR_KEY,
    Account,
    Actor,
    Membership,
    Quota,
)
from engine.core.domain.enums import ActorKind, MembershipRole, PlanTier
from engine.core.ports.clock import Clock


def ensure_account(
    uow: SqlAlchemyUnitOfWork,
    *,
    slug: str,
    name: str | None = None,
    clock: Clock,
) -> tuple[Account, Actor]:
    """Return the account for ``slug``, creating it and its system actor if absent.

    Idempotent: safe to call on every seed, exactly like source reconciliation.
    Must be called inside an active unit of work; the caller owns the commit.
    """
    account = uow.accounts.get_by_slug(slug)
    if account is None:
        now = clock.now()
        account = Account(
            slug=slug,
            name=name or slug.replace("-", " ").title(),
            # Self-hosted installs are not quota-limited. Enforcement arrives
            # with the hosted API in Phase 7; the ceiling is recorded now so
            # metering has somewhere to compare against.
            plan=PlanTier.ENTERPRISE,
            quota=Quota.unlimited(),
            created_at=now,
            updated_at=now,
        )
        uow.accounts.add(account)

    actor = _ensure_actor(
        uow,
        account_id=account.id,
        key=SYSTEM_ACTOR_KEY,
        display_name="System",
        kind=ActorKind.SYSTEM,
        clock=clock,
    )
    # The human owner, provisioned alongside. Review decisions must attribute
    # to a person: the difference between a machine draft and a human rewrite
    # is the dataset the revision chain exists to build, and attributing an
    # edit to the system actor would erase it at the point of capture.
    _ensure_actor(
        uow,
        account_id=account.id,
        key=OWNER_ACTOR_KEY,
        display_name=account.name,
        kind=ActorKind.HUMAN,
        clock=clock,
    )

    return account, actor


def _ensure_actor(
    uow: SqlAlchemyUnitOfWork,
    *,
    account_id: str,
    key: str,
    display_name: str,
    kind: ActorKind,
    clock: Clock,
) -> Actor:
    """Return the actor for ``key``, creating it and its membership if absent."""
    actor = uow.actors.get_by_key(account_id, key)
    if actor is not None:
        return actor

    actor = Actor(
        account_id=account_id,
        key=key,
        display_name=display_name,
        kind=kind,
        created_at=clock.now(),
    )
    uow.actors.add(actor)
    uow.actors.add_membership(
        Membership(
            account_id=account_id,
            actor_id=actor.id,
            role=MembershipRole.OWNER,
            created_at=clock.now(),
        )
    )
    return actor


def system_actor(uow: SqlAlchemyUnitOfWork, account_id: str) -> Actor:
    """Fetch an account's system actor.

    Raises:
        LookupError: If it is missing. That indicates a provisioning bug rather
            than a user error, so it fails loudly instead of silently creating
            a second actor and splitting the audit trail.
    """
    return actor_by_key(uow, account_id, SYSTEM_ACTOR_KEY)


def actor_by_key(uow: SqlAlchemyUnitOfWork, account_id: str, key: str) -> Actor:
    """Fetch an actor by its stable key.

    Raises:
        LookupError: If no actor holds that key. Conjuring one from a CLI flag
            would let a typo become a second identity, and an audit trail whose
            actors are created by misspelling is not one.
    """
    actor = uow.actors.get_by_key(account_id, key)
    if actor is None:
        msg = (
            f"account {account_id} has no actor {key!r}; "
            "run: engine workspace seed, or pass --as with an existing actor key"
        )
        raise LookupError(msg)
    return actor
