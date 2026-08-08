"""The provenance ledger: which sources support which claim.

Revision ID: b1a48bf5b3e2
Revises: 975dd58c457c
Create Date: 2026-08-07 11:33:00.000000

Phase 2.5. Unique on ``(story_id, kind, source_url)`` because evidence is
*reconciled* on every run rather than appended: the constraint is what makes a
repeated run a no-op instead of a growing pile of identical rows.

``ON DELETE CASCADE`` from stories: evidence supports a specific story and has
no meaning without it. Discarding stories to re-cluster (``engine stories
reset``) therefore discards their evidence too, which is correct -- it is
rebuilt by the next evidence run from the mentions, which are never deleted.
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = 'b1a48bf5b3e2'
down_revision: str | None = '975dd58c457c'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('evidence',
    sa.Column('id', sa.String(length=40), nullable=False),
    sa.Column('workspace_id', sa.String(length=40), nullable=False),
    sa.Column('story_id', sa.String(length=40), nullable=False),
    sa.Column('claim', sa.Text(), nullable=False),
    sa.Column('kind', sa.String(length=32), nullable=False),
    sa.Column('source_url', sa.String(length=2048), nullable=False),
    sa.Column('source_title', sa.String(length=1024), nullable=True),
    sa.Column('quote', sa.Text(), nullable=True),
    sa.Column('captured_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['story_id'], ['stories.id'], name=op.f('fk_evidence_story_id_stories'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], name=op.f('fk_evidence_workspace_id_workspaces'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_evidence')),
    sa.UniqueConstraint('story_id', 'kind', 'source_url', name='uq_evidence_story_id_kind_source_url')
    )
    with op.batch_alter_table('evidence', schema=None) as batch_op:
        batch_op.create_index('ix_evidence_workspace_id_kind', ['workspace_id', 'kind'], unique=False)
        batch_op.create_index('ix_evidence_workspace_id_story_id', ['workspace_id', 'story_id'], unique=False)



def downgrade() -> None:
    with op.batch_alter_table('evidence', schema=None) as batch_op:
        batch_op.drop_index('ix_evidence_workspace_id_story_id')
        batch_op.drop_index('ix_evidence_workspace_id_kind')

    op.drop_table('evidence')
