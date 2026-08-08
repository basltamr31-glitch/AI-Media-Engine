"""Publications and performance metrics: the feedback loop's storage.

Both tables are append-only by design. ``publications`` records that content
went live -- the engine records publication rather than performing it
(ADR 0018) -- and ``performance_metrics`` records what it achieved, one
observation per row so a series survives rather than a single overwritten
total.

Revision ID: 179e96806dd8
Revises: 5b2067976336
Create Date: 2026-08-08 14:13:55.883008

"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = '179e96806dd8'
down_revision: str | None = '5b2067976336'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('publications',
    sa.Column('id', sa.String(length=40), nullable=False),
    sa.Column('workspace_id', sa.String(length=40), nullable=False),
    sa.Column('asset_id', sa.String(length=40), nullable=False),
    sa.Column('channel_key', sa.String(length=64), nullable=False),
    sa.Column('external_url', sa.Text(), nullable=True),
    sa.Column('external_id', sa.String(length=255), nullable=True),
    sa.Column('published_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('published_by', sa.String(length=32), nullable=False),
    sa.ForeignKeyConstraint(['asset_id'], ['content_assets.id'], name=op.f('fk_publications_asset_id_content_assets'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], name=op.f('fk_publications_workspace_id_workspaces'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_publications')),
    sa.UniqueConstraint('asset_id', 'channel_key', name='uq_publications_asset_id_channel_key')
    )
    with op.batch_alter_table('publications', schema=None) as batch_op:
        batch_op.create_index('ix_publications_workspace_id_published_at', ['workspace_id', 'published_at'], unique=False)

    op.create_table('performance_metrics',
    sa.Column('id', sa.String(length=40), nullable=False),
    sa.Column('workspace_id', sa.String(length=40), nullable=False),
    sa.Column('publication_id', sa.String(length=40), nullable=False),
    sa.Column('metric', sa.String(length=64), nullable=False),
    sa.Column('value', sa.Float(), nullable=False),
    sa.Column('captured_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('collected_by', sa.String(length=32), nullable=False),
    sa.ForeignKeyConstraint(['publication_id'], ['publications.id'], name=op.f('fk_performance_metrics_publication_id_publications'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], name=op.f('fk_performance_metrics_workspace_id_workspaces'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_performance_metrics')),
    sa.UniqueConstraint('publication_id', 'metric', 'captured_at', name='uq_performance_metrics_publication_metric_captured')
    )
    with op.batch_alter_table('performance_metrics', schema=None) as batch_op:
        batch_op.create_index('ix_performance_metrics_workspace_id_captured_at', ['workspace_id', 'captured_at'], unique=False)
        batch_op.create_index('ix_performance_metrics_workspace_id_publication_id', ['workspace_id', 'publication_id'], unique=False)



def downgrade() -> None:
    with op.batch_alter_table('performance_metrics', schema=None) as batch_op:
        batch_op.drop_index('ix_performance_metrics_workspace_id_publication_id')
        batch_op.drop_index('ix_performance_metrics_workspace_id_captured_at')

    op.drop_table('performance_metrics')
    with op.batch_alter_table('publications', schema=None) as batch_op:
        batch_op.drop_index('ix_publications_workspace_id_published_at')

    op.drop_table('publications')
