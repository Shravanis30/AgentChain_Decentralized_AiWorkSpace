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


class MarketplaceLifecycleStatus(str, enum.Enum):
    """Authoritative end-to-end marketplace state machine for Phase 6.8."""
    DISCOVERED = "DISCOVERED"
    SELECTED = "SELECTED"
    PRICE_LOCKED = "PRICE_LOCKED"
    ESCROW_PENDING = "ESCROW_PENDING"
    ESCROW_FUNDED = "ESCROW_FUNDED"
    EXECUTION_QUEUED = "EXECUTION_QUEUED"
    EXECUTING = "EXECUTING"
    RESULT_AVAILABLE = "RESULT_AVAILABLE"
    RESULT_NOTARIZED = "RESULT_NOTARIZED"
    OUTCOME_VERIFIED = "OUTCOME_VERIFIED"
    SETTLEMENT_PENDING = "SETTLEMENT_PENDING"
    SETTLED = "SETTLED"
    REFUNDED = "REFUNDED"
    DISPUTED = "DISPUTED"
    CANCELLED = "CANCELLED"
    TIMED_OUT = "TIMED_OUT"
    FAILED = "FAILED"


class MarketplaceOrder(Base):
    """
    Authoritative marketplace order tracking the complete end-to-end transaction lifecycle.
    Binds:
    Client Goal -> Candidate Selection -> Exact Price Pinning -> Escrow Creation ->
    Execution -> Result Notarization -> Verified Reputation -> Settlement -> 85/10/5 Distribution.
    """
    __tablename__ = "marketplace_orders"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    order_number: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    idempotency_key: Mapped[str] = mapped_column(String(128), unique=True, nullable=False, index=True)

    client_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    client_address: Mapped[str] = mapped_column(String(42), nullable=False, index=True)

    goal: Mapped[str] = mapped_column(Text, nullable=False)
    task_input: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    max_budget_atomic: Mapped[int | None] = mapped_column(Numeric(38, 0), nullable=True)
    price_currency: Mapped[str] = mapped_column(String(16), default="USDC", nullable=False)

    # Pinned Selection terms
    selection_decision_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("selection_decisions.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    selected_agent_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agents.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    selected_agent_version: Mapped[str] = mapped_column(String(32), nullable=False)
    pinned_price_atomic: Mapped[int] = mapped_column(Numeric(38, 0), nullable=False)
    platform_fee_atomic: Mapped[int] = mapped_column(Numeric(38, 0), default=0, nullable=False)
    total_escrow_atomic: Mapped[int] = mapped_column(Numeric(38, 0), nullable=False)

    # Escrow binding
    escrow_chain_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    escrow_contract: Mapped[str] = mapped_column(String(42), nullable=False)
    escrow_reference_id: Mapped[str] = mapped_column(String(66), nullable=False, index=True)
    escrow_salt: Mapped[str] = mapped_column(String(66), default="0", nullable=False)
    escrow_id: Mapped[str | None] = mapped_column(String(66), nullable=True, index=True)

    # Execution & Orchestration linkage
    orchestration_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("orchestrations.id", ondelete="SET NULL"), nullable=True, index=True
    )
    orchestration_task_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("orchestration_tasks.id", ondelete="SET NULL"), nullable=True, index=True
    )
    execution_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agent_executions.id", ondelete="SET NULL"), nullable=True, index=True
    )

    # Settlement & Distribution linkage
    settlement_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("settlements.id", ondelete="SET NULL"), nullable=True, index=True
    )
    distribution_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("distributions.id", ondelete="SET NULL"), nullable=True, index=True
    )

    # State Machine
    status: Mapped[str] = mapped_column(
        String(32), default=MarketplaceLifecycleStatus.SELECTED.value, nullable=False, index=True
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Relationships
    client_user = relationship("User", foreign_keys=[client_user_id])
    selection_decision = relationship("SelectionDecision", foreign_keys=[selection_decision_id])
    selected_agent = relationship("Agent", foreign_keys=[selected_agent_id])
    execution = relationship("AgentExecution", foreign_keys=[execution_id])
    settlement = relationship("Settlement", foreign_keys=[settlement_id])
    distribution = relationship("Distribution", foreign_keys=[distribution_id])

    __table_args__ = (
        Index("idx_marketplace_orders_client_status", "client_user_id", "status"),
        Index("idx_marketplace_orders_escrow", "escrow_chain_id", "escrow_id"),
        Index("idx_marketplace_orders_created", "created_at"),
    )
