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
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, utc_now


class BlockStatus(str, enum.Enum):
    SEEN = "SEEN"
    CONFIRMING = "CONFIRMING"
    CONFIRMED = "CONFIRMED"
    ORPHANED = "ORPHANED"


class EventStatus(str, enum.Enum):
    SEEN = "SEEN"
    CONFIRMING = "CONFIRMING"
    CONFIRMED = "CONFIRMED"
    ORPHANED = "ORPHANED"


class ReorgStatus(str, enum.Enum):
    DETECTED = "DETECTED"
    RESOLVED = "RESOLVED"
    FAILED = "FAILED"


class IntentStatus(str, enum.Enum):
    CREATED = "CREATED"
    QUEUED = "QUEUED"
    SUBMITTED = "SUBMITTED"
    PENDING = "PENDING"
    CONFIRMED = "CONFIRMED"
    FAILED = "FAILED"
    REPLACED = "REPLACED"
    DROPPED = "DROPPED"
    UNKNOWN = "UNKNOWN"


class TxLifecycleStatus(str, enum.Enum):
    QUEUED = "QUEUED"
    SUBMITTED = "SUBMITTED"
    PENDING = "PENDING"
    CONFIRMED = "CONFIRMED"
    FAILED = "FAILED"
    REPLACED = "REPLACED"
    DROPPED = "DROPPED"
    UNKNOWN = "UNKNOWN"


class AttemptStatus(str, enum.Enum):
    SUBMITTED = "SUBMITTED"
    PENDING = "PENDING"
    CONFIRMED = "CONFIRMED"
    FAILED = "FAILED"
    REPLACED = "REPLACED"
    DROPPED = "DROPPED"


class IndexedBlock(Base):
    """
    Persistent block tracking with parent hash linking for reorg detection.
    """
    __tablename__ = "indexed_blocks"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    chain_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    block_number: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    block_hash: Mapped[str] = mapped_column(String(66), nullable=False)
    parent_hash: Mapped[str] = mapped_column(String(66), nullable=False)
    timestamp: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), default=BlockStatus.SEEN.value, nullable=False, index=True
    )
    is_canonical: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=sa.text("true"), nullable=False, index=True
    )
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )

    __table_args__ = (
        UniqueConstraint("chain_id", "block_hash", name="uq_indexed_blocks_chain_hash"),
        Index(
            "uq_indexed_blocks_canonical",
            "chain_id",
            "block_number",
            unique=True,
            postgresql_where=sa.text("is_canonical = true"),
        ),
        Index("idx_indexed_blocks_chain_status_num", "chain_id", "status", "block_number"),
        Index("idx_indexed_blocks_chain_parent", "chain_id", "parent_hash"),
    )


class BlockchainEvent(Base):
    """
    Persisted on-chain events from the AgentChain Escrow contract.
    Deduplicated uniquely by (chain_id, block_hash, transaction_hash, log_index).
    """
    __tablename__ = "blockchain_events"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    chain_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    contract_address: Mapped[str] = mapped_column(String(42), nullable=False, index=True)
    block_number: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    block_hash: Mapped[str] = mapped_column(String(66), nullable=False)
    transaction_hash: Mapped[str] = mapped_column(String(66), nullable=False, index=True)
    transaction_index: Mapped[int] = mapped_column(Integer, nullable=False)
    log_index: Mapped[int] = mapped_column(Integer, nullable=False)
    event_name: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    event_signature: Mapped[str | None] = mapped_column(String(66), nullable=True)
    decoded_data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    raw_topics: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)
    raw_data: Mapped[str] = mapped_column(Text, default="0x", nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), default=EventStatus.SEEN.value, nullable=False, index=True
    )
    is_canonical: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, index=True)
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )

    __table_args__ = (
        UniqueConstraint("chain_id", "block_hash", "transaction_hash", "log_index", name="uq_blockchain_events_block_tx_log"),
        Index(
            "uq_bc_events_canonical_tx_log",
            "chain_id",
            "transaction_hash",
            "log_index",
            unique=True,
            postgresql_where=sa.text("is_canonical = true"),
        ),
        Index("idx_bc_events_chain_contract_block", "chain_id", "contract_address", "block_number"),
        Index("idx_bc_events_chain_name_canonical", "chain_id", "event_name", "is_canonical"),
        Index("idx_bc_events_chain_status", "chain_id", "status"),
        Index("idx_bc_events_block_hash", "chain_id", "block_hash"),
    )


