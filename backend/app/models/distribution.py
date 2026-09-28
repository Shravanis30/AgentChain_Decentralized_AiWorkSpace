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


class DistributionStatus(str, enum.Enum):
    PENDING = "PENDING"
    AUTHORIZED = "AUTHORIZED"
    SUBMITTED = "SUBMITTED"
    CONFIRMED = "CONFIRMED"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"
    CANCELLED = "CANCELLED"
    REORGED = "REORGED"


class Distribution(Base):
    """
    Durable distribution record representing an 85/10/5 economic split.
    Tracks exact payouts to Developer (85%), Staker Pool (10%), and DAO Treasury (5% + remainder).
    """
    __tablename__ = "distributions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    distribution_key: Mapped[str] = mapped_column(String(256), nullable=False, unique=True)
    chain_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    escrow_contract: Mapped[str] = mapped_column(String(42), nullable=False)
    escrow_id: Mapped[str] = mapped_column(String(66), nullable=False, index=True)
    settlement_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("settlements.id", ondelete="CASCADE"), nullable=False, index=True
    )
    distributor_contract: Mapped[str] = mapped_column(String(42), nullable=False)
    token_address: Mapped[str] = mapped_column(String(42), nullable=False)

    # 85/10/5 exact integer amounts in USDC base units
    gross_amount: Mapped[int] = mapped_column(Numeric(38, 0), nullable=False)
    developer_amount: Mapped[int] = mapped_column(Numeric(38, 0), nullable=False)
    developer_recipient: Mapped[str] = mapped_column(String(42), nullable=False, index=True)
    staker_amount: Mapped[int] = mapped_column(Numeric(38, 0), nullable=False)
    staker_recipient: Mapped[str] = mapped_column(String(42), nullable=False)
    dao_amount: Mapped[int] = mapped_column(Numeric(38, 0), nullable=False)
    dao_recipient: Mapped[str] = mapped_column(String(42), nullable=False)

    distribution_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), default=DistributionStatus.PENDING.value, nullable=False, index=True
    )

    # Blockchain submission link
    transaction_intent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("blockchain_transaction_intents.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    distribution_tx_hash: Mapped[str | None] = mapped_column(String(66), nullable=True, index=True)
    onchain_distribution_id: Mapped[str | None] = mapped_column(String(66), nullable=True, index=True)
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
    confirmed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Relationships
    settlement = relationship("Settlement", foreign_keys=[settlement_id])
    transaction_intent = relationship("BlockchainTransactionIntent", foreign_keys=[transaction_intent_id])
    history = relationship(
        "DistributionHistory",
        back_populates="distribution",
        cascade="all, delete-orphan",
        order_by="DistributionHistory.created_at.asc()",
    )

    __table_args__ = (
        UniqueConstraint("chain_id", "idempotency_key", name="uq_distributions_chain_idempotency"),
        UniqueConstraint("settlement_id", "distribution_version", name="uq_distributions_settlement_version"),
        Index("idx_distributions_chain_status", "chain_id", "status"),
        Index("idx_distributions_escrow", "chain_id", "escrow_id"),
        Index("idx_distributions_settlement", "settlement_id"),
        Index("idx_distributions_tx_hash", "distribution_tx_hash"),
    )


class DistributionHistory(Base):
    """
    Immutable audit history of distribution status transitions.
    """
    __tablename__ = "distribution_history"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    distribution_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("distributions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    from_status: Mapped[str] = mapped_column(String(32), nullable=False)
    to_status: Mapped[str] = mapped_column(String(32), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False, index=True
    )

    distribution = relationship("Distribution", back_populates="history")
