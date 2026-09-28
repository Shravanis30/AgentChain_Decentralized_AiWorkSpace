"""006_orchestrations_and_artifacts

Revision ID: 006_orchestrations_and_artifacts
Revises: 005_outbox_and_leases
Create Date: 2026-09-27 01:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "006_orchestrations_and_artifacts"
down_revision: str | None = "005_outbox_and_leases"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Create orchestrations table
    op.create_table(
        "orchestrations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "requested_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("goal", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="PLANNING"),
        sa.Column("graph_version", sa.String(length=32), nullable=False, server_default="1.0.0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("result", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "metadata_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}",
            nullable=False,
        ),
    )
    op.create_index(
        "idx_orchestrations_requested_status",
        "orchestrations",
        ["requested_by", "status"],
    )
    op.create_index("idx_orchestrations_status", "orchestrations", ["status"])
    op.create_index("idx_orchestrations_created_at", "orchestrations", ["created_at"])

    # 2. Add orchestration linkage to agent_executions
    op.add_column(
        "agent_executions",
        sa.Column("orchestration_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "agent_executions",
        sa.Column("orchestration_task_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_index(
        "idx_agent_executions_orchestration_id",
        "agent_executions",
        ["orchestration_id"],
    )
    op.create_index(
        "idx_agent_executions_orchestration_task_id",
        "agent_executions",
        ["orchestration_task_id"],
    )

    # 3. Create orchestration_tasks table
    op.create_table(
        "orchestration_tasks",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "orchestration_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("orchestrations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("task_key", sa.String(length=64), nullable=False),
        sa.Column(
            "agent_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agents.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("agent_version", sa.String(length=32), nullable=False),
        sa.Column(
            "input_data",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}",
            nullable=False,
        ),
        sa.Column("input_hash", sa.String(length=64), nullable=False),
        sa.Column("output_data", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("output_hash", sa.String(length=64), nullable=True),
        sa.Column(
            "status",
            sa.String(length=32),
            server_default="PENDING",
            nullable=False,
        ),
        sa.Column(
            "dependencies",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="[]",
            nullable=False,
        ),
        sa.Column(
            "execution_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agent_executions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("attempt", sa.Integer(), server_default="0", nullable=False),
        sa.Column("max_attempts", sa.Integer(), server_default="3", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.UniqueConstraint(
            "orchestration_id", "task_key", name="uq_orchestration_task_key"
        ),
    )
    op.create_index(
        "idx_orch_tasks_orch_status",
        "orchestration_tasks",
        ["orchestration_id", "status"],
    )
    op.create_index(
        "idx_orch_tasks_execution_id",
        "orchestration_tasks",
        ["execution_id"],
    )
    op.create_index(
        "idx_orch_tasks_agent_id",
        "orchestration_tasks",
        ["agent_id"],
    )

    # 4. Create orchestration_checkpoints table
    op.create_table(
        "orchestration_checkpoints",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "orchestration_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("orchestrations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("checkpoint_id", sa.String(length=128), nullable=False),
        sa.Column("graph_version", sa.String(length=32), nullable=False),
        sa.Column("state_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("state_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index(
        "idx_orch_checkpoints_orch_created",
        "orchestration_checkpoints",
        ["orchestration_id", "created_at"],
    )

    # 5. Create artifacts table
    op.create_table(
        "artifacts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "orchestration_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("orchestrations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "task_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("orchestration_tasks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "producer_agent_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agents.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("producer_agent_version", sa.String(length=32), nullable=False),
        sa.Column(
            "execution_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agent_executions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("artifact_key", sa.String(length=64), nullable=False),
        sa.Column(
            "content_type",
            sa.String(length=64),
            server_default="application/json",
            nullable=False,
        ),
        sa.Column("schema_version", sa.String(length=32), server_default="1.0", nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("content_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index(
        "idx_artifacts_orch_key",
        "artifacts",
        ["orchestration_id", "artifact_key"],
    )
    op.create_index("idx_artifacts_sha256", "artifacts", ["sha256"])


def downgrade() -> None:
    op.drop_index("idx_artifacts_sha256", table_name="artifacts")
    op.drop_index("idx_artifacts_orch_key", table_name="artifacts")
    op.drop_table("artifacts")

    op.drop_index("idx_orch_checkpoints_orch_created", table_name="orchestration_checkpoints")
    op.drop_table("orchestration_checkpoints")

    op.drop_index("idx_orch_tasks_agent_id", table_name="orchestration_tasks")
    op.drop_index("idx_orch_tasks_execution_id", table_name="orchestration_tasks")
    op.drop_index("idx_orch_tasks_orch_status", table_name="orchestration_tasks")
    op.drop_table("orchestration_tasks")

    op.drop_index("idx_agent_executions_orchestration_task_id", table_name="agent_executions")
    op.drop_index("idx_agent_executions_orchestration_id", table_name="agent_executions")
    op.drop_column("agent_executions", "orchestration_task_id")
    op.drop_column("agent_executions", "orchestration_id")

    op.drop_index("idx_orchestrations_created_at", table_name="orchestrations")
    op.drop_index("idx_orchestrations_status", table_name="orchestrations")
    op.drop_index("idx_orchestrations_requested_status", table_name="orchestrations")
    op.drop_table("orchestrations")
