"""001_initial_foundation

Revision ID: 001_initial_foundation
Revises: 
Create Date: 2026-09-26 00:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "001_initial_foundation"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Create Enums
    user_role_enum = postgresql.ENUM(
        "REQUESTER", "AGENT_OPERATOR", "VALIDATOR", "ADMIN", name="user_role"
    )
    user_role_enum.create(op.get_bind(), checkfirst=True)

    task_status_enum = postgresql.ENUM(
        "DRAFT",
        "PLANNING",
        "PENDING_DEPOSIT",
        "ACTIVE",
        "VERIFYING",
        "COMPLETED",
        "DISPUTED",
        "CANCELLED",
        "FAILED",
        name="task_status",
    )
    task_status_enum.create(op.get_bind(), checkfirst=True)

    # 2. Create Users Table
    op.create_table(
        "users",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("wallet_address", sa.String(length=42), nullable=False),
        sa.Column(
            "role",
            postgresql.ENUM(
                "REQUESTER", "AGENT_OPERATOR", "VALIDATOR", "ADMIN", name="user_role", create_type=False
            ),
            nullable=False,
        ),
        sa.Column("nonce", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("wallet_address"),
    )
    op.create_index("idx_users_wallet", "users", ["wallet_address"])

    # 3. Create Agents Table
    op.create_table(
        "agents",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("operator_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("onchain_agent_id", sa.BigInteger(), nullable=True),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("reputation_score", sa.Numeric(precision=10, scale=4), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["operator_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("onchain_agent_id"),
    )
    op.create_index("idx_agents_operator", "agents", ["operator_id"])
    op.create_index("idx_agents_reputation", "agents", ["reputation_score"])

    # 4. Create Tasks Table
    op.create_table(
        "tasks",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("requester_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("onchain_task_id", sa.BigInteger(), nullable=True),
        sa.Column("title", sa.String(length=256), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column(
            "status",
            postgresql.ENUM(
                "DRAFT",
                "PLANNING",
                "PENDING_DEPOSIT",
                "ACTIVE",
                "VERIFYING",
                "COMPLETED",
                "DISPUTED",
                "CANCELLED",
                "FAILED",
                name="task_status",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column("budget_usdc", sa.Numeric(precision=20, scale=6), nullable=False),
        sa.Column("platform_fee_usdc", sa.Numeric(precision=20, scale=6), nullable=False),
        sa.Column("escrow_tx_hash", sa.String(length=66), nullable=True),
        sa.Column("result_hash", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["requester_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("onchain_task_id"),
    )
    op.create_index("idx_tasks_requester", "tasks", ["requester_id"])
    op.create_index("idx_tasks_status", "tasks", ["status"])


def downgrade() -> None:
    op.drop_index("idx_tasks_status", table_name="tasks")
    op.drop_index("idx_tasks_requester", table_name="tasks")
    op.drop_table("tasks")

    op.drop_index("idx_agents_reputation", table_name="agents")
    op.drop_index("idx_agents_operator", table_name="agents")
    op.drop_table("agents")

    op.drop_index("idx_users_wallet", table_name="users")
    op.drop_table("users")

    op.execute("DROP TYPE IF EXISTS task_status")
    op.execute("DROP TYPE IF EXISTS user_role")
