"""009_blockchain_indexer_and_tx_infra

Revision ID: 009_blockchain_indexer_and_tx_infra
Revises: 008_orch_wakeup_outbox
Create Date: 2026-09-27 20:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic (max 32 chars).
revision: str = "009_bc_indexer_tx_infra"
down_revision: str | None = "008_orch_wakeup_outbox"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. indexed_blocks
    op.create_table(
        "indexed_blocks",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("chain_id", sa.BigInteger(), nullable=False),
        sa.Column("block_number", sa.BigInteger(), nullable=False),
        sa.Column("block_hash", sa.String(66), nullable=False),
        sa.Column("parent_hash", sa.String(66), nullable=False),
        sa.Column("timestamp", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="SEEN"),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("chain_id", "block_number", name="uq_indexed_blocks_chain_number"),
        sa.UniqueConstraint("chain_id", "block_hash", name="uq_indexed_blocks_chain_hash"),
    )
    op.create_index(
        "idx_indexed_blocks_chain_status_num",
        "indexed_blocks",
        ["chain_id", "status", "block_number"],
    )
    op.create_index(
        "idx_indexed_blocks_chain_parent",
        "indexed_blocks",
        ["chain_id", "parent_hash"],
    )

    # 2. blockchain_events
    op.create_table(
        "blockchain_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("chain_id", sa.BigInteger(), nullable=False),
        sa.Column("contract_address", sa.String(42), nullable=False),
        sa.Column("block_number", sa.BigInteger(), nullable=False),
        sa.Column("block_hash", sa.String(66), nullable=False),
        sa.Column("transaction_hash", sa.String(66), nullable=False),
        sa.Column("transaction_index", sa.Integer(), nullable=False),
        sa.Column("log_index", sa.Integer(), nullable=False),
        sa.Column("event_name", sa.String(64), nullable=False),
        sa.Column("event_signature", sa.String(66), nullable=True),
        sa.Column("decoded_data", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("raw_topics", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("raw_data", sa.Text(), nullable=False, server_default="0x"),
        sa.Column("status", sa.String(32), nullable=False, server_default="SEEN"),
        sa.Column("is_canonical", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("chain_id", "transaction_hash", "log_index", name="uq_blockchain_events_dedup"),
    )
    op.create_index(
        "idx_bc_events_chain_contract_block",
        "blockchain_events",
        ["chain_id", "contract_address", "block_number"],
    )
    op.create_index(
        "idx_bc_events_chain_name_canonical",
        "blockchain_events",
        ["chain_id", "event_name", "is_canonical"],
    )
    op.create_index(
        "idx_bc_events_chain_status",
        "blockchain_events",
        ["chain_id", "status"],
    )
    op.create_index(
        "idx_bc_events_block_hash",
        "blockchain_events",
        ["chain_id", "block_hash"],
    )

    # 3. escrow_chain_state
    op.create_table(
        "escrow_chain_state",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("chain_id", sa.BigInteger(), nullable=False),
        sa.Column("escrow_id", sa.String(66), nullable=False),
        sa.Column("contract_address", sa.String(42), nullable=False),
        sa.Column("client", sa.String(42), nullable=False),
        sa.Column("developer", sa.String(42), nullable=False),
        sa.Column("token", sa.String(42), nullable=False),
        sa.Column("amount", sa.Numeric(precision=38, scale=0), nullable=False),
        sa.Column("reference_id", sa.String(66), nullable=False),
        sa.Column("salt", sa.String(66), nullable=False),
        sa.Column("current_chain_state", sa.Integer(), nullable=False),
        sa.Column("creation_tx_hash", sa.String(66), nullable=False),
        sa.Column("latest_tx_hash", sa.String(66), nullable=False),
        sa.Column("latest_block_number", sa.BigInteger(), nullable=False),
        sa.Column("latest_block_hash", sa.String(66), nullable=False),
        sa.Column("is_canonical", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("confirmation_status", sa.String(32), nullable=False, server_default="SEEN"),
        sa.Column("last_reconciliation_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("chain_id", "escrow_id", name="uq_escrow_chain_state_id"),
    )
    op.create_index(
        "idx_escrow_chain_state_client",
        "escrow_chain_state",
        ["chain_id", "client"],
    )
    op.create_index(
        "idx_escrow_chain_state_developer",
        "escrow_chain_state",
        ["chain_id", "developer"],
    )
    op.create_index(
        "idx_escrow_chain_state_ref",
        "escrow_chain_state",
        ["chain_id", "reference_id"],
    )
    op.create_index(
        "idx_escrow_chain_state_state",
        "escrow_chain_state",
        ["chain_id", "current_chain_state"],
    )
    op.create_index(
        "idx_escrow_chain_state_canonical",
        "escrow_chain_state",
        ["chain_id", "is_canonical", "confirmation_status"],
    )

    # 4. chain_reorganizations
    op.create_table(
        "chain_reorganizations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("chain_id", sa.BigInteger(), nullable=False),
        sa.Column("detection_block_number", sa.BigInteger(), nullable=False),
        sa.Column("old_block_hash", sa.String(66), nullable=False),
        sa.Column("new_block_hash", sa.String(66), nullable=False),
        sa.Column("common_ancestor_number", sa.BigInteger(), nullable=False),
        sa.Column("common_ancestor_hash", sa.String(66), nullable=False),
        sa.Column("depth", sa.Integer(), nullable=False),
        sa.Column("affected_blocks_count", sa.Integer(), nullable=False),
        sa.Column("affected_events_count", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="DETECTED"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("detected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "idx_reorgs_chain_status",
        "chain_reorganizations",
        ["chain_id", "status"],
    )
    op.create_index(
        "idx_reorgs_chain_detection_block",
        "chain_reorganizations",
        ["chain_id", "detection_block_number"],
    )

    # 5. blockchain_transaction_intents
    op.create_table(
        "blockchain_transaction_intents",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("chain_id", sa.BigInteger(), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("target_contract", sa.String(42), nullable=False),
        sa.Column("operation", sa.String(64), nullable=False),
        sa.Column("parameters", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("status", sa.String(32), nullable=False, server_default="CREATED"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("chain_id", "idempotency_key", name="uq_tx_intents_chain_idempotency"),
    )
    op.create_index(
        "idx_tx_intents_chain_status",
        "blockchain_transaction_intents",
        ["chain_id", "status"],
    )
    op.create_index(
        "idx_tx_intents_chain_operation",
        "blockchain_transaction_intents",
        ["chain_id", "operation"],
    )

    # 6. blockchain_tx_outbox
    op.create_table(
        "blockchain_tx_outbox",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "intent_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("blockchain_transaction_intents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("chain_id", sa.BigInteger(), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="PENDING"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="5"),
        sa.Column("backoff_seconds", sa.Integer(), nullable=False, server_default="2"),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("dispatched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "idx_bc_tx_outbox_due",
        "blockchain_tx_outbox",
        ["status", "next_attempt_at"],
    )
    op.create_index(
        "idx_bc_tx_outbox_intent",
        "blockchain_tx_outbox",
        ["intent_id"],
    )
    op.create_index(
        "idx_bc_tx_outbox_chain_idempotency",
        "blockchain_tx_outbox",
        ["chain_id", "idempotency_key"],
    )

    # 7. blockchain_transactions
    op.create_table(
        "blockchain_transactions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "intent_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("blockchain_transaction_intents.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("chain_id", sa.BigInteger(), nullable=False),
        sa.Column("from_address", sa.String(42), nullable=False),
        sa.Column("to_address", sa.String(42), nullable=False),
        sa.Column("nonce", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="QUEUED"),
        sa.Column("current_tx_hash", sa.String(66), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("attempts_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("chain_id", "from_address", "nonce", name="uq_bc_tx_chain_from_nonce"),
    )
    op.create_index(
        "idx_bc_tx_chain_status",
        "blockchain_transactions",
        ["chain_id", "status"],
    )
    op.create_index(
        "idx_bc_tx_intent_id",
        "blockchain_transactions",
        ["intent_id"],
    )
    op.create_index(
        "idx_bc_tx_current_hash",
        "blockchain_transactions",
        ["chain_id", "current_tx_hash"],
    )

    # 8. blockchain_transaction_attempts
    op.create_table(
        "blockchain_transaction_attempts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "transaction_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("blockchain_transactions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("tx_hash", sa.String(66), nullable=False),
        sa.Column("nonce", sa.BigInteger(), nullable=False),
        sa.Column("max_fee_per_gas", sa.BigInteger(), nullable=False),
        sa.Column("max_priority_fee_per_gas", sa.BigInteger(), nullable=False),
        sa.Column("gas_limit", sa.BigInteger(), nullable=False),
        sa.Column("raw_signed_tx", sa.Text(), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="SUBMITTED"),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("block_number", sa.BigInteger(), nullable=True),
        sa.Column("block_hash", sa.String(66), nullable=True),
        sa.Column("effective_gas_price", sa.BigInteger(), nullable=True),
        sa.Column("gas_used", sa.BigInteger(), nullable=True),
        sa.Column("receipt_status", sa.Integer(), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("transaction_id", "attempt_number", name="uq_bc_tx_attempt_unique"),
        sa.UniqueConstraint("tx_hash", name="uq_bc_tx_attempt_hash"),
    )
    op.create_index(
        "idx_bc_tx_attempt_status",
        "blockchain_transaction_attempts",
        ["status"],
    )
    op.create_index(
        "idx_bc_tx_attempt_block",
        "blockchain_transaction_attempts",
        ["block_number"],
    )

    # 9. relayer_nonces
    op.create_table(
        "relayer_nonces",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("chain_id", sa.BigInteger(), nullable=False),
        sa.Column("account_address", sa.String(42), nullable=False),
        sa.Column("next_nonce", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("reserved_nonce", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("chain_confirmed_nonce", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("chain_id", "account_address", name="uq_relayer_nonces_chain_account"),
    )


def downgrade() -> None:
    op.drop_table("relayer_nonces")
    op.drop_table("blockchain_transaction_attempts")
    op.drop_table("blockchain_transactions")
    op.drop_table("blockchain_tx_outbox")
    op.drop_table("blockchain_transaction_intents")
    op.drop_table("chain_reorganizations")
    op.drop_table("escrow_chain_state")
    op.drop_table("blockchain_events")
    op.drop_table("indexed_blocks")
