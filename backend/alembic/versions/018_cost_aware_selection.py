"""018_cost_aware_selection

Revision ID: 018_cost_aware_selection
Revises: 017_selection_hardening
Create Date: 2026-09-29 02:00:00.000000

Phase 6.7 — Cost-Aware & Constraint-Aware Agent Selection

Adds economic snapshot and budget fields:
To selection_decisions:
  - selected_price_atomic (NUMERIC(38, 0), NULLABLE)
  - price_currency (VARCHAR(16), NOT NULL, DEFAULT 'USDC')
  - max_budget_atomic (NUMERIC(38, 0), NULLABLE)
  - economic_policy_version (VARCHAR(64), NOT NULL, DEFAULT 'EconomicConstraintPolicyV1')

To orchestration_tasks:
  - price_atomic (NUMERIC(38, 0), NULLABLE)
  - price_currency (VARCHAR(16), NOT NULL, DEFAULT 'USDC')
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "018_cost_aware_selection"
down_revision: str | None = "017_selection_hardening"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Update selection_decisions
    op.add_column(
        "selection_decisions",
        sa.Column("selected_price_atomic", sa.Numeric(38, 0), nullable=True),
    )
    op.add_column(
        "selection_decisions",
        sa.Column("price_currency", sa.String(16), nullable=False, server_default="USDC"),
    )
    op.add_column(
        "selection_decisions",
        sa.Column("max_budget_atomic", sa.Numeric(38, 0), nullable=True),
    )
    op.add_column(
        "selection_decisions",
        sa.Column(
            "economic_policy_version",
            sa.String(64),
            nullable=False,
            server_default="EconomicConstraintPolicyV1",
        ),
    )

    # 2. Update orchestration_tasks
    op.add_column(
        "orchestration_tasks",
        sa.Column("price_atomic", sa.Numeric(38, 0), nullable=True),
    )
    op.add_column(
        "orchestration_tasks",
        sa.Column("price_currency", sa.String(16), nullable=False, server_default="USDC"),
    )


def downgrade() -> None:
    # 1. Revert orchestration_tasks
    op.drop_column("orchestration_tasks", "price_currency")
    op.drop_column("orchestration_tasks", "price_atomic")

    # 2. Revert selection_decisions
    op.drop_column("selection_decisions", "economic_policy_version")
    op.drop_column("selection_decisions", "max_budget_atomic")
    op.drop_column("selection_decisions", "price_currency")
    op.drop_column("selection_decisions", "selected_price_atomic")
