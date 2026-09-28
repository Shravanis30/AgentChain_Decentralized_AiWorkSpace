"""007_orch_attempt_and_wakeups

Revision ID: 007_orch_attempt_and_wakeups
Revises: 006_orchestrations_and_artifacts
Create Date: 2026-09-27 15:50:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic (max 32 chars).
revision: str = "007_orch_attempt_and_wakeups"
down_revision: str | None = "006_orchestrations_and_artifacts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Add task_attempt column to agent_executions table if not already added
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = [c["name"] for c in inspector.get_columns("agent_executions")]
    if "task_attempt" not in columns:
        op.add_column(
            "agent_executions",
            sa.Column("task_attempt", sa.Integer(), nullable=True),
        )
    indexes = [idx["name"] for idx in inspector.get_indexes("agent_executions")]
    if "idx_agent_executions_task_attempt" not in indexes:
        op.create_index(
            "idx_agent_executions_task_attempt",
            "agent_executions",
            ["orchestration_task_id", "task_attempt"],
        )


def downgrade() -> None:
    op.drop_index("idx_agent_executions_task_attempt", table_name="agent_executions")
    op.drop_column("agent_executions", "task_attempt")
