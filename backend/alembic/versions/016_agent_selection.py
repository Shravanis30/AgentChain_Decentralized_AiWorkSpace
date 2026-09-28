"""016_agent_selection

Revision ID: 016_agent_selection
Revises: 015_reputation_policy_engine
Create Date: 2026-09-29 01:00:00.000000

Phase 6.6 — Agent Selection
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "016_agent_selection"
down_revision: str | None = "015_reputation_policy_engine"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

def upgrade() -> None:
    op.create_table(
        "selection_decisions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("orchestration_id", UUID(as_uuid=True), sa.ForeignKey("orchestrations.id", ondelete="SET NULL"), nullable=True),
        sa.Column("task_id", UUID(as_uuid=True), sa.ForeignKey("orchestration_tasks.id", ondelete="SET NULL"), nullable=True),
        sa.Column("selection_policy_version", sa.String(64), nullable=False),
        sa.Column("reputation_policy_version", sa.String(64), nullable=True),
        sa.Column("request_constraints", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("eligible_candidates_json", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("selected_agent_id", UUID(as_uuid=True), sa.ForeignKey("agents.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("selected_agent_version", sa.String(32), nullable=False),
        sa.Column("reputation_score_scaled", sa.Integer(), nullable=True),
        sa.Column("reputation_evidence_count", sa.Integer(), nullable=True),
        sa.Column("reputation_evidence_hash", sa.String(64), nullable=True),
        sa.Column("canonical_selection_hash", sa.String(64), nullable=False),
        sa.Column("selection_reason", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    
    op.create_index("ix_selection_decisions_orchestration_id", "selection_decisions", ["orchestration_id"])
    op.create_index("ix_selection_decisions_task_id", "selection_decisions", ["task_id"])
    op.create_index("ix_selection_decisions_selected_agent_id", "selection_decisions", ["selected_agent_id"])
    op.create_index("ix_selection_decisions_canonical_hash", "selection_decisions", ["canonical_selection_hash"])
    op.create_index("ix_selection_decisions_agent", "selection_decisions", ["selected_agent_id", "selected_agent_version"])
    op.create_index("ix_selection_decisions_created_at", "selection_decisions", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_selection_decisions_created_at", table_name="selection_decisions")
    op.drop_index("ix_selection_decisions_agent", table_name="selection_decisions")
    op.drop_index("ix_selection_decisions_canonical_hash", table_name="selection_decisions")
    op.drop_index("ix_selection_decisions_selected_agent_id", table_name="selection_decisions")
    op.drop_index("ix_selection_decisions_task_id", table_name="selection_decisions")
    op.drop_index("ix_selection_decisions_orchestration_id", table_name="selection_decisions")
    
    op.drop_table("selection_decisions")
