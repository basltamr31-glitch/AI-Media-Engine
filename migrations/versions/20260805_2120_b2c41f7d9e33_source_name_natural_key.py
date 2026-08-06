"""Make source name the natural key within a workspace.

Revision ID: b2c41f7d9e33
Revises: 58e9aba83736
Create Date: 2026-08-05 21:20:00.000000

Phase 1 discovered that keying sources on ``(workspace_id, kind, identifier)``
is wrong in two directions:

* An identifier is **mutable** -- arXiv moved from ``http`` to ``https``. Keyed
  on identifier, re-seeding forks a second source and abandons the original's
  ingested documents.
* The same endpoint legitimately appears **twice** under different names: one
  GitHub releases URL filtered two ways, one Hacker News query at two attention
  thresholds. Uniqueness on identifier forbids a real use case.

The human-facing name is the stable key, and it must be unique because the run
ledger reports per-source statistics keyed by name -- a collision silently drops
one source's numbers from the report.

This migration carries a **data step**: any pre-existing duplicate names are
retired rather than deleted. Their documents are evidence and are never
discarded for a schema change.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "b2c41f7d9e33"
down_revision = "58e9aba83736"
branch_labels = None
depends_on = None


def _retire_duplicate_names() -> None:
    """Rename duplicate source names so the unique constraint can be applied.

    For each ``(workspace_id, name)`` group, the row carrying the most ingested
    documents wins the name (ties broken toward the oldest, since ULID ids sort
    by creation time). Losers are deactivated and suffixed with their id, which
    keeps them inspectable and keeps their documents reachable.
    """
    connection = op.get_bind()

    duplicates = connection.execute(
        sa.text(
            """
            SELECT workspace_id, name
            FROM sources
            GROUP BY workspace_id, name
            HAVING COUNT(*) > 1
            """
        )
    ).fetchall()

    for workspace_id, name in duplicates:
        rows = connection.execute(
            sa.text(
                """
                SELECT s.id,
                       (SELECT COUNT(*) FROM source_documents d WHERE d.source_id = s.id)
                           AS document_count
                FROM sources s
                WHERE s.workspace_id = :workspace_id AND s.name = :name
                ORDER BY document_count DESC, s.id ASC
                """
            ),
            {"workspace_id": workspace_id, "name": name},
        ).fetchall()

        for source_id, _ in rows[1:]:
            connection.execute(
                sa.text(
                    """
                    UPDATE sources
                    SET name = :new_name, is_active = 0
                    WHERE id = :source_id
                    """
                ),
                {
                    # Short id suffix keeps the retired row identifiable without
                    # exceeding the 255-character column.
                    "new_name": f"{name[:220]} (retired {source_id[-8:]})",
                    "source_id": source_id,
                },
            )


def upgrade() -> None:
    _retire_duplicate_names()

    # batch_alter_table rebuilds the table, which is how SQLite alters
    # constraints at all. It works because Phase 0 established an explicit
    # constraint naming convention.
    with op.batch_alter_table("sources", schema=None) as batch_op:
        batch_op.drop_constraint("uq_sources_workspace_id_kind_identifier", type_="unique")
        batch_op.create_unique_constraint("uq_sources_workspace_id_name", ["workspace_id", "name"])
        batch_op.create_index(
            "ix_sources_workspace_id_identifier",
            ["workspace_id", "identifier"],
            unique=False,
        )


def downgrade() -> None:
    # Note: the data step is not reversed. Retired duplicates keep their
    # suffixed names, because restoring them would immediately violate the
    # constraint this downgrade re-creates.
    with op.batch_alter_table("sources", schema=None) as batch_op:
        batch_op.drop_index("ix_sources_workspace_id_identifier")
        batch_op.drop_constraint("uq_sources_workspace_id_name", type_="unique")
        batch_op.create_unique_constraint(
            "uq_sources_workspace_id_kind_identifier",
            ["workspace_id", "kind", "identifier"],
        )
