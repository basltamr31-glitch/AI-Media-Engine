"""Account provisioning shared by the CLI commands.

The CLI is the application layer for now (ADR 0002), so the "ensure an account
and its system actor exist" use case lives here rather than in a service layer
that would otherwise contain one function.

Every account gets a ``system`` actor at creation. Automated pipeline actions
are attributed to it rather than to null, so the signal log and audit trail have
no holes exactly where unattended work happens.
"""

from __future__ import annotations

from engine.adapters.persistence.unit_of_work import SqlAlchemyUnitOfWork
from engine.core.domain.account import (
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

    actor = uow.actors.get_by_key(account.id, SYSTEM_ACTOR_KEY)
    if actor is None:
        actor = Actor(
            account_id=account.id,
            key=SYSTEM_ACTOR_KEY,
            display_name="System",
            kind=ActorKind.SYSTEM,
            created_at=clock.now(),
        )
        uow.actors.add(actor)
        uow.actors.add_membership(
            Membership(
                account_id=account.id,
                actor_id=actor.id,
                role=MembershipRole.OWNER,
                created_at=clock.now(),
            )
        )

    return account, actor


def system_actor(uow: SqlAlchemyUnitOfWork, account_id: str) -> Actor:
    """Fetch an account's system actor.

    Raises:
        LookupError: If it is missing. That indicates a provisioning bug rather
            than a user error, so it fails loudly instead of silently creating
            a second actor and splitting the audit trail.
    """
    actor = uow.actors.get_by_key(account_id, SYSTEM_ACTOR_KEY)
    if actor is None:
        msg = f"account {account_id} has no system actor; run: engine workspace seed"
        raise LookupError(msg)
    return actor
