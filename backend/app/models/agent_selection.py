import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, utc_now


class SelectionDecision(Base):
    """Immutable record of a deterministic agent selection.

    One row is created per (task, attempt_number) pair.
    SelectionDecisions are NEVER overwritten. If a task is retried, a new row
    is inserted with attempt_number incremented.

    Reputation state is explicitly classified via reputation_state so that
    CANONICAL, COLD_START, and ABSENT are always distinguishable in the record.

    Phase 6.7 additions:
    Captures economic policy, selected price in atomic units, budget constraints,
    and currency snapshot. Price is treated as an explicit eligibility constraint
    and NEVER converted into or conflated with reputation.

    The canonical_selection_hash is SHA-256 over the exact deterministic inputs
    that produced the selection. It can be independently recomputed from the
    fields in this row (see agent_selector.py for the canonical payload schema).
    """
    __tablename__ = "selection_decisions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    orchestration_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("orchestrations.id", ondelete="SET NULL"), nullable=True, index=True
    )
    task_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("orchestration_tasks.id", ondelete="SET NULL"), nullable=True, index=True
    )

    # Attempt number: 1 for first selection, 2+ for retries.
    # Allows multiple decisions per task without overwriting history.
    attempt_number: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    selection_policy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    reputation_policy_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    economic_policy_version: Mapped[str] = mapped_column(
        String(64), nullable=False, default="EconomicConstraintPolicyV1"
    )

    # Explicit reputation state for the WINNER at time of selection.
    # Values: CANONICAL | COLD_START | ABSENT | UNAVAILABLE
    # See ReputationState enum in agent_selector.py.
    reputation_state: Mapped[str] = mapped_column(
        String(16), nullable=False, default="ABSENT"
    )

    # Economic snapshot for the WINNER at time of selection
    selected_price_atomic: Mapped[int | None] = mapped_column(Numeric(38, 0), nullable=True)
    price_currency: Mapped[str] = mapped_column(String(16), nullable=False, default="USDC")
    max_budget_atomic: Mapped[int | None] = mapped_column(Numeric(38, 0), nullable=True)

    # Input constraints used for candidate discovery
    request_constraints: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)

    # Snapshot of all eligible candidates (including their reputation state)
    # captured at the moment of selection. Order matches policy ranking.
    eligible_candidates_json: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list, nullable=False)

    # Pinned selected agent
    selected_agent_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agents.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    selected_agent_version: Mapped[str] = mapped_column(String(32), nullable=False)

    # Winner's reputation snapshot at time of selection.
    # NULL when reputation_state == ABSENT (no row existed).
    reputation_score_scaled: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reputation_evidence_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reputation_evidence_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # SHA-256 hex (64 chars) of the canonical selection payload.
    # Independently reproducible from the fields above.
    canonical_selection_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)

    selection_reason: Mapped[str] = mapped_column(Text, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False, index=True
    )

    __table_args__ = (
        # Unique per (task_id, attempt_number) — prevents duplicate decisions for same retry.
        UniqueConstraint("task_id", "attempt_number", name="uq_selection_task_attempt"),
        Index("idx_selection_decisions_agent", "selected_agent_id", "selected_agent_version"),
        Index("idx_selection_decisions_orch_attempt", "orchestration_id", "attempt_number"),
    )
