"""Account, Actor and Membership -- the commercial and identity layer.

ADR 0011. Three entities that sit above and beside the workspace:

* :class:`Account` owns many workspaces. It is the billing, quota and plan root.
  A marketing agency with 80 brands is one account, not 80 tenants.
* :class:`Actor` is anyone or anything that takes an action -- a human operator,
  the scheduler, the pipeline itself. Every action is attributable.
* :class:`Membership` links an actor to an account with a role.

The workspace remains the *editorial* context (voice, sources, policy); the
account is the *commercial* context (billing, quota, people). Overloading one
entity with both is what this separation avoids.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Final

from pydantic import Field, field_validator

from engine.core.domain.base import DomainModel, utcnow
from engine.core.domain.enums import ActorKind, MembershipRole, PlanTier
from engine.core.domain.ids import IdPrefix, new_id

_SLUG_PATTERN: Final = re.compile(r"^[a-z0-9][a-z0-9-]{1,62}[a-z0-9]$")

#: Slug of the account created by the migration to own pre-existing workspaces.
DEFAULT_ACCOUNT_SLUG: Final = "default"

#: Every automated action is attributed to this actor rather than to null, so
#: "who did this?" always has an answer.
SYSTEM_ACTOR_KEY: Final = "system"


class Quota(DomainModel):
    """Usage ceilings for an account.

    Present before billing exists because metering must precede monetisation:
    you cannot price a product whose consumption you do not measure. Enforcement
    arrives with the API in Phase 7; recording starts now.
    """

    max_workspaces: int = Field(default=5, ge=1)
    max_sources_per_workspace: int = Field(default=25, ge=1)
    max_runs_per_day: int = Field(default=48, ge=1)
    max_cost_usd_per_month: float = Field(default=50.0, gt=0)
    max_documents_per_month: int = Field(default=10_000, ge=1)

    @classmethod
    def unlimited(cls) -> Quota:
        """Quota for self-hosted and development use."""
        return cls(
            max_workspaces=1_000,
            max_sources_per_workspace=1_000,
            max_runs_per_day=1_440,
            max_cost_usd_per_month=100_000.0,
            max_documents_per_month=10_000_000,
        )


class Account(DomainModel):
    """The billing and quota root. Owns many workspaces."""

    id: str = Field(default_factory=lambda: new_id(IdPrefix.ACCOUNT))
    slug: str = Field(description="URL-safe stable key, unique across the install.")
    name: str
    plan: PlanTier = PlanTier.FREE
    quota: Quota = Field(default_factory=Quota)
    is_active: bool = True
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    meta: dict[str, Any] = Field(default_factory=dict)

    @field_validator("slug")
    @classmethod
    def _validate_slug(cls, value: str) -> str:
        slug = value.strip().lower()
        if not _SLUG_PATTERN.match(slug):
            msg = (
                "slug must be 3-64 characters of lowercase letters, digits or "
                f"hyphens, and start/end alphanumeric; got {value!r}"
            )
            raise ValueError(msg)
        return slug

    def touch(self) -> None:
        self.updated_at = utcnow()


class Actor(DomainModel):
    """Whoever -- or whatever -- took an action.

    Actors are account-scoped rather than global: the same person working for two
    agencies is two actors, because their editorial preferences differ per
    account and preference learning is per-actor.
    """

    id: str = Field(default_factory=lambda: new_id(IdPrefix.ACTOR))
    account_id: str
    key: str = Field(description="Stable lookup key, unique within the account.")
    display_name: str
    kind: ActorKind = ActorKind.HUMAN
    email: str | None = None
    is_active: bool = True
    created_at: datetime = Field(default_factory=utcnow)
    meta: dict[str, Any] = Field(default_factory=dict)

    @field_validator("key")
    @classmethod
    def _validate_key(cls, value: str) -> str:
        key = value.strip().lower()
        if not key:
            msg = "actor key must not be empty"
            raise ValueError(msg)
        return key

    @property
    def is_system(self) -> bool:
        """True for the automated actor that owns unattended pipeline actions."""
        return self.kind is not ActorKind.HUMAN


class Membership(DomainModel):
    """Links an actor to an account with a role."""

    id: str = Field(default_factory=lambda: new_id(IdPrefix.MEMBERSHIP))
    account_id: str
    actor_id: str
    role: MembershipRole = MembershipRole.EDITOR
    created_at: datetime = Field(default_factory=utcnow)

    @property
    def can_approve(self) -> bool:
        """Whether this role may move content to APPROVED.

        Authorisation is not enforced anywhere yet -- there is no API and one
        operator. The predicate lives here so that when enforcement arrives it
        has a single home rather than being scattered through handlers.
        """
        return self.role in {MembershipRole.OWNER, MembershipRole.ADMIN, MembershipRole.EDITOR}
