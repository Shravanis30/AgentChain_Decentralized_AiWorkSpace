import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import DateTime, ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, utc_now

if TYPE_CHECKING:
    from app.models.user import User


class AuditEventType(str, enum.Enum):
    AUTH_NONCE_CREATED = "AUTH_NONCE_CREATED"
    AUTH_SUCCESS = "AUTH_SUCCESS"
    AUTH_FAILURE = "AUTH_FAILURE"
    LOGOUT = "LOGOUT"
    WALLET_ADDED = "WALLET_ADDED"
    WALLET_REMOVED = "WALLET_REMOVED"
    ROLE_CHANGED = "ROLE_CHANGED"
    SESSION_REVOKED = "SESSION_REVOKED"
    AGENT_CREATED = "AGENT_CREATED"
    AGENT_UPDATED = "AGENT_UPDATED"
    AGENT_VALIDATED = "AGENT_VALIDATED"
    AGENT_PUBLISHED = "AGENT_PUBLISHED"
    AGENT_SUSPENDED = "AGENT_SUSPENDED"
    AGENT_EXECUTION_QUEUED = "AGENT_EXECUTION_QUEUED"
    AGENT_EXECUTION_STARTED = "AGENT_EXECUTION_STARTED"
    AGENT_EXECUTION_SUCCEEDED = "AGENT_EXECUTION_SUCCEEDED"
    AGENT_EXECUTION_FAILED = "AGENT_EXECUTION_FAILED"
    AGENT_EXECUTION_CANCELLED = "AGENT_EXECUTION_CANCELLED"
    AGENT_EXECUTION_TIMED_OUT = "AGENT_EXECUTION_TIMED_OUT"
    SETTLEMENT_CREATED = "SETTLEMENT_CREATED"
    SETTLEMENT_AUTHORIZED = "SETTLEMENT_AUTHORIZED"
    SETTLEMENT_BLOCKED = "SETTLEMENT_BLOCKED"
    SETTLEMENT_SUBMITTED = "SETTLEMENT_SUBMITTED"
    SETTLEMENT_CONFIRMED = "SETTLEMENT_CONFIRMED"
    SETTLEMENT_FAILED = "SETTLEMENT_FAILED"
    SETTLEMENT_CANCELLED = "SETTLEMENT_CANCELLED"
    SETTLEMENT_REORG_INVALIDATED = "SETTLEMENT_REORG_INVALIDATED"
    SETTLEMENT_RECONCILED = "SETTLEMENT_RECONCILED"
    DISTRIBUTION_CREATED = "DISTRIBUTION_CREATED"
    DISTRIBUTION_CONFIRMED = "DISTRIBUTION_CONFIRMED"
    DISTRIBUTION_FAILED = "DISTRIBUTION_FAILED"
    DISTRIBUTION_REORGED = "DISTRIBUTION_REORGED"
    REPUTATION_EVENT_CREATED = "REPUTATION_EVENT_CREATED"
    REPUTATION_EVENT_CONFIRMED = "REPUTATION_EVENT_CONFIRMED"
    REPUTATION_EVENT_REORGED = "REPUTATION_EVENT_REORGED"
    REPUTATION_SCORE_CALCULATED = "REPUTATION_SCORE_CALCULATED"
    REPUTATION_SCORE_RECALCULATED = "REPUTATION_SCORE_RECALCULATED"
    REPUTATION_SCORE_REORG_RECALCULATED = "REPUTATION_SCORE_REORG_RECALCULATED"



class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    wallet_address: Mapped[str | None] = mapped_column(
        String(42), nullable=True, index=True
    )
    event_type: Mapped[str] = mapped_column(
        String(64), nullable=False, index=True
    )
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False, index=True
    )
    request_id: Mapped[str | None] = mapped_column(
        String(64), nullable=True, index=True
    )
    ip_address: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    user_agent: Mapped[str | None] = mapped_column(
        String(256), nullable=True
    )
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, nullable=False
    )

    # Relationships
    user: Mapped["User | None"] = relationship("User", back_populates="audit_logs")
