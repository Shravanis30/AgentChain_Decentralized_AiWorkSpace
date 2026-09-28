"""014_verified_reputation

Revision ID: 014_verified_reputation
Revises: 013_result_notarization
Create Date: 2026-09-28 14:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

# revision identifiers, used by Alembic.
revision: str = "014_verified_reputation"
down_revision: str | None = "013_result_notarization"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. reputation_events table
    op.create_table(
        "reputation_events",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("reputation_key", sa.String(256), nullable=False),
        sa.Column(
            "agent_id",
            UUID(as_uuid=True),
            sa.ForeignKey("agents.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "agent_version_id",
            UUID(as_uuid=True),
            sa.ForeignKey("agent_versions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "execution_id",
            UUID(as_uuid=True),
            sa.ForeignKey("agent_executions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("outcome_type", sa.String(32), nullable=False),
        sa.Column("result_hash", sa.String(64), nullable=True),
        sa.Column(
            "notarization_id",
            UUID(as_uuid=True),
            sa.ForeignKey("result_notarizations.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("evidence_hash", sa.String(64), nullable=False),
        sa.Column("chain_id", sa.BigInteger(), nullable=False),
        sa.Column("contract_address", sa.String(42), nullable=False),
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
        sa.Column("metadata_json", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reorged_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("chain_id", "execution_id", name="uq_reputation_events_chain_exec"),
        sa.UniqueConstraint("reputation_key", name="uq_reputation_events_key"),
        sa.UniqueConstraint("chain_id", "idempotency_key", name="uq_reputation_events_chain_idempotency"),
    )

    op.create_index("idx_reputation_events_agent_status", "reputation_events", ["agent_id", "status"])
    op.create_index("idx_reputation_events_agent_ver_status", "reputation_events", ["agent_version_id", "status"])
    op.create_index("idx_reputation_events_chain_status", "reputation_events", ["chain_id", "status"])
    op.create_index("idx_reputation_events_tx_hash", "reputation_events", ["transaction_hash"])
    op.create_index("idx_reputation_events_exec_status", "reputation_events", ["execution_id", "status"])
    op.create_index("idx_reputation_events_canonical", "reputation_events", ["is_canonical", "status"])

    # 2. reputation_history table
    op.create_table(
        "reputation_history",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "reputation_event_id",
            UUID(as_uuid=True),
            sa.ForeignKey("reputation_events.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("from_status", sa.String(32), nullable=False),
        sa.Column("to_status", sa.String(32), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("metadata_json", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    op.create_index("idx_reputation_history_event", "reputation_history", ["reputation_event_id"])

    # 3. reputation_profiles table
    op.create_table(
        "reputation_profiles",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "agent_id",
            UUID(as_uuid=True),
            sa.ForeignKey("agents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("chain_id", sa.BigInteger(), nullable=False),
        sa.Column("total_verified_executions", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("verified_successes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("verified_failures", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("verified_timeouts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("verified_cancellations", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("canonical_event_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("first_verified_execution_id", UUID(as_uuid=True), nullable=True),
        sa.Column("latest_verified_execution_id", UUID(as_uuid=True), nullable=True),
        sa.Column("latest_verified_outcome", sa.String(32), nullable=True),
        sa.Column("latest_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("success_rate", sa.Numeric(5, 4), nullable=True),
        sa.Column("last_recalculated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("agent_id", "chain_id", name="uq_reputation_profiles_agent_chain"),
    )

    op.create_index("idx_reputation_profiles_agent_chain", "reputation_profiles", ["agent_id", "chain_id"])


def downgrade() -> None:
    op.drop_table("reputation_profiles")
    op.drop_table("reputation_history")
    op.drop_table("reputation_events")
