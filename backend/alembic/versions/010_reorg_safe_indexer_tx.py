"""010_reorg_safe_indexer_tx

Revision ID: 010_reorg_safe_indexer_tx
Revises: 009_bc_indexer_tx_infra
Create Date: 2026-09-27 22:30:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic (max 32 chars).
revision: str = "010_reorg_safe_indexer_tx"
down_revision: str | None = "009_bc_indexer_tx_infra"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. indexed_blocks: Allow multiple historical blocks per height, enforce single canonical block
    op.drop_constraint("uq_indexed_blocks_chain_number", "indexed_blocks", type_="unique")
    
    op.add_column(
        "indexed_blocks",
        sa.Column("is_canonical", sa.Boolean(), nullable=False, server_default=sa.text("true")),
    )

    # Partial unique index: exactly one canonical block per (chain_id, block_number)
    op.create_index(
        "uq_indexed_blocks_canonical",
        "indexed_blocks",
        ["chain_id", "block_number"],
        unique=True,
        postgresql_where=sa.text("is_canonical = true"),
    )

    # 2. blockchain_events: Reorg-compatible event identity (chain_id, block_hash, transaction_hash, log_index)
    op.drop_constraint("uq_blockchain_events_dedup", "blockchain_events", type_="unique")

    op.create_unique_constraint(
        "uq_blockchain_events_block_tx_log",
        "blockchain_events",
        ["chain_id", "block_hash", "transaction_hash", "log_index"],
    )

    # Partial unique index: exactly one canonical event per (chain_id, transaction_hash, log_index)
    op.create_index(
        "uq_bc_events_canonical_tx_log",
        "blockchain_events",
        ["chain_id", "transaction_hash", "log_index"],
        unique=True,
        postgresql_where=sa.text("is_canonical = true"),
    )


def downgrade() -> None:
    # 2. blockchain_events
    op.drop_index("uq_bc_events_canonical_tx_log", table_name="blockchain_events")
    op.drop_constraint("uq_blockchain_events_block_tx_log", "blockchain_events", type_="unique")
    op.create_unique_constraint(
        "uq_blockchain_events_dedup",
        "blockchain_events",
        ["chain_id", "transaction_hash", "log_index"],
    )

    # 1. indexed_blocks
    op.drop_index("uq_indexed_blocks_canonical", table_name="indexed_blocks")
    op.drop_column("indexed_blocks", "is_canonical")
    op.create_unique_constraint(
        "uq_indexed_blocks_chain_number",
        "indexed_blocks",
        ["chain_id", "block_number"],
    )