class EscrowChainState(Base):
    """
    Derived database representation of on-chain escrow state.
    Authoritative on-chain state remains on the blockchain.
    """
    __tablename__ = "escrow_chain_state"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    chain_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    escrow_id: Mapped[str] = mapped_column(String(66), nullable=False, index=True)
    contract_address: Mapped[str] = mapped_column(String(42), nullable=False)
    client: Mapped[str] = mapped_column(String(42), nullable=False, index=True)
    developer: Mapped[str] = mapped_column(String(42), nullable=False, index=True)
    token: Mapped[str] = mapped_column(String(42), default="", nullable=False)
    amount: Mapped[int] = mapped_column(Numeric(38, 0), nullable=False)
    reference_id: Mapped[str] = mapped_column(String(66), nullable=False, index=True)
    salt: Mapped[str] = mapped_column(String(66), default="0", nullable=False)
    current_chain_state: Mapped[int] = mapped_column(Integer, default=0, nullable=False, index=True)
    creation_tx_hash: Mapped[str] = mapped_column(String(66), nullable=False)
    latest_tx_hash: Mapped[str] = mapped_column(String(66), nullable=False)
    latest_block_number: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    latest_block_hash: Mapped[str] = mapped_column(String(66), default="", nullable=False)
    is_canonical: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    confirmation_status: Mapped[str] = mapped_column(String(32), default="SEEN", nullable=False)
    last_reconciliation_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )

    __table_args__ = (
        UniqueConstraint("chain_id", "escrow_id", name="uq_escrow_chain_state_id"),
        Index("idx_escrow_chain_state_client", "chain_id", "client"),
        Index("idx_escrow_chain_state_developer", "chain_id", "developer"),
        Index("idx_escrow_chain_state_ref", "chain_id", "reference_id"),
        Index("idx_escrow_chain_state_state", "chain_id", "current_chain_state"),
        Index("idx_escrow_chain_state_canonical", "chain_id", "is_canonical", "confirmation_status"),
    )


class ChainReorganization(Base):
    """
    Audit record for detected chain reorganizations.
    """
    __tablename__ = "chain_reorganizations"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    chain_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    detection_block_number: Mapped[int] = mapped_column(BigInteger, nullable=False)
    old_block_hash: Mapped[str] = mapped_column(String(66), nullable=False)
    new_block_hash: Mapped[str] = mapped_column(String(66), nullable=False)
    common_ancestor_number: Mapped[int] = mapped_column(BigInteger, nullable=False)
    common_ancestor_hash: Mapped[str] = mapped_column(String(66), nullable=False)
    depth: Mapped[int] = mapped_column(Integer, nullable=False)
    affected_blocks_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    affected_events_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(
        String(32), default=ReorgStatus.DETECTED.value, nullable=False
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    detected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )

    __table_args__ = (
        Index("idx_reorgs_chain_status", "chain_id", "status"),
        Index("idx_reorgs_chain_detection_block", "chain_id", "detection_block_number"),
    )


class BlockchainTransactionIntent(Base):
    """
    Idempotent transaction-intent abstraction.
    Every future on-chain transaction request must originate from an intent.
    """
    __tablename__ = "blockchain_transaction_intents"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    chain_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    target_contract: Mapped[str] = mapped_column(String(42), nullable=False)
    operation: Mapped[str] = mapped_column(String(64), nullable=False)
    parameters: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), default=IntentStatus.CREATED.value, nullable=False, index=True
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )

    __table_args__ = (
        UniqueConstraint("chain_id", "idempotency_key", name="uq_intent_chain_idempotency"),
    )


