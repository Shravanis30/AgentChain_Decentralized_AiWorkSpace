"""SQLAlchemy models for Phase 6.5 — Reputation Scoring & Policy Engine.

Architectural invariants:
- ReputationPolicyVersion: Immutable versioned registry of policy specifications.
- ReputationScore: Active score snapshot per agent × chain × policy.
- ReputationScoreHistory: Append-only immutable score calculation audit log.

Score is stored as score_scaled INTEGER in [0, 10000].
Divide by 10000 for normalized float in [0.0, 1.0].
This avoids floating-point nondeterminism.
"""

import uuid
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, utc_now

# Score scale factor: score_scaled = round(score_float * SCORE_SCALE)
SCORE_SCALE = 10000
SCORE_MIN_SCALED = 0
SCORE_MAX_SCALED = 10000


class ReputationPolicyVersion(Base):
    """Immutable versioned policy registry.

    Each row represents one version of a ReputationPolicy.
    Rows are insert-only.  Never update an existing row.
    If a policy changes, insert a new row with a higher version_number.

    Policy coexistence:
    - Multiple policy versions may be active simultaneously.
    - Historical scores under V1 are reproducible when V2 is activated.
    - Deactivation sets is_active=False and deprecated_at; rows remain.
    """

    __tablename__ = "reputation_policy_versions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    policy_name: Mapped[str] = mapped_column(
        String(64), nullable=False, index=True
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    formula_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, nullable=False
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    activated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    deprecated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )

    scores = relationship(
        "ReputationScore",
        back_populates="policy_version_record",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        UniqueConstraint("policy_name", "version_number", name="uq_policy_version_name_num"),
        Index("idx_policy_versions_name_active", "policy_name", "is_active"),
    )

    @property
    def version_string(self) -> str:
        return f"{self.policy_name}V{self.version_number}"


class ReputationScore(Base):
    """Current active score snapshot per agent × chain × policy.

    One row per (agent_id, chain_id, policy_version).
    Updated in-place on recalculation.

    Immutability contract:
    - score_scaled is only set via the policy engine; never by direct admin write.
    - Every update also inserts a corresponding ReputationScoreHistory row.
    - evidence_set_hash identifies exactly which evidence produced this score.

    Integer arithmetic:
    - score_scaled: integer in [0, 10000].
    - Divide by SCORE_SCALE (10000) to get float in [0.0, 1.0].
    - Rate fields (success_rate_scaled etc.) follow the same convention.
    - NULL rates indicate zero total_verified_executions (cold start).
    """

    __tablename__ = "reputation_scores"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    agent_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    chain_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    policy_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("reputation_policy_versions.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    policy_version: Mapped[str] = mapped_column(String(64), nullable=False, index=True)

    # Score: integer in [0, 10000]
    score_scaled: Mapped[int] = mapped_column(Integer, nullable=False)
    score_min: Mapped[int] = mapped_column(Integer, default=SCORE_MIN_SCALED, nullable=False)
    score_max: Mapped[int] = mapped_column(Integer, default=SCORE_MAX_SCALED, nullable=False)

    # Evidence counters (snapshot)
    total_verified_executions: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    verified_successes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    verified_failures: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    verified_timeouts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    verified_cancellations: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # Rates: NULL when total==0; integer in [0, 10000]
    success_rate_scaled: Mapped[int | None] = mapped_column(Integer, nullable=True)
    failure_rate_scaled: Mapped[int | None] = mapped_column(Integer, nullable=True)
    timeout_rate_scaled: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cancellation_rate_scaled: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Cold-start: separate experience metric
    experience_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # Deterministic evidence identification
    evidence_set_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    evidence_event_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # Calculation metadata
    calculation_reason: Mapped[str] = mapped_column(
        String(64), default="INITIAL_CALCULATION", nullable=False
    )
    explanation_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, nullable=False
    )
    calculated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )

    # Relationships
    agent = relationship("Agent")
    policy_version_record = relationship("ReputationPolicyVersion", back_populates="scores")
    history = relationship(
        "ReputationScoreHistory",
        back_populates="score",
        cascade="all, delete-orphan",
        order_by="ReputationScoreHistory.calculated_at.desc()",
    )

    __table_args__ = (
        CheckConstraint(
            "score_scaled >= 0 AND score_scaled <= 10000",
            name="chk_reputation_scores_bounds",
        ),
        UniqueConstraint(
            "agent_id",
            "chain_id",
            "policy_version",
            name="uq_reputation_scores_agent_chain_policy",
        ),
        Index("idx_reputation_scores_agent_chain", "agent_id", "chain_id"),
        Index("idx_reputation_scores_policy_version", "policy_version"),
        Index("idx_reputation_scores_evidence_hash", "evidence_set_hash"),
    )

    @property
    def score_float(self) -> float:
        """Normalized score in [0.0, 1.0]."""
        return self.score_scaled / SCORE_SCALE

    @property
    def score_percent(self) -> float:
        """Score expressed as a percentage [0.0, 100.0]."""
        return self.score_scaled / 100.0


class ReputationScoreHistory(Base):
    """Immutable append-only log of every score calculation event.

    Rows are NEVER updated.  One new row is appended per recalculation.
    Historical rows remain reproducible even after policy version changes.

    Calculation reasons:
    - INITIAL_CALCULATION: First score for this agent × policy.
    - NEW_VERIFIED_EVENT: New canonical confirmed reputation event added.
    - REORG_RECALCULATION: A blockchain reorg invalidated an event.
    - POLICY_VERSION_CHANGE: New policy version applied to same evidence.
    - MANUAL_RECALCULATION: Admin-triggered recalculation (still deterministic).
    """

    __tablename__ = "reputation_score_history"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    reputation_score_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("reputation_scores.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    agent_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    chain_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    policy_version: Mapped[str] = mapped_column(String(64), nullable=False, index=True)

    # Previous score (NULL for INITIAL_CALCULATION)
    previous_score_scaled: Mapped[int | None] = mapped_column(Integer, nullable=True)
    previous_evidence_set_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # New score
    new_score_scaled: Mapped[int] = mapped_column(Integer, nullable=False)
    new_evidence_set_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    # Evidence counters snapshot
    total_verified_executions: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    verified_successes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    verified_failures: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    verified_timeouts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    verified_cancellations: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    calculation_reason: Mapped[str] = mapped_column(String(64), nullable=False)
    explanation_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, nullable=False
    )
    calculated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )

    # Relationships
    agent = relationship("Agent")
    score = relationship("ReputationScore", back_populates="history")

    __table_args__ = (
        CheckConstraint(
            "new_score_scaled >= 0 AND new_score_scaled <= 10000",
            name="chk_reputation_score_history_bounds",
        ),
        CheckConstraint(
            "previous_score_scaled IS NULL OR (previous_score_scaled >= 0 AND previous_score_scaled <= 10000)",
            name="chk_reputation_score_history_prev_bounds",
        ),
        Index("idx_reputation_score_history_agent", "agent_id", "chain_id"),
        Index("idx_reputation_score_history_score_id", "reputation_score_id"),
        Index("idx_reputation_score_history_policy", "policy_version"),
        Index("idx_reputation_score_history_calculated_at", "calculated_at"),
    )
