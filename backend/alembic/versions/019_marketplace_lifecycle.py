"""019_marketplace_lifecycle

Revision ID: 019_marketplace_lifecycle
Revises: 018_cost_aware_selection
Create Date: 2026-09-29 02:25:00.000000

Phase 6.8 — Marketplace Execution & Escrow Lifecycle Integration

Creates:
1. marketplace_orders table:
   Tracks authoritative lifecycle from Discovery -> Selection -> Price Lock ->
   Escrow -> Execution -> Result Notarization -> Verified Reputation -> Settlement -> Distribution.
2. orchestration_tasks.escrow_id column:
   Links multi-agent DAG tasks to on-chain escrow records.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "019_marketplace_lifecycle"
down_revision: str | None = "018_cost_aware_selection"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Add escrow_id to orchestration_tasks
    op.add_column(
        "orchestration_tasks",
        sa.Column("escrow_id", sa.String(66), nullable=True),
    )
    op.create_index(
        "idx_orch_tasks_escrow",
        "orchestration_tasks",
        ["escrow_id"],
    )

    # 2. Create marketplace_orders table
    op.create_table(
        "marketplace_orders",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("order_number", sa.String(64), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("client_user_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("client_address", sa.String(42), nullable=False),
        sa.Column("goal", sa.Text(), nullable=False),
        sa.Column("task_input", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("max_budget_atomic", sa.Numeric(38, 0), nullable=True),
        sa.Column("price_currency", sa.String(16), nullable=False, server_default="USDC"),
        sa.Column("selection_decision_id", UUID(as_uuid=True), sa.ForeignKey("selection_decisions.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("selected_agent_id", UUID(as_uuid=True), sa.ForeignKey("agents.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("selected_agent_version", sa.String(32), nullable=False),
        sa.Column("pinned_price_atomic", sa.Numeric(38, 0), nullable=False),
        sa.Column("platform_fee_atomic", sa.Numeric(38, 0), nullable=False, server_default="0"),
        sa.Column("total_escrow_atomic", sa.Numeric(38, 0), nullable=False),
        sa.Column("escrow_chain_id", sa.BigInteger(), nullable=False),
        sa.Column("escrow_contract", sa.String(42), nullable=False),
        sa.Column("escrow_reference_id", sa.String(66), nullable=False),
        sa.Column("escrow_salt", sa.String(66), nullable=False, server_default="0"),
        sa.Column("escrow_id", sa.String(66), nullable=True),
        sa.Column("orchestration_id", UUID(as_uuid=True), sa.ForeignKey("orchestrations.id", ondelete="SET NULL"), nullable=True),
        sa.Column("orchestration_task_id", UUID(as_uuid=True), sa.ForeignKey("orchestration_tasks.id", ondelete="SET NULL"), nullable=True),
        sa.Column("execution_id", UUID(as_uuid=True), sa.ForeignKey("agent_executions.id", ondelete="SET NULL"), nullable=True),
        sa.Column("settlement_id", UUID(as_uuid=True), sa.ForeignKey("settlements.id", ondelete="SET NULL"), nullable=True),
        sa.Column("distribution_id", UUID(as_uuid=True), sa.ForeignKey("distributions.id", ondelete="SET NULL"), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="SELECTED"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("metadata_json", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("order_number", name="uq_marketplace_orders_number"),
        sa.UniqueConstraint("idempotency_key", name="uq_marketplace_orders_idempotency"),
    )

    op.create_index("idx_marketplace_orders_client_status", "marketplace_orders", ["client_user_id", "status"])
    op.create_index("idx_marketplace_orders_escrow", "marketplace_orders", ["escrow_chain_id", "escrow_id"])
    op.create_index("idx_marketplace_orders_created", "marketplace_orders", ["created_at"])
    op.create_index("idx_marketplace_orders_decision", "marketplace_orders", ["selection_decision_id"])
    op.create_index("idx_marketplace_orders_agent", "marketplace_orders", ["selected_agent_id"])
    op.create_index("idx_marketplace_orders_exec", "marketplace_orders", ["execution_id"])


def downgrade() -> None:
    # 1. Drop marketplace_orders table
    op.drop_table("marketplace_orders")

    # 2. Drop escrow_id from orchestration_tasks
    op.drop_index("idx_orch_tasks_escrow", table_name="orchestration_tasks")
    op.drop_column("orchestration_tasks", "escrow_id")
