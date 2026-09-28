import enum
import uuid
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy import (
    BigInteger,
    Boolean,
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


class NotarizationStatus(str, enum.Enum):
    PENDING = "PENDING"
    SUBMITTED = "SUBMITTED"
    CONFIRMING = "CONFIRMING"
    CONFIRMED = "CONFIRMED"
    REORGED = "REORGED"
    FAILED = "FAILED"


class ResultNotarization(Base):
    """
    Cryptographic Result Notarization model.
    Records on-chain anchoring proof for an AgentExecution result hash.
    PostgreSQL remains authoritative for business state; blockchain is authoritative for proof.
    """
    __tablename__ = "result_notarizations"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    notarization_key: Mapped[str] = mapped_column(String(256), nullable=False)
    execution_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agent_executions.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    chain_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    contract_address: Mapped[str] = mapped_column(String(42), nullable=False, index=True)
    result_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    hash_algorithm: Mapped[str] = mapped_column(String(32), default="SHA-256", nullable=False)
    canonicalization_version: Mapped[str] = mapped_column(String(32), default="RFC-8785", nullable=False)
    artifact_reference: Mapped[str | None] = mapped_column(String(256), nullable=True)
    artifact_commitment: Mapped[str | None] = mapped_column(String(66), nullable=True)

    status: Mapped[str] = mapped_column(
        String(32), default=NotarizationStatus.PENDING.value, nullable=False, index=True
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
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)

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
    execution = relationship("AgentExecution")
    transaction_intent = relationship("BlockchainTransactionIntent")
    history = relationship(
        "NotarizationHistory",
        back_populates="notarization",
        cascade="all, delete-orphan",
        order_by="NotarizationHistory.created_at.desc()",
    )

    __table_args__ = (
        UniqueConstraint("chain_id", "execution_id", name="uq_result_notarizations_chain_exec"),
        UniqueConstraint("notarization_key", name="uq_result_notarizations_key"),
        UniqueConstraint("chain_id", "idempotency_key", name="uq_result_notarizations_chain_idempotency"),
        Index("idx_notarizations_chain_status", "chain_id", "status"),
        Index("idx_notarizations_exec_status", "execution_id", "status"),
        Index("idx_notarizations_tx_hash", "transaction_hash"),
    )


class NotarizationHistory(Base):
    """
    Audit log for state transitions of ResultNotarization records.
    """
    __tablename__ = "notarization_history"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    notarization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("result_notarizations.id", ondelete="CASCADE"),
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

    notarization = relationship("ResultNotarization", back_populates="history")
