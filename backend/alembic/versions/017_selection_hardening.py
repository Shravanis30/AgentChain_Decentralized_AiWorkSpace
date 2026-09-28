"""017_selection_hardening

Revision ID: 017_selection_hardening
Revises: 016_agent_selection
Create Date: 2026-09-29 01:20:00.000000

Phase 6.6.1 — Selection Integrity Hardening

Adds to selection_decisions:
  - attempt_number  (INT, NOT NULL, DEFAULT 1)
  - reputation_state (VARCHAR(16), NOT NULL, DEFAULT 'ABSENT')

Adds unique constraint uq_selection_task_attempt (task_id, attempt_number).
Adds composite index idx_selection_decisions_orch_attempt.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "017_selection_hardening"
down_revision: str | None = "016_agent_selection"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Add attempt_number column
    op.add_column(
        "selection_decisions",
        sa.Column("attempt_number", sa.Integer(), nullable=False, server_default="1"),
    )

    # Add reputation_state column
    op.add_column(
        "selection_decisions",
        sa.Column("reputation_state", sa.String(16), nullable=False, server_default="ABSENT"),
    )

    # Unique constraint: one decision per (task_id, attempt_number)
    # Only enforce when task_id IS NOT NULL (partial unique index)
    op.execute(
        """
        CREATE UNIQUE INDEX uq_selection_task_attempt
        ON selection_decisions (task_id, attempt_number)
        WHERE task_id IS NOT NULL
        """
    )

    # Composite index for orchestration+attempt lookups
    op.create_index(
        "idx_selection_decisions_orch_attempt",
        "selection_decisions",
        ["orchestration_id", "attempt_number"],
    )


def downgrade() -> None:
    op.drop_index("idx_selection_decisions_orch_attempt", table_name="selection_decisions")
    op.execute("DROP INDEX IF EXISTS uq_selection_task_attempt")
    op.drop_column("selection_decisions", "reputation_state")
    op.drop_column("selection_decisions", "attempt_number")
