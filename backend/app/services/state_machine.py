from datetime import datetime, timedelta
import logging
from typing import Any
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.metrics import metrics
from app.models.agent import AgentExecution, ExecutionStatus
from app.models.base import utc_now
from app.models.orchestration_outbox import (
    OrchestrationWakeupEventType,
    OrchestrationWakeupOutbox,
)

logger = logging.getLogger("agentchain.state_machine")


class InvalidStateTransitionError(Exception):
    def __init__(self, current_status: str, target_status: str, execution_id: str):
        super().__init__(
            f"Invalid execution state transition from '{current_status}' to '{target_status}' "
            f"for execution '{execution_id}'"
        )
        self.current_status = current_status
        self.target_status = target_status
        self.execution_id = execution_id


class StaleLeaseConflictError(Exception):
    """Raised when a worker attempts to claim or finalize an execution without owning the active lease."""
    def __init__(self, message: str):
        super().__init__(message)


VALID_TRANSITIONS: dict[str, set[str]] = {
    ExecutionStatus.QUEUED.value: {
        ExecutionStatus.RUNNING.value,
        ExecutionStatus.CANCELLED.value,
    },
    ExecutionStatus.RUNNING.value: {
        ExecutionStatus.SUCCEEDED.value,
        ExecutionStatus.FAILED.value,
        ExecutionStatus.TIMED_OUT.value,
        ExecutionStatus.CANCELLED.value,
        ExecutionStatus.QUEUED.value,  # Transient retry requeue
        ExecutionStatus.RUNNING.value,  # Reclaim / lease renewal
    },
    ExecutionStatus.SUCCEEDED.value: set(),  # Terminal
    ExecutionStatus.FAILED.value: set(),     # Terminal
    ExecutionStatus.TIMED_OUT.value: set(),  # Terminal
    ExecutionStatus.CANCELLED.value: set(),  # Terminal
}

TERMINAL_STATUSES: set[str] = {
    ExecutionStatus.SUCCEEDED.value,
    ExecutionStatus.FAILED.value,
    ExecutionStatus.TIMED_OUT.value,
    ExecutionStatus.CANCELLED.value,
}


