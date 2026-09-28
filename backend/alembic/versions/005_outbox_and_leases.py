"""005_outbox_and_leases

Revision ID: 005_outbox_and_leases
Revises: 004_worker_runtime_lifecycle
Create Date: 2026-09-27 00:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "005_outbox_and_leases"
down_revision: str | None = "004_worker_runtime_lifecycle"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Add lease ownership columns to agent_executions
    op.add_column(
        "agent_executions",
        sa.Column("lease_owner", sa.String(length=128), nullable=True),
    )
    op.add_column(
        "agent_executions",
        sa.Column("lease_acquired_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "agent_executions",
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "idx_agent_executions_lease",
        "agent_executions",
        ["lease_owner", "lease_expires_at"],
    )

    # 2. Create execution_outbox table for transactional dispatch
    op.create_table(
        "execution_outbox",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "execution_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agent_executions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "available_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index(
        "idx_outbox_execution_id", "execution_outbox", ["execution_id"]
    )
    op.create_index(
        "idx_outbox_pending_dispatch",
        "execution_outbox",
        ["published_at", "available_at"],
    )


def downgrade() -> None:
    op.drop_index("idx_outbox_pending_dispatch", table_name="execution_outbox")
    op.drop_index("idx_outbox_execution_id", table_name="execution_outbox")
    op.drop_table("execution_outbox")

    op.drop_index("idx_agent_executions_lease", table_name="agent_executions")
    op.drop_column("agent_executions", "lease_expires_at")
    op.drop_column("agent_executions", "lease_acquired_at")
    op.drop_column("agent_executions", "lease_owner")
