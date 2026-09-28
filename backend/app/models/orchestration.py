import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

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
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, utc_now

if TYPE_CHECKING:
    from app.models.agent import Agent, AgentExecution
    from app.models.user import User


class OrchestrationStatus(str, enum.Enum):
    PLANNING = "PLANNING"
    READY = "READY"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class OrchestrationTaskStatus(str, enum.Enum):
    PENDING = "PENDING"
    READY = "READY"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    SKIPPED = "SKIPPED"


class Orchestration(Base):
    __tablename__ = "orchestrations"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    requested_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    goal: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        String(32),
        default=OrchestrationStatus.PLANNING.value,
        nullable=False,
        index=True,
    )
    graph_version: Mapped[str] = mapped_column(
        String(32), default="1.0.0", nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False, index=True
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    failed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    cancelled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, nullable=False
    )

    # Relationships
    tasks: Mapped[list["OrchestrationTask"]] = relationship(
        "OrchestrationTask",
        back_populates="orchestration",
        cascade="all, delete-orphan",
        order_by="OrchestrationTask.created_at",
    )
    checkpoints: Mapped[list["OrchestrationCheckpoint"]] = relationship(
        "OrchestrationCheckpoint",
        back_populates="orchestration",
        cascade="all, delete-orphan",
        order_by="OrchestrationCheckpoint.created_at",
    )
    artifacts: Mapped[list["Artifact"]] = relationship(
        "Artifact",
        back_populates="orchestration",
        cascade="all, delete-orphan",
        order_by="Artifact.created_at",
    )

    __table_args__ = (
        Index("idx_orchestrations_requested_status", "requested_by", "status"),
    )


class OrchestrationTask(Base):
    __tablename__ = "orchestration_tasks"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    orchestration_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("orchestrations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    task_key: Mapped[str] = mapped_column(String(64), nullable=False)
    agent_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agents.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    agent_version: Mapped[str] = mapped_column(String(32), nullable=False)
    price_atomic: Mapped[int | None] = mapped_column(Numeric(38, 0), nullable=True)
    price_currency: Mapped[str] = mapped_column(String(16), default="USDC", nullable=False)
    escrow_id: Mapped[str | None] = mapped_column(String(66), nullable=True, index=True)
    input_data: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, nullable=False
    )
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    output_data: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    output_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(
        String(32),
        default=OrchestrationTaskStatus.PENDING.value,
        nullable=False,
        index=True,
    )
    dependencies: Mapped[list[str]] = mapped_column(
        JSONB, default=list, nullable=False
    )
    execution_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agent_executions.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    attempt: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False, index=True
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    @property
    def logical_task_key(self) -> str:
        return f"orch-{self.orchestration_id}-task-{self.id}"

    @property
    def execution_attempt_idempotency_key(self) -> str:
        return f"orch-{self.orchestration_id}-task-{self.id}-attempt-{self.attempt}"

    # Relationships
    orchestration: Mapped["Orchestration"] = relationship(
        "Orchestration", back_populates="tasks"
    )
    execution: Mapped["AgentExecution | None"] = relationship("AgentExecution")

    __table_args__ = (
        UniqueConstraint("orchestration_id", "task_key", name="uq_orchestration_task_key"),
        Index("idx_orch_tasks_orch_status", "orchestration_id", "status"),
    )


class OrchestrationCheckpoint(Base):
    __tablename__ = "orchestration_checkpoints"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    orchestration_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("orchestrations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    checkpoint_id: Mapped[str] = mapped_column(String(128), nullable=False)
    graph_version: Mapped[str] = mapped_column(String(32), nullable=False)
    state_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    state_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False, index=True
    )

    orchestration: Mapped["Orchestration"] = relationship(
        "Orchestration", back_populates="checkpoints"
    )

    __table_args__ = (
        Index("idx_orch_checkpoints_orch_created", "orchestration_id", "created_at"),
    )


class Artifact(Base):
    __tablename__ = "artifacts"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    orchestration_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("orchestrations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    task_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("orchestration_tasks.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    producer_agent_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agents.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    producer_agent_version: Mapped[str] = mapped_column(String(32), nullable=False)
    execution_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agent_executions.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    artifact_key: Mapped[str] = mapped_column(String(64), nullable=False)
    content_type: Mapped[str] = mapped_column(String(64), default="application/json", nullable=False)
    schema_version: Mapped[str] = mapped_column(String(32), default="1.0", nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    content_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False, index=True
    )

    orchestration: Mapped["Orchestration"] = relationship(
        "Orchestration", back_populates="artifacts"
    )
    task: Mapped["OrchestrationTask"] = relationship("OrchestrationTask")

    __table_args__ = (
        Index("idx_artifacts_orch_key", "orchestration_id", "artifact_key"),
    )
