"""File-format schemas for policy configuration.

These describe the *YAML shape*, which is an adapter concern, and are kept out
of the domain so the on-disk format can change without touching business logic.

Policy lives in files rather than code because onboarding a second workspace --
the first real step toward revenue -- must not require a deployment.
"""

from __future__ import annotations

from typing import Any

from pydantic import Field

from engine.core.domain.base import DomainModel
from engine.core.domain.enums import Language, SourceKind
from engine.core.domain.policy import EditorialPolicy


class SourceConfig(DomainModel):
    """One configured input feed, as declared in a workspace file."""

    kind: SourceKind
    name: str
    identifier: str
    parser_key: str | None = None
    trust_weight: float = Field(default=0.5, ge=0.0, le=1.0)
    is_active: bool = True
    config: dict[str, Any] = Field(default_factory=dict)


class WorkspaceConfig(DomainModel):
    """Declarative definition of a workspace and everything it needs to run.

    This file *is* the tenant definition. Seeding is idempotent, so the file
    stays the source of truth and the database is a materialised view of it.
    """

    slug: str
    name: str
    account: str | None = Field(
        default=None,
        description=(
            "Owning account slug (ADR 0011). Defaults to the configured account. "
            "An agency points 80 workspace files at one account slug."
        ),
    )
    pack: str = Field(
        default="ai-news",
        description="Domain pack supplying taxonomy and vocabulary (ADR 0014).",
    )
    timezone: str = "UTC"
    languages: list[Language] = Field(default_factory=lambda: [Language.AR, Language.EN])
    sources: list[SourceConfig] = Field(default_factory=list)
    editorial: EditorialPolicy = Field(default_factory=EditorialPolicy)
    brand_profiles: list[str] = Field(
        default_factory=list,
        description="Keys of brand profile files this workspace uses.",
    )
    meta: dict[str, Any] = Field(default_factory=dict)
