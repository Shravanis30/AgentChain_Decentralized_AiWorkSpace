import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, utc_now


class OrchestrationWakeupEventType(str, enum.Enum):
    AGENT_EXECUTION_COMPLETED = "AGENT_EXECUTION_COMPLETED"


class OrchestrationWakeupOutbox(Base):
    __tablename__ = "orchestration_wakeup_outbox"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    orchestration_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("orchestrations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    orchestration_task_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("orchestration_tasks.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    execution_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agent_executions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    event_type: Mapped[str] = mapped_column(
        String(64),
        default=OrchestrationWakeupEventType.AGENT_EXECUTION_COMPLETED.value,
        nullable=False,
    )
    terminal_status: Mapped[str] = mapped_column(String(32), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False, index=True
    )
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False, index=True
    )
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    processed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )

    # Relationships
    orchestration = relationship("Orchestration")
    task = relationship("OrchestrationTask")
    execution = relationship("AgentExecution")

    def to_stream_dict(self) -> dict[str, str]:
        """
        Bounded payload for Redis Streams.
        Never contains raw agent payload or full artifacts. PostgreSQL is authoritative.
        """
        return {
            "event_id": str(self.id),
            "event_type": self.event_type,
            "orchestration_id": str(self.orchestration_id),
            "task_id": str(self.orchestration_task_id) if self.orchestration_task_id else "",
            "execution_id": str(self.execution_id),
            "terminal_status": self.terminal_status,
            "created_at": self.created_at.isoformat() if self.created_at else "",
            "attempt": str(self.payload.get("attempt", 1)),
            "correlation_id": str(self.payload.get("correlation_id", "")),
        }

    __table_args__ = (
        Index("idx_orch_wakeup_outbox_pending", "published_at", "available_at"),
        Index("idx_orch_wakeup_outbox_orch_created", "orchestration_id", "created_at"),
    )