class ExecutionStateMachine:
    @staticmethod
    def can_transition(current_status: str, target_status: str) -> bool:
        allowed = VALID_TRANSITIONS.get(current_status, set())
        return target_status in allowed

    @classmethod
    def validate_transition(cls, execution: AgentExecution, target_status: str) -> None:
        if not cls.can_transition(execution.status, target_status):
            raise InvalidStateTransitionError(
                current_status=execution.status,
                target_status=target_status,
                execution_id=str(execution.id),
            )

    @classmethod
    async def transition_to_running(
        cls,
        db: AsyncSession,
        execution: AgentExecution,
        worker_id: str,
        timeout_seconds: int = 300,
        lease_duration_seconds: int = 60,
    ) -> AgentExecution:
        now = utc_now()

        # Reject transition if already terminal
        if execution.status in TERMINAL_STATUSES:
            metrics.inc_counter("execution_duplicate_delivery_total")
            raise InvalidStateTransitionError(
                current_status=execution.status,
                target_status=ExecutionStatus.RUNNING.value,
                execution_id=str(execution.id),
            )

        # If already running, check lease ownership
        if execution.status == ExecutionStatus.RUNNING.value:
            if (
                execution.lease_owner
                and execution.lease_owner != worker_id
                and execution.lease_expires_at
                and execution.lease_expires_at > now
            ):
                metrics.inc_counter("execution_stale_lease_total")
                raise StaleLeaseConflictError(
                    f"Execution {execution.id} is actively leased by {execution.lease_owner} until {execution.lease_expires_at.isoformat()}"
                )

        cls.validate_transition(execution, ExecutionStatus.RUNNING.value)

        execution.status = ExecutionStatus.RUNNING.value
        execution.started_at = execution.started_at or now
        execution.worker_id = worker_id
        execution.lease_owner = worker_id
        execution.lease_acquired_at = now
        execution.lease_expires_at = now + timedelta(seconds=lease_duration_seconds)
        execution.timeout_at = now + timedelta(seconds=timeout_seconds)
        execution.attempt_count += 1
        await db.flush()
        return execution

    @classmethod
    async def extend_lease(
        cls,
        db: AsyncSession,
        execution: AgentExecution,
        worker_id: str,
        extension_seconds: int = 60,
    ) -> bool:
        """Extends worker lease if the worker currently owns the active lease."""
        now = utc_now()
        if (
            execution.status == ExecutionStatus.RUNNING.value
            and execution.lease_owner == worker_id
        ):
            execution.lease_expires_at = now + timedelta(seconds=extension_seconds)
            await db.flush()
            return True
        return False

    @classmethod
    async def transition_to_succeeded(
        cls,
        db: AsyncSession,
        execution: AgentExecution,
        output_data: dict[str, Any],
        output_hash: str,
        execution_time_ms: int,
        worker_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> AgentExecution:
        # Idempotent terminal check: if already terminal, do NOT overwrite result
        if execution.status in TERMINAL_STATUSES:
            logger.warning(
                f"Execution {execution.id} already terminal ({execution.status}). Ignoring duplicate SUCCEEDED."
            )
            metrics.inc_counter("execution_finalization_conflict_total")
            return execution

        # Validate worker lease ownership if worker_id specified
        if (
            worker_id
            and execution.lease_owner
            and execution.lease_owner != worker_id
        ):
            metrics.inc_counter("execution_stale_lease_total")
            raise StaleLeaseConflictError(
                f"Worker '{worker_id}' lost lease on execution '{execution.id}' to '{execution.lease_owner}'"
            )

        cls.validate_transition(execution, ExecutionStatus.SUCCEEDED.value)
        execution.status = ExecutionStatus.SUCCEEDED.value
        execution.output_data = output_data
        execution.output_hash = output_hash
        execution.completed_at = utc_now()
        execution.lease_owner = None
        execution.lease_expires_at = None

        meta = execution.metadata_json or {}
        meta.update(metadata or {})
        meta["execution_time_ms"] = execution_time_ms
        execution.metadata_json = meta
        await cls._create_orchestration_wakeup_outbox(db, execution, ExecutionStatus.SUCCEEDED.value)
        await db.flush()
        return execution

    @classmethod
    async def transition_to_failed(
        cls,
        db: AsyncSession,
        execution: AgentExecution,
        error_code: str,
        error_message: str,
        worker_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> AgentExecution:
        # Idempotent terminal check
        if execution.status in TERMINAL_STATUSES:
            logger.warning(
                f"Execution {execution.id} already terminal ({execution.status}). Ignoring duplicate FAILED."
            )
            metrics.inc_counter("execution_finalization_conflict_total")
            return execution

        if (
            worker_id
            and execution.lease_owner
            and execution.lease_owner != worker_id
        ):
            metrics.inc_counter("execution_stale_lease_total")
            raise StaleLeaseConflictError(
                f"Worker '{worker_id}' lost lease on execution '{execution.id}' to '{execution.lease_owner}'"
            )

        cls.validate_transition(execution, ExecutionStatus.FAILED.value)
        execution.status = ExecutionStatus.FAILED.value
        execution.error_code = error_code
        execution.error_message = error_message
        execution.completed_at = utc_now()
        execution.lease_owner = None
        execution.lease_expires_at = None

        if metadata:
            meta = execution.metadata_json or {}
            meta.update(metadata)
            execution.metadata_json = meta
        await cls._create_orchestration_wakeup_outbox(db, execution, ExecutionStatus.FAILED.value)
        await db.flush()
        return execution

    @classmethod
    async def transition_to_timed_out(
        cls,
        db: AsyncSession,
        execution: AgentExecution,
        timeout_seconds: int,
        worker_id: str | None = None,
    ) -> AgentExecution:
        # Idempotent terminal check
        if execution.status in TERMINAL_STATUSES:
            logger.warning(
                f"Execution {execution.id} already terminal ({execution.status}). Ignoring duplicate TIMED_OUT."
            )
            metrics.inc_counter("execution_finalization_conflict_total")
            return execution

        if (
            worker_id
            and execution.lease_owner
            and execution.lease_owner != worker_id
        ):
            metrics.inc_counter("execution_stale_lease_total")
            raise StaleLeaseConflictError(
                f"Worker '{worker_id}' lost lease on execution '{execution.id}' to '{execution.lease_owner}'"
            )

        cls.validate_transition(execution, ExecutionStatus.TIMED_OUT.value)
        execution.status = ExecutionStatus.TIMED_OUT.value
        execution.error_code = "EXECUTION_TIMEOUT"
        execution.error_message = f"Execution exceeded maximum timeout of {timeout_seconds} seconds"
        execution.completed_at = utc_now()
        execution.lease_owner = None
        execution.lease_expires_at = None
        await cls._create_orchestration_wakeup_outbox(db, execution, ExecutionStatus.TIMED_OUT.value)
        await db.flush()
        return execution

    @classmethod
    async def transition_to_cancelled(
        cls,
        db: AsyncSession,
        execution: AgentExecution,
        reason: str | None = None,
    ) -> AgentExecution:
        # If already cancelled or finished, do not overwrite
        if execution.status in TERMINAL_STATUSES:
            return execution

        cls.validate_transition(execution, ExecutionStatus.CANCELLED.value)
        now = utc_now()
        execution.status = ExecutionStatus.CANCELLED.value
        execution.cancellation_requested = True
        execution.cancelled_at = now
        execution.completed_at = now
        execution.error_code = "EXECUTION_CANCELLED"
        execution.error_message = reason or "Execution cancelled by client request"
        execution.lease_owner = None
        execution.lease_expires_at = None
        await cls._create_orchestration_wakeup_outbox(db, execution, ExecutionStatus.CANCELLED.value)
        await db.flush()
        return execution

    @classmethod
    async def _create_orchestration_wakeup_outbox(
        cls,
        db: AsyncSession,
        execution: AgentExecution,
        terminal_status: str,
    ) -> OrchestrationWakeupOutbox | None:
        """
        Atomically creates a durable wake-up outbox record when an AgentExecution
        belonging to an orchestration reaches a terminal state.
        Guarantees terminal AgentExecution DB state and durable wake-up record are committed
        in the exact same database transaction.
        """
        if not execution.orchestration_id:
            return None

        res = await db.execute(
            select(OrchestrationWakeupOutbox.id).where(
                OrchestrationWakeupOutbox.execution_id == execution.id
            )
        )
        if res.scalar_one_or_none():
            return None

        meta = execution.metadata_json or {}
        corr_id = meta.get("correlation_id", "")
        now = utc_now()
        payload = {
            "orchestration_id": str(execution.orchestration_id),
            "task_id": str(execution.orchestration_task_id) if execution.orchestration_task_id else None,
            "execution_id": str(execution.id),
            "status": terminal_status,
            "attempt": execution.task_attempt or 1,
            "correlation_id": corr_id,
        }
        outbox_entry = OrchestrationWakeupOutbox(
            orchestration_id=execution.orchestration_id,
            orchestration_task_id=execution.orchestration_task_id,
            execution_id=execution.id,
            event_type=OrchestrationWakeupEventType.AGENT_EXECUTION_COMPLETED.value,
            terminal_status=terminal_status,
            payload=payload,
            attempts=0,
            created_at=now,
            available_at=now,
            published_at=None,
            processed_at=None,
        )
        db.add(outbox_entry)
        metrics.inc_counter("orchestration_wakeup_outbox_created_total")
        logger.info(
            f"Atomic durable wake-up record created for execution {execution.id} ({terminal_status})",
            extra={
                "orchestration_id": str(execution.orchestration_id),
                "task_id": str(execution.orchestration_task_id) if execution.orchestration_task_id else None,
                "execution_id": str(execution.id),
                "terminal_status": terminal_status,
                "wakeup_event_id": str(outbox_entry.id),
                "delivery_attempt": 0,
                "correlation_id": corr_id,
            },
        )
        return outbox_entry

    @classmethod
    async def transition_to_requeued(
        cls,
        db: AsyncSession,
        execution: AgentExecution,
        reason: str,
    ) -> AgentExecution:
        cls.validate_transition(execution, ExecutionStatus.QUEUED.value)
        execution.status = ExecutionStatus.QUEUED.value
        execution.error_code = "TRANSIENT_RETRY"
        execution.error_message = reason
        execution.lease_owner = None
        execution.lease_expires_at = None
        meta = execution.metadata_json or {}
        meta["last_retry_reason"] = reason
        execution.metadata_json = meta
        await db.flush()
        return execution
