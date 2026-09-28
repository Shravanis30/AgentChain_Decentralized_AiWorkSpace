"""013_result_notarization

Revision ID: 013_result_notarization
Revises: 012_distribution
Create Date: 2026-09-28 06:30:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

# revision identifiers, used by Alembic.
revision: str = "013_result_notarization"
down_revision: str | None = "012_distribution"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. result_notarizations table
    op.create_table(
        "result_notarizations",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("notarization_key", sa.String(256), nullable=False),
        sa.Column(
            "execution_id",
            UUID(as_uuid=True),
            sa.ForeignKey("agent_executions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("chain_id", sa.BigInteger(), nullable=False),
        sa.Column("contract_address", sa.String(42), nullable=False),
        sa.Column("result_hash", sa.String(64), nullable=False),
        sa.Column("hash_algorithm", sa.String(32), nullable=False, server_default="SHA-256"),
        sa.Column("canonicalization_version", sa.String(32), nullable=False, server_default="RFC-8785"),
        sa.Column("artifact_reference", sa.String(256), nullable=True),
        sa.Column("artifact_commitment", sa.String(66), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="PENDING"),
        sa.Column(
            "transaction_intent_id",
            UUID(as_uuid=True),
            sa.ForeignKey("blockchain_transaction_intents.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("transaction_hash", sa.String(66), nullable=True),
        sa.Column("block_number", sa.BigInteger(), nullable=True),
        sa.Column("block_hash", sa.String(66), nullable=True),
        sa.Column("confirmations", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_canonical", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("payload_json", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reorged_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("chain_id", "execution_id", name="uq_result_notarizations_chain_exec"),
        sa.UniqueConstraint("notarization_key", name="uq_result_notarizations_key"),
        sa.UniqueConstraint("chain_id", "idempotency_key", name="uq_result_notarizations_chain_idempotency"),
    )

    op.create_index("idx_notarizations_chain_status", "result_notarizations", ["chain_id", "status"])
    op.create_index("idx_notarizations_exec_status", "result_notarizations", ["execution_id", "status"])
    op.create_index("idx_notarizations_tx_hash", "result_notarizations", ["transaction_hash"])
    op.create_index("idx_notarizations_result_hash", "result_notarizations", ["result_hash"])

    # 2. notarization_history table
    op.create_table(
        "notarization_history",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "notarization_id",
            UUID(as_uuid=True),
            sa.ForeignKey("result_notarizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("from_status", sa.String(32), nullable=False),
        sa.Column("to_status", sa.String(32), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("metadata_json", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    op.create_index("idx_notarization_hist_notarization", "notarization_history", ["notarization_id"])


def downgrade() -> None:
    op.drop_table("notarization_history")
    op.drop_table("result_notarizations")
