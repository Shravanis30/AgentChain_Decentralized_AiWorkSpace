"""008_orch_wakeup_outbox

Revision ID: 008_orch_wakeup_outbox
Revises: 007_orch_attempt_and_wakeups
Create Date: 2026-09-27 16:30:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic (max 32 chars).
revision: str = "008_orch_wakeup_outbox"
down_revision: str | None = "007_orch_attempt_and_wakeups"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "orchestration_wakeup_outbox",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "orchestration_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("orchestrations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "orchestration_task_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("orchestration_tasks.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "execution_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agent_executions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "event_type",
            sa.String(64),
            server_default="AGENT_EXECUTION_COMPLETED",
            nullable=False,
        ),
        sa.Column("terminal_status", sa.String(32), nullable=False),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "available_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_index(
        "idx_orch_wakeup_outbox_pending",
        "orchestration_wakeup_outbox",
        ["published_at", "available_at"],
    )
    op.create_index(
        "idx_orch_wakeup_outbox_orch_created",
        "orchestration_wakeup_outbox",
        ["orchestration_id", "created_at"],
    )
    op.create_index(
        "idx_orch_wakeup_outbox_exec",
        "orchestration_wakeup_outbox",
        ["execution_id"],
    )
    op.create_index(
        "idx_orch_wakeup_outbox_task",
        "orchestration_wakeup_outbox",
        ["orchestration_task_id"],
    )


def downgrade() -> None:
    op.drop_index("idx_orch_wakeup_outbox_task", table_name="orchestration_wakeup_outbox")
    op.drop_index("idx_orch_wakeup_outbox_exec", table_name="orchestration_wakeup_outbox")
    op.drop_index("idx_orch_wakeup_outbox_orch_created", table_name="orchestration_wakeup_outbox")
    op.drop_index("idx_orch_wakeup_outbox_pending", table_name="orchestration_wakeup_outbox")
    op.drop_table("orchestration_wakeup_outbox")
