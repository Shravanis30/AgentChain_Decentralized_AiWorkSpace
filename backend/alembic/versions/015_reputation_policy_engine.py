"""015_reputation_policy_engine

Revision ID: 015_reputation_policy_engine
Revises: 014_verified_reputation
Create Date: 2026-09-28 23:00:00.000000

Phase 6.5 — Deterministic Reputation Scoring & Policy Engine.

Tables added:
  1. reputation_policy_versions — Immutable versioned policy registry.
  2. reputation_scores — Active score snapshot per agent x chain x policy.
  3. reputation_score_history — Append-only immutable score calculation log.

Score stored as score_scaled INTEGER in [0, 10000] (basis points x10000).
Divide by 10000 for normalized float in [0.0, 1.0].
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "015_reputation_policy_engine"
down_revision: str | None = "014_verified_reputation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. reputation_policy_versions
    op.create_table(
        "reputation_policy_versions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("policy_name", sa.String(64), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("formula_json", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("deprecated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("policy_name", "version_number", name="uq_policy_version_name_num"),
    )
    op.create_index("idx_policy_versions_name_active", "reputation_policy_versions", ["policy_name", "is_active"])

    # 2. reputation_scores
    op.create_table(
        "reputation_scores",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("agent_id", UUID(as_uuid=True), sa.ForeignKey("agents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("chain_id", sa.BigInteger(), nullable=False),
        sa.Column("policy_version_id", UUID(as_uuid=True), sa.ForeignKey("reputation_policy_versions.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("policy_version", sa.String(64), nullable=False),
        # Score in [0, 10000]; divide by 10000 for normalized [0.0, 1.0]
        sa.Column("score_scaled", sa.Integer(), nullable=False),
        sa.Column("score_min", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("score_max", sa.Integer(), nullable=False, server_default="10000"),
        # Evidence counters snapshot
        sa.Column("total_verified_executions", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("verified_successes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("verified_failures", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("verified_timeouts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("verified_cancellations", sa.Integer(), nullable=False, server_default="0"),
        # Rates in basis points [0, 10000]; NULL when total==0
        sa.Column("success_rate_scaled", sa.Integer(), nullable=True),
        sa.Column("failure_rate_scaled", sa.Integer(), nullable=True),
        sa.Column("timeout_rate_scaled", sa.Integer(), nullable=True),
        sa.Column("cancellation_rate_scaled", sa.Integer(), nullable=True),
        # Cold-start experience metric (separate from score)
        sa.Column("experience_count", sa.Integer(), nullable=False, server_default="0"),
        # Deterministic evidence identification
        sa.Column("evidence_set_hash", sa.String(64), nullable=False),
        sa.Column("evidence_event_count", sa.Integer(), nullable=False, server_default="0"),
        # Calculation metadata
        sa.Column("calculation_reason", sa.String(64), nullable=False, server_default="'INITIAL_CALCULATION'"),
        sa.Column("explanation_json", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("calculated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        # Constraints
        sa.CheckConstraint("score_scaled >= 0 AND score_scaled <= 10000", name="chk_reputation_scores_bounds"),
        sa.UniqueConstraint("agent_id", "chain_id", "policy_version", name="uq_reputation_scores_agent_chain_policy"),
    )
    op.create_index("idx_reputation_scores_agent_chain", "reputation_scores", ["agent_id", "chain_id"])
    op.create_index("idx_reputation_scores_policy_version", "reputation_scores", ["policy_version"])
    op.create_index("idx_reputation_scores_evidence_hash", "reputation_scores", ["evidence_set_hash"])

    # 3. reputation_score_history
    op.create_table(
        "reputation_score_history",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("reputation_score_id", UUID(as_uuid=True), sa.ForeignKey("reputation_scores.id", ondelete="CASCADE"), nullable=False),
        sa.Column("agent_id", UUID(as_uuid=True), sa.ForeignKey("agents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("chain_id", sa.BigInteger(), nullable=False),
        sa.Column("policy_version", sa.String(64), nullable=False),
        sa.Column("previous_score_scaled", sa.Integer(), nullable=True),
        sa.Column("previous_evidence_set_hash", sa.String(64), nullable=True),
        sa.Column("new_score_scaled", sa.Integer(), nullable=False),
        sa.Column("new_evidence_set_hash", sa.String(64), nullable=False),
        sa.Column("total_verified_executions", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("verified_successes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("verified_failures", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("verified_timeouts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("verified_cancellations", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("calculation_reason", sa.String(64), nullable=False),
        sa.Column("explanation_json", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("calculated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("new_score_scaled >= 0 AND new_score_scaled <= 10000", name="chk_reputation_score_history_bounds"),
        sa.CheckConstraint("previous_score_scaled IS NULL OR (previous_score_scaled >= 0 AND previous_score_scaled <= 10000)", name="chk_reputation_score_history_prev_bounds"),
    )
    op.create_index("idx_reputation_score_history_agent", "reputation_score_history", ["agent_id", "chain_id"])
    op.create_index("idx_reputation_score_history_score_id", "reputation_score_history", ["reputation_score_id"])
    op.create_index("idx_reputation_score_history_policy", "reputation_score_history", ["policy_version"])
    op.create_index("idx_reputation_score_history_calculated_at", "reputation_score_history", ["calculated_at"])

    # Enforce append-only immutability at PostgreSQL database level:
    # Any UPDATE or DELETE attempt on reputation_score_history is aborted by trigger.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION enforce_reputation_score_history_immutable()
        RETURNS TRIGGER AS $$
        BEGIN
            RAISE EXCEPTION 'reputation_score_history is append-only and cannot be mutated or deleted';
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_reputation_score_history_immutable
        BEFORE UPDATE OR DELETE ON reputation_score_history
        FOR EACH ROW EXECUTE FUNCTION enforce_reputation_score_history_immutable();
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_reputation_score_history_immutable ON reputation_score_history;")
    op.execute("DROP FUNCTION IF EXISTS enforce_reputation_score_history_immutable();")
    op.drop_table("reputation_score_history")
    op.drop_table("reputation_scores")
    op.drop_table("reputation_policy_versions")
