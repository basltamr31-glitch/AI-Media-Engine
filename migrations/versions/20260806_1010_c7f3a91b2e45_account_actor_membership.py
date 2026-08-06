"""Add the account, actor and membership layer.

Revision ID: c7f3a91b2e45
Revises: b2c41f7d9e33
Create Date: 2026-08-06 10:10:00.000000

ADR 0011. A workspace is an editorial context; an account is the commercial one.
An agency's 80 brands are 80 workspaces under a single account.

Carries a **data step**: `workspaces.account_id` is NOT NULL, and workspaces
already exist. The column is added nullable, backfilled to a `default` account,
then tightened -- the standard three-step for a required column on a populated
table. Doing it in one step would fail on any database with rows.
"""

from __future__ import annotations

from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

revision = "c7f3a91b2e45"
down_revision = "b2c41f7d9e33"
branch_labels = None
depends_on = None

ID_LENGTH = 40

# Deterministic ids for the seeded rows. Fixed rather than generated so the
# migration is reproducible and so a downgrade can target them precisely.
DEFAULT_ACCOUNT_ID = "acc_00000000000000000000000000"
SYSTEM_ACTOR_ID = "act_00000000000000000000000000"
DEFAULT_MEMBERSHIP_ID = "mbr_00000000000000000000000000"

DEFAULT_QUOTA = (
    '{"max_workspaces": 1000, "max_sources_per_workspace": 1000, '
    '"max_runs_per_day": 1440, "max_cost_usd_per_month": 100000.0, '
    '"max_documents_per_month": 10000000}'
)


def upgrade() -> None:
    op.create_table(
        "accounts",
        sa.Column("id", sa.String(ID_LENGTH), nullable=False),
        sa.Column("slug", sa.String(64), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("plan", sa.String(32), nullable=False),
        sa.Column("quota", sa.JSON(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("meta", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_accounts"),
        sa.UniqueConstraint("slug", name="uq_accounts_slug"),
    )

    op.create_table(
        "actors",
        sa.Column("id", sa.String(ID_LENGTH), nullable=False),
        sa.Column("account_id", sa.String(ID_LENGTH), nullable=False),
        sa.Column("key", sa.String(64), nullable=False),
        sa.Column("display_name", sa.String(255), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("email", sa.String(320), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("meta", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["accounts.id"],
            name="fk_actors_account_id_accounts",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_actors"),
        sa.UniqueConstraint("account_id", "key", name="uq_actors_account_id_key"),
    )
    with op.batch_alter_table("actors", schema=None) as batch_op:
        batch_op.create_index("ix_actors_account_id", ["account_id"], unique=False)

    op.create_table(
        "memberships",
        sa.Column("id", sa.String(ID_LENGTH), nullable=False),
        sa.Column("account_id", sa.String(ID_LENGTH), nullable=False),
        sa.Column("actor_id", sa.String(ID_LENGTH), nullable=False),
        sa.Column("role", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["accounts.id"],
            name="fk_memberships_account_id_accounts",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["actor_id"],
            ["actors.id"],
            name="fk_memberships_actor_id_actors",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_memberships"),
        sa.UniqueConstraint("account_id", "actor_id", name="uq_memberships_account_id_actor_id"),
    )
    with op.batch_alter_table("memberships", schema=None) as batch_op:
        batch_op.create_index("ix_memberships_actor_id", ["actor_id"], unique=False)

    _seed_default_account()
    _add_workspace_account_id()


def _seed_default_account() -> None:
    """Create the account and system actor that own everything built so far."""
    now = datetime.now(UTC)
    connection = op.get_bind()

    connection.execute(
        sa.text(
            """
            INSERT INTO accounts
                (id, slug, name, plan, quota, is_active, created_at, updated_at, meta)
            VALUES
                (:id, 'default', 'Default Account', 'enterprise', :quota, 1, :now, :now, '{}')
            """
        ),
        {"id": DEFAULT_ACCOUNT_ID, "quota": DEFAULT_QUOTA, "now": now},
    )

    # Every automated action needs an attributable origin. Without this row,
    # signals emitted by the pipeline would have a null actor and the audit
    # trail would have a hole exactly where unattended work happens.
    connection.execute(
        sa.text(
            """
            INSERT INTO actors
                (id, account_id, key, display_name, kind, email, is_active, created_at, meta)
            VALUES
                (:id, :account_id, 'system', 'System', 'system', NULL, 1, :now, '{}')
            """
        ),
        {"id": SYSTEM_ACTOR_ID, "account_id": DEFAULT_ACCOUNT_ID, "now": now},
    )

    connection.execute(
        sa.text(
            """
            INSERT INTO memberships (id, account_id, actor_id, role, created_at)
            VALUES (:id, :account_id, :actor_id, 'owner', :now)
            """
        ),
        {
            "id": DEFAULT_MEMBERSHIP_ID,
            "account_id": DEFAULT_ACCOUNT_ID,
            "actor_id": SYSTEM_ACTOR_ID,
            "now": now,
        },
    )


def _add_workspace_account_id() -> None:
    """Add the required column in three steps, so existing rows survive."""
    with op.batch_alter_table("workspaces", schema=None) as batch_op:
        batch_op.add_column(sa.Column("account_id", sa.String(ID_LENGTH), nullable=True))

    op.get_bind().execute(
        sa.text("UPDATE workspaces SET account_id = :account_id WHERE account_id IS NULL"),
        {"account_id": DEFAULT_ACCOUNT_ID},
    )

    with op.batch_alter_table("workspaces", schema=None) as batch_op:
        batch_op.alter_column("account_id", existing_type=sa.String(ID_LENGTH), nullable=False)
        batch_op.create_foreign_key(
            "fk_workspaces_account_id_accounts",
            "accounts",
            ["account_id"],
            ["id"],
            ondelete="CASCADE",
        )
        batch_op.create_index("ix_workspaces_account_id", ["account_id"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("workspaces", schema=None) as batch_op:
        batch_op.drop_index("ix_workspaces_account_id")
        batch_op.drop_constraint("fk_workspaces_account_id_accounts", type_="foreignkey")
        batch_op.drop_column("account_id")

    with op.batch_alter_table("memberships", schema=None) as batch_op:
        batch_op.drop_index("ix_memberships_actor_id")
    op.drop_table("memberships")

    with op.batch_alter_table("actors", schema=None) as batch_op:
        batch_op.drop_index("ix_actors_account_id")
    op.drop_table("actors")

    op.drop_table("accounts")
