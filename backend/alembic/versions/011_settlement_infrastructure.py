"""011_settlement_infrastructure

Revision ID: 011_settlement_infrastructure
Revises: 010_reorg_safe_indexer_tx
Create Date: 2026-09-27 22:50:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

# revision identifiers, used by Alembic.
revision: str = "011_settlement_infrastructure"
down_revision: str | None = "010_reorg_safe_indexer_tx"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. settlements table
    op.create_table(
        "settlements",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("chain_id", sa.BigInteger(), nullable=False),
        sa.Column("escrow_contract", sa.String(42), nullable=False),
        sa.Column("escrow_id", sa.String(66), nullable=False),
        sa.Column("client_address", sa.String(42), nullable=False),
        sa.Column("beneficiary_address", sa.String(42), nullable=False),
        sa.Column("token_address", sa.String(42), nullable=False),
        sa.Column("amount", sa.Numeric(38, 0), nullable=False),
        sa.Column("action", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="PENDING_AUTHORIZATION"),
        sa.Column("authorization_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "orchestration_id",
            UUID(as_uuid=True),
            sa.ForeignKey("orchestrations.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "execution_id",
            UUID(as_uuid=True),
            sa.ForeignKey("agent_executions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "authorized_by",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("authorized_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("authorization_reason", sa.Text(), nullable=True),
        sa.Column("blocked_reason", sa.Text(), nullable=True),
        sa.Column("dispute_reason_hash", sa.String(66), nullable=True),
        sa.Column("beneficiary_amount", sa.Numeric(38, 0), nullable=True),
        sa.Column("client_refund_amount", sa.Numeric(38, 0), nullable=True),
        sa.Column(
            "transaction_intent_id",
            UUID(as_uuid=True),
            sa.ForeignKey("blockchain_transaction_intents.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("settlement_tx_hash", sa.String(66), nullable=True),
        sa.Column("block_number", sa.BigInteger(), nullable=True),
        sa.Column("block_hash", sa.String(66), nullable=True),
        sa.Column("confirmations", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("metadata_json", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )

    op.create_unique_constraint(
        "uq_settlements_chain_idempotency",
        "settlements",
        ["chain_id", "idempotency_key"],
    )
    op.create_unique_constraint(
        "uq_settlements_escrow_action_ver",
        "settlements",
        ["chain_id", "escrow_id", "action", "authorization_version"],
    )
    op.create_index(
        "idx_settlements_chain_status",
        "settlements",
        ["chain_id", "status"],
    )
    op.create_index(
        "idx_settlements_escrow",
        "settlements",
        ["chain_id", "escrow_id"],
    )
    op.create_index(
        "idx_settlements_tx_hash",
        "settlements",
        ["settlement_tx_hash"],
    )
    op.create_index(
        "idx_settlements_orchestration",
        "settlements",
        ["orchestration_id"],
    )
    op.create_index(
        "idx_settlements_execution",
        "settlements",
        ["execution_id"],
    )
    op.create_index(
        "idx_settlements_user",
        "settlements",
        ["user_id"],
    )
    op.create_index(
        "idx_settlements_intent",
        "settlements",
        ["transaction_intent_id"],
    )

    # 2. settlement_history table
    op.create_table(
        "settlement_history",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "settlement_id",
            UUID(as_uuid=True),
            sa.ForeignKey("settlements.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("from_status", sa.String(32), nullable=False),
        sa.Column("to_status", sa.String(32), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column(
            "actor_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("actor_role", sa.String(32), nullable=True),
        sa.Column("metadata_json", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )

    op.create_index(
        "idx_settlement_history_settlement",
        "settlement_history",
        ["settlement_id"],
    )
    op.create_index(
        "idx_settlement_history_created",
        "settlement_history",
        ["created_at"],
    )


def downgrade() -> None:
    op.drop_table("settlement_history")
    op.drop_table("settlements")