class BlockchainTxOutbox(Base):
    """
    Transactional outbox for reliable delivery of transaction intents to Redis Streams / Relayer.
    """
    __tablename__ = "blockchain_tx_outbox"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    intent_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("blockchain_transaction_intents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    chain_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="PENDING", nullable=False, index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, default=5, nullable=False)
    backoff_seconds: Mapped[int] = mapped_column(Integer, default=2, nullable=False)
    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False, index=True
    )
    dispatched_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )

    intent = relationship("BlockchainTransactionIntent")

    __table_args__ = (
        Index("idx_bc_tx_outbox_due", "status", "next_attempt_at"),
        Index("idx_bc_tx_outbox_chain_idempotency", "chain_id", "idempotency_key"),
    )


class BlockchainTransaction(Base):
    """
    Lifecycle tracking for submitted blockchain transactions.
    """
    __tablename__ = "blockchain_transactions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    intent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("blockchain_transaction_intents.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    chain_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    from_address: Mapped[str] = mapped_column(String(42), nullable=False, index=True)
    to_address: Mapped[str] = mapped_column(String(42), nullable=False)
    nonce: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), default=TxLifecycleStatus.QUEUED.value, nullable=False, index=True
    )
    current_tx_hash: Mapped[str | None] = mapped_column(String(66), nullable=True)
    submitted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    failed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempts_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )

    intent = relationship("BlockchainTransactionIntent")
    attempts = relationship("BlockchainTransactionAttempt", back_populates="transaction", cascade="all, delete-orphan")

    __table_args__ = (
        UniqueConstraint("chain_id", "from_address", "nonce", name="uq_bc_tx_chain_from_nonce"),
        Index("idx_bc_tx_chain_status", "chain_id", "status"),
        Index("idx_bc_tx_current_hash", "chain_id", "current_tx_hash"),
    )


class BlockchainTransactionAttempt(Base):
    """
    Individual submission attempts for a transaction, supporting EIP-1559 gas replacement.
    """
    __tablename__ = "blockchain_transaction_attempts"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    transaction_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("blockchain_transactions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    tx_hash: Mapped[str] = mapped_column(String(66), nullable=False, index=True, unique=True)
    nonce: Mapped[int] = mapped_column(BigInteger, nullable=False)
    max_fee_per_gas: Mapped[int] = mapped_column(BigInteger, nullable=False)
    max_priority_fee_per_gas: Mapped[int] = mapped_column(BigInteger, nullable=False)
    gas_limit: Mapped[int] = mapped_column(BigInteger, nullable=False)
    raw_signed_tx: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(
        String(32), default=AttemptStatus.SUBMITTED.value, nullable=False, index=True
    )
    submitted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    block_number: Mapped[int | None] = mapped_column(BigInteger, nullable=True, index=True)
    block_hash: Mapped[str | None] = mapped_column(String(66), nullable=True)
    effective_gas_price: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    gas_used: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    receipt_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )

    transaction = relationship("BlockchainTransaction", back_populates="attempts")

    __table_args__ = (
        UniqueConstraint("transaction_id", "attempt_number", name="uq_tx_attempt_number"),
    )


class RelayerNonce(Base):
    """
    Persistent concurrency-safe nonce tracking per (chain_id, account_address).
    """
    __tablename__ = "relayer_nonces"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    chain_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    account_address: Mapped[str] = mapped_column(String(42), nullable=False)
    next_nonce: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    reserved_nonce: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    chain_confirmed_nonce: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )

    __table_args__ = (
        UniqueConstraint("chain_id", "account_address", name="uq_relayer_nonce_chain_account"),
    )
