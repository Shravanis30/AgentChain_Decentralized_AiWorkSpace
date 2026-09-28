import enum
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

import sqlalchemy as sa
from sqlalchemy import (
    BigInteger,
    Boolean,
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
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, utc_now


class ReputationOutcomeType(str, enum.Enum):
    VERIFIED_SUCCESS = "VERIFIED_SUCCESS"
    VERIFIED_FAILURE = "VERIFIED_FAILURE"
    VERIFIED_TIMEOUT = "VERIFIED_TIMEOUT"
    VERIFIED_CANCELLATION = "VERIFIED_CANCELLATION"


class ReputationStatus(str, enum.Enum):
    PENDING = "PENDING"
    SUBMITTED = "SUBMITTED"
    CONFIRMING = "CONFIRMING"
    CONFIRMED = "CONFIRMED"
    REORGED = "REORGED"
    FAILED = "FAILED"
    INVALIDATED = "INVALIDATED"


class ReputationEvent(Base):
    """
    Authoritative verified reputation event model.
    Cryptographically grounded in terminal execution outcome and ResultNotary proof.
    Immutable evidence record; holds zero funds, zero subjective scores.
    """
    __tablename__ = "reputation_events"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    reputation_key: Mapped[str] = mapped_column(String(256), nullable=False)
    agent_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agents.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    agent_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agent_versions.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    execution_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agent_executions.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    outcome_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    result_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    notarization_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("result_notarizations.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    evidence_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    chain_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    contract_address: Mapped[str] = mapped_column(String(42), nullable=False, index=True)

    status: Mapped[str] = mapped_column(
        String(32), default=ReputationStatus.PENDING.value, nullable=False, index=True
    )
    transaction_intent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("blockchain_transaction_intents.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    transaction_hash: Mapped[str | None] = mapped_column(String(66), nullable=True, index=True)
    block_number: Mapped[int | None] = mapped_column(BigInteger, nullable=True, index=True)
    block_hash: Mapped[str | None] = mapped_column(String(66), nullable=True)
    confirmations: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_canonical: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, index=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    reorged_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Relationships
    agent = relationship("Agent")
    agent_version = relationship("AgentVersion")
    execution = relationship("AgentExecution")
    notarization = relationship("ResultNotarization")
    transaction_intent = relationship("BlockchainTransactionIntent")
    history = relationship(
        "ReputationHistory",
        back_populates="reputation_event",
        cascade="all, delete-orphan",
        order_by="ReputationHistory.created_at.desc()",
    )

    __table_args__ = (
        UniqueConstraint("chain_id", "execution_id", name="uq_reputation_events_chain_exec"),
        UniqueConstraint("reputation_key", name="uq_reputation_events_key"),
        UniqueConstraint("chain_id", "idempotency_key", name="uq_reputation_events_chain_idempotency"),
        Index("idx_reputation_events_agent_status", "agent_id", "status"),
        Index("idx_reputation_events_agent_ver_status", "agent_version_id", "status"),
        Index("idx_reputation_events_chain_status", "chain_id", "status"),
        Index("idx_reputation_events_tx_hash", "transaction_hash"),
        Index("idx_reputation_events_canonical", "is_canonical", "status"),
    )


class ReputationHistory(Base):
    """
    Audit log for state transitions of ReputationEvent records.
    """
    __tablename__ = "reputation_history"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    reputation_event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("reputation_events.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    from_status: Mapped[str] = mapped_column(String(32), nullable=False)
    to_status: Mapped[str] = mapped_column(String(32), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )

    reputation_event = relationship("ReputationEvent", back_populates="history")


class ReputationProfile(Base):
    """
    Deterministic projection read model aggregated across canonical confirmed reputation events.
    Does NOT use arbitrary weights or subjective star scores.
    """
    __tablename__ = "reputation_profiles"

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

    total_verified_executions: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    verified_successes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    verified_failures: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    verified_timeouts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    verified_cancellations: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    canonical_event_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    first_verified_execution_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    latest_verified_execution_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    latest_verified_outcome: Mapped[str | None] = mapped_column(String(32), nullable=True)
    latest_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    success_rate: Mapped[Decimal | None] = mapped_column(Numeric(5, 4), nullable=True)

    last_recalculated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )

    agent = relationship("Agent")

    __table_args__ = (
        UniqueConstraint("agent_id", "chain_id", name="uq_reputation_profiles_agent_chain"),
        Index("idx_reputation_profiles_agent_chain", "agent_id", "chain_id"),
    )
