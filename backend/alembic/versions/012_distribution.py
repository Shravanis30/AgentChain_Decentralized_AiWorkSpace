"""012_distribution

Revision ID: 012_distribution
Revises: 011_settlement_infrastructure
Create Date: 2026-09-28 03:30:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

# revision identifiers, used by Alembic.
revision: str = "012_distribution"
down_revision: str | None = "011_settlement_infrastructure"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. distributions table
    op.create_table(
        "distributions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("distribution_key", sa.String(256), nullable=False),
        sa.Column("chain_id", sa.BigInteger(), nullable=False),
        sa.Column("escrow_contract", sa.String(42), nullable=False),
        sa.Column("escrow_id", sa.String(66), nullable=False),
        sa.Column(
            "settlement_id",
            UUID(as_uuid=True),
            sa.ForeignKey("settlements.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("distributor_contract", sa.String(42), nullable=False),
        sa.Column("token_address", sa.String(42), nullable=False),
        sa.Column("gross_amount", sa.Numeric(38, 0), nullable=False),
        sa.Column("developer_amount", sa.Numeric(38, 0), nullable=False),
        sa.Column("developer_recipient", sa.String(42), nullable=False),
        sa.Column("staker_amount", sa.Numeric(38, 0), nullable=False),
        sa.Column("staker_recipient", sa.String(42), nullable=False),
        sa.Column("dao_amount", sa.Numeric(38, 0), nullable=False),
        sa.Column("dao_recipient", sa.String(42), nullable=False),
        sa.Column("distribution_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("status", sa.String(32), nullable=False, server_default="PENDING"),
        sa.Column(
            "transaction_intent_id",
            UUID(as_uuid=True),
            sa.ForeignKey("blockchain_transaction_intents.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("distribution_tx_hash", sa.String(66), nullable=True),
        sa.Column("onchain_distribution_id", sa.String(66), nullable=True),
        sa.Column("block_number", sa.BigInteger(), nullable=True),
        sa.Column("block_hash", sa.String(66), nullable=True),
        sa.Column("confirmations", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("metadata_json", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("chain_id", "idempotency_key", name="uq_distributions_chain_idempotency"),
        sa.UniqueConstraint("distribution_key", name="uq_distributions_key"),
        sa.UniqueConstraint("settlement_id", "distribution_version", name="uq_distributions_settlement_version"),
    )

    op.create_index("idx_distributions_chain_status", "distributions", ["chain_id", "status"])
    op.create_index("idx_distributions_escrow", "distributions", ["chain_id", "escrow_id"])
    op.create_index("idx_distributions_settlement", "distributions", ["settlement_id"])
    op.create_index("idx_distributions_tx_hash", "distributions", ["distribution_tx_hash"])

    # 2. distribution_history table
    op.create_table(
        "distribution_history",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "distribution_id",
            UUID(as_uuid=True),
            sa.ForeignKey("distributions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("from_status", sa.String(32), nullable=False),
        sa.Column("to_status", sa.String(32), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("metadata_json", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    op.create_index("idx_dist_hist_distribution", "distribution_history", ["distribution_id"])


def downgrade() -> None:
    op.drop_table("distribution_history")
    op.drop_table("distributions")
