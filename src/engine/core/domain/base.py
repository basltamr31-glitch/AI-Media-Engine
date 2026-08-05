"""Shared base classes for domain models.

All domain entities are Pydantic models, which buys three things at once:

1. Runtime validation at every boundary.
2. JSON serialisation for the file outputs and, later, the HTTP API.
3. Schemas that can be handed directly to an LLM as a structured-output contract,
   so generated content is schema-valid by construction rather than by parsing.
"""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict


def utcnow() -> datetime:
    """Return the current time as a timezone-aware UTC datetime.

    Naive datetimes are banned throughout (see the ruff ``DTZ`` rules): a
    scheduled, multi-timezone content pipeline that loses offsets will publish
    at the wrong hour, and that class of bug is invisible in tests.
    """
    return datetime.now(UTC)


class DomainModel(BaseModel):
    """Base for mutable domain entities."""

    model_config = ConfigDict(
        extra="forbid",  # typos become errors, not silently dropped fields
        validate_assignment=True,  # invariants hold after mutation, not just at construction
        str_strip_whitespace=True,
        use_enum_values=False,
        frozen=False,
    )


class ImmutableModel(BaseModel):
    """Base for value objects and append-only records.

    Used for anything that represents "what happened": fetched documents,
    evidence, scores. Immutability is what makes the provenance ledger
    trustworthy -- a record that can be edited is not an audit trail.
    """

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        use_enum_values=False,
        frozen=True,
    )


class TenantScoped(DomainModel):
    """Base for every entity that belongs to a workspace.

    Multi-tenancy is structural from day one. Carrying ``workspace_id`` on the
    entity (not just the query) means a tenant boundary can be asserted in the
    domain layer, and the single-tenant-to-SaaS transition is a deployment
    change rather than a schema migration.
    """

    workspace_id: str
