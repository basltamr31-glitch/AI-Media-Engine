"""Workspace -- the tenant root.

Every other entity hangs off a workspace. Today there is exactly one; the shape
costs nothing now and is the difference between "add a customer" and "rewrite
the data layer" later.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Final

from pydantic import Field, field_validator

from engine.core.domain.base import DomainModel, utcnow
from engine.core.domain.enums import Language
from engine.core.domain.ids import IdPrefix, new_id

_SLUG_PATTERN: Final = re.compile(r"^[a-z0-9][a-z0-9-]{1,62}[a-z0-9]$")


class Workspace(DomainModel):
    """An isolated tenant: its own sources, policies, brand voices and content."""

    id: str = Field(default_factory=lambda: new_id(IdPrefix.WORKSPACE))
    slug: str = Field(description="URL-safe stable key, unique across the install")
    name: str
    timezone: str = Field(
        default="UTC",
        description="IANA timezone. Drives publishing windows and daily boundaries.",
    )
    languages: list[Language] = Field(
        default_factory=lambda: [Language.AR, Language.EN],
        min_length=1,
        description="Languages this workspace produces, in priority order.",
    )
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

    @field_validator("languages")
    @classmethod
    def _validate_languages(cls, value: list[Language]) -> list[Language]:
        if len(set(value)) != len(value):
            msg = "languages must not contain duplicates"
            raise ValueError(msg)
        return value

    def touch(self) -> None:
        """Mark the workspace as modified now."""
        self.updated_at = utcnow()
