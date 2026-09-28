import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
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


class SettlementStatus(str, enum.Enum):
    PENDING_AUTHORIZATION = "PENDING_AUTHORIZATION"
    AUTHORIZED = "AUTHORIZED"
    SUBMITTED = "SUBMITTED"
    CONFIRMED = "CONFIRMED"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"
    CANCELLED = "CANCELLED"


class SettlementAction(str, enum.Enum):
    RELEASE = "RELEASE"
    REFUND = "REFUND"
    DISPUTE_RESOLVE_RELEASE = "DISPUTE_RESOLVE_RELEASE"
    DISPUTE_RESOLVE_REFUND = "DISPUTE_RESOLVE_REFUND"


class Settlement(Base):
    """
    Durable settlement record representing an authorized on-chain escrow action.
    Maintains deterministic identity, full state history, and atomic lifecycle tracking.
    """
    __tablename__ = "settlements"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    chain_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    escrow_contract: Mapped[str] = mapped_column(String(42), nullable=False)
    escrow_id: Mapped[str] = mapped_column(String(66), nullable=False, index=True)
    client_address: Mapped[str] = mapped_column(String(42), nullable=False, index=True)
    beneficiary_address: Mapped[str] = mapped_column(String(42), nullable=False, index=True)
    token_address: Mapped[str] = mapped_column(String(42), nullable=False)
    amount: Mapped[int] = mapped_column(Numeric(38, 0), nullable=False)
    action: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    status: Mapped[str] = mapped_column(
        String(32), default=SettlementStatus.PENDING_AUTHORIZATION.value, nullable=False, index=True
    )
    authorization_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    # Off-chain associations
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    orchestration_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("orchestrations.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    execution_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agent_executions.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # Authorization details
    authorized_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    authorized_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    authorization_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    blocked_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Dispute resolution details if applicable
    dispute_reason_hash: Mapped[str | None] = mapped_column(String(66), nullable=True)
    beneficiary_amount: Mapped[int | None] = mapped_column(Numeric(38, 0), nullable=True)
    client_refund_amount: Mapped[int | None] = mapped_column(Numeric(38, 0), nullable=True)

    # Blockchain submission link
    transaction_intent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("blockchain_transaction_intents.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    submitted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    failed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    cancelled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # On-chain settlement confirmation data
    settlement_tx_hash: Mapped[str | None] = mapped_column(String(66), nullable=True, index=True)
    block_number: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    block_hash: Mapped[str | None] = mapped_column(String(66), nullable=True)
    confirmations: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )

    # Relationships
    user = relationship("User", foreign_keys=[user_id])
    orchestration = relationship("Orchestration", foreign_keys=[orchestration_id])
    execution = relationship("AgentExecution", foreign_keys=[execution_id])
    authorizer = relationship("User", foreign_keys=[authorized_by])
    transaction_intent = relationship("BlockchainTransactionIntent", foreign_keys=[transaction_intent_id])
    history = relationship("SettlementHistory", back_populates="settlement", cascade="all, delete-orphan", order_by="SettlementHistory.created_at.asc()")

    __table_args__ = (
        UniqueConstraint("chain_id", "idempotency_key", name="uq_settlements_chain_idempotency"),
        UniqueConstraint("chain_id", "escrow_id", "action", "authorization_version", name="uq_settlements_escrow_action_ver"),
        Index("idx_settlements_chain_status", "chain_id", "status"),
        Index("idx_settlements_escrow", "chain_id", "escrow_id"),
        Index("idx_settlements_orchestration", "orchestration_id"),
        Index("idx_settlements_execution", "execution_id"),
        Index("idx_settlements_user", "user_id"),
    )


class SettlementHistory(Base):
    """
    Immutable audit history of settlement status transitions.
    """
    __tablename__ = "settlement_history"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    settlement_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("settlements.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    from_status: Mapped[str] = mapped_column(String(32), nullable=False)
    to_status: Mapped[str] = mapped_column(String(32), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    actor_role: Mapped[str | None] = mapped_column(String(32), nullable=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False, index=True
    )

    settlement = relationship("Settlement", back_populates="history")
    actor = relationship("User", foreign_keys=[actor_id])
