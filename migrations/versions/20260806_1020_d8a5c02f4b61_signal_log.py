"""Add the append-only signal log.

Revision ID: d8a5c02f4b61
Revises: c7f3a91b2e45
Create Date: 2026-08-06 10:20:00.000000

ADR 0012. One table for every human decision and every measured outcome, rather
than one table per feedback type -- so a new learning loop costs a config entry
instead of a migration.

Four indexes, each earning its place against a named query:

* ``(workspace_id, subject_type, subject_id)`` -- "everything that happened to
  this story", the dominant read in review and debugging.
* ``(workspace_id, signal_type, occurred_at)`` -- the learning phase pulls one
  signal type across a time range.
* ``(account_id, occurred_at)`` -- cross-brand analytics and usage metering.
* ``run_id`` -- "what did this run record?", used by the funnel view.

No update or delete path is provided. Immutability is what makes the log
usable as both a training set and an audit trail.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "d8a5c02f4b61"
down_revision = "c7f3a91b2e45"
branch_labels = None
depends_on = None

ID_LENGTH = 40


def upgrade() -> None:
    op.create_table(
        "signals",
        sa.Column("id", sa.String(ID_LENGTH), nullable=False),
        sa.Column("account_id", sa.String(ID_LENGTH), nullable=False),
        sa.Column("workspace_id", sa.String(ID_LENGTH), nullable=False),
        sa.Column("actor_id", sa.String(ID_LENGTH), nullable=False),
        sa.Column("subject_type", sa.String(32), nullable=False),
        sa.Column("subject_id", sa.String(ID_LENGTH), nullable=False),
        sa.Column("signal_type", sa.String(64), nullable=False),
        sa.Column("polarity", sa.String(16), nullable=False),
        sa.Column("value", sa.Float(), nullable=True),
        sa.Column("label", sa.String(255), nullable=True),
        sa.Column("context", sa.JSON(), nullable=False),
        sa.Column("run_id", sa.String(ID_LENGTH), nullable=True),
        # Deliberately two timestamps: engagement is pulled hours after it
        # happened, and collapsing them would corrupt every timing analysis.
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name="fk_signals_workspace_id_workspaces",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_signals"),
    )

    with op.batch_alter_table("signals", schema=None) as batch_op:
        batch_op.create_index(
            "ix_signals_workspace_id_subject_type_subject_id",
            ["workspace_id", "subject_type", "subject_id"],
            unique=False,
        )
        batch_op.create_index(
            "ix_signals_workspace_id_signal_type_occurred_at",
            ["workspace_id", "signal_type", "occurred_at"],
            unique=False,
        )
        batch_op.create_index(
            "ix_signals_account_id_occurred_at",
            ["account_id", "occurred_at"],
            unique=False,
        )
        batch_op.create_index("ix_signals_run_id", ["run_id"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("signals", schema=None) as batch_op:
        batch_op.drop_index("ix_signals_run_id")
        batch_op.drop_index("ix_signals_account_id_occurred_at")
        batch_op.drop_index("ix_signals_workspace_id_signal_type_occurred_at")
        batch_op.drop_index("ix_signals_workspace_id_subject_type_subject_id")
    op.drop_table("signals")
