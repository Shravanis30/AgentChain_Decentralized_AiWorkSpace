"""004_worker_runtime_and_execution_lifecycle

Revision ID: 004_worker_runtime_and_execution_lifecycle
Revises: 003_agent_registry_and_execution
Create Date: 2026-09-27 00:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "004_worker_runtime_lifecycle"
down_revision: str | None = "003_agent_registry_and_execution"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Add worker lifecycle columns to agent_executions
    op.add_column(
        "agent_executions",
        sa.Column(
            "queued_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.add_column(
        "agent_executions",
        sa.Column("timeout_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "agent_executions",
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "agent_executions",
        sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "agent_executions",
        sa.Column("max_attempts", sa.Integer(), server_default="3", nullable=False),
    )
    op.add_column(
        "agent_executions",
        sa.Column("worker_id", sa.String(length=128), nullable=True),
    )
    op.add_column(
        "agent_executions",
        sa.Column("idempotency_key", sa.String(length=128), nullable=True),
    )
    op.add_column(
        "agent_executions",
        sa.Column(
            "cancellation_requested",
            sa.Boolean(),
            server_default="false",
            nullable=False,
        ),
    )
    op.add_column(
        "agent_executions",
        sa.Column(
            "queue_name",
            sa.String(length=64),
            server_default="agent_executions",
            nullable=False,
        ),
    )
    op.add_column(
        "agent_executions",
        sa.Column("queue_message_id", sa.String(length=128), nullable=True),
    )

    # 2. Add indexes
    op.create_index(
        "idx_agent_executions_worker_id", "agent_executions", ["worker_id"]
    )
    op.create_index(
        "idx_agent_executions_worker_status",
        "agent_executions",
        ["worker_id", "status"],
    )
    op.create_index(
        "idx_agent_executions_idempotency",
        "agent_executions",
        ["idempotency_key"],
    )

    # 3. Add idempotency uniqueness constraint per user
    op.create_unique_constraint(
        "uq_execution_user_idempotency",
        "agent_executions",
        ["requested_by", "idempotency_key"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_execution_user_idempotency", "agent_executions", type_="unique"
    )
    op.drop_index(
        "idx_agent_executions_idempotency", table_name="agent_executions"
    )
    op.drop_index(
        "idx_agent_executions_worker_status", table_name="agent_executions"
    )
    op.drop_index(
        "idx_agent_executions_worker_id", table_name="agent_executions"
    )

    op.drop_column("agent_executions", "queue_message_id")
    op.drop_column("agent_executions", "queue_name")
    op.drop_column("agent_executions", "cancellation_requested")
    op.drop_column("agent_executions", "idempotency_key")
    op.drop_column("agent_executions", "worker_id")
    op.drop_column("agent_executions", "max_attempts")
    op.drop_column("agent_executions", "attempt_count")
    op.drop_column("agent_executions", "cancelled_at")
    op.drop_column("agent_executions", "timeout_at")
    op.drop_column("agent_executions", "queued_at")
