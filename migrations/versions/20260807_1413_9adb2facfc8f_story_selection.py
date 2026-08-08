"""When editorial chose a story for production.

Revision ID: 9adb2facfc8f
Revises: be0f54ef190a
Create Date: 2026-08-07 14:13:00.000000

Phase 3.2. A nullable timestamp rather than a status flag alone: the selection
quota is counted over a *window*, so "what did we choose today?" has to be an
indexed range query. It is asked on every run from here to publication.

Nullable and never reset. A story is selected at most once; missing today's cut
is not recorded, because a story that missed it may be exactly right tomorrow
and a "rejected" marker would make that unreachable.
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = '9adb2facfc8f'
down_revision: str | None = 'be0f54ef190a'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table('stories', schema=None) as batch_op:
        batch_op.add_column(sa.Column('selected_at', sa.DateTime(timezone=True), nullable=True))
        batch_op.create_index('ix_stories_workspace_id_selected_at', ['workspace_id', 'selected_at'], unique=False)



def downgrade() -> None:
    with op.batch_alter_table('stories', schema=None) as batch_op:
        batch_op.drop_index('ix_stories_workspace_id_selected_at')
        batch_op.drop_column('selected_at')

