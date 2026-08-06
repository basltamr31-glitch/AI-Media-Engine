"""Add the active domain pack to workspaces.

Revision ID: e1b7d43a9c22
Revises: d8a5c02f4b61
Create Date: 2026-08-06 14:00:00.000000

ADR 0014. A workspace selects which domain pack supplies its taxonomy and
vocabulary. Existing workspaces are AI news, so they backfill to ``ai-news``.

Added with a server default so the column can be NOT NULL in one step on a
populated table; the default is then dropped, because the application always
supplies the value and a lingering database default would silently mask a bug
where it does not.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "e1b7d43a9c22"
down_revision = "d8a5c02f4b61"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("workspaces", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("pack_key", sa.String(64), nullable=False, server_default="ai-news")
        )

    with op.batch_alter_table("workspaces", schema=None) as batch_op:
        batch_op.alter_column("pack_key", server_default=None)


def downgrade() -> None:
    with op.batch_alter_table("workspaces", schema=None) as batch_op:
        batch_op.drop_column("pack_key")
