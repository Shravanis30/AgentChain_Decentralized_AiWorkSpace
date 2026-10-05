import json
import logging
import uuid
from typing import Any

from fastapi import HTTPException, Request, status
import jsonschema
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.agent import (
    Agent,
    AgentExecution,
    AgentStatus,
    ExecutionStatus,
)
from app.models.audit import AuditEventType
from app.models.base import utc_now
from app.models.user import UserRole
from app.services.audit import AuditService
from app.services.hashing import compute_canonical_hash
from app.services.queue import (
    QueueJob,
    get_execution_queue,
    is_cancellation_requested,
    publish_execution_event,
    signal_cancellation,
)
from app.services.rate_limiter import get_redis_client
from app.services.state_machine import ExecutionStateMachine

logger = logging.getLogger(__name__)

# Security & quota limits
MAX_INPUT_PAYLOAD_BYTES = 1024 * 1024  # 1MB
MAX_OUTPUT_PAYLOAD_BYTES = 5 * 1024 * 1024  # 5MB


class AgentExecutionService:
    @classmethod
    async def enqueue_agent_execution(
        cls,
        db: AsyncSession,
        agent_id: uuid.UUID,
        requested_by_user_id: uuid.UUID,
        input_data: dict[str, Any],
        idempotency_key: str | None = None,
        request: Request | None = None,
        agent_version: str | None = None,
        orchestration_id: uuid.UUID | None = None,
        task_id: uuid.UUID | None = None,
        task_attempt: int | None = None,
    ) -> AgentExecution:
        """
        Validates, creates, and durably enqueues an agent execution.
        The API process does NOT execute code directly.
        Enforces:
        - Authenticated caller with valid agent availability (PUBLISHED)
        - Maximum input payload quota
        - JSON schema validation
        - Deterministic input canonical hashing (RFC 8785 / SHA-256)
        - Idempotency per (user_id, idempotency_key)
        - Durable DB record in QUEUED state prior to queue submission
        - Durable message dispatch to Redis Stream
        """
        # 1. Payload size quota validation
        try:
            raw_input_bytes = len(json.dumps(input_data).encode("utf-8"))
        except (TypeError, ValueError) as e:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"Input payload could not be serialized to JSON: {e}",
            )

        if raw_input_bytes > MAX_INPUT_PAYLOAD_BYTES:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=f"Input payload size ({raw_input_bytes} bytes) exceeds limit ({MAX_INPUT_PAYLOAD_BYTES} bytes)",
            )

        # 2. Idempotency check: Return existing execution if idempotency key matches
        if idempotency_key:
            existing = await cls.get_execution_by_idempotency_key(
                db=db,
                user_id=requested_by_user_id,
                idempotency_key=idempotency_key,
            )
            if existing:
                logger.info(
                    f"Idempotent hit for user={requested_by_user_id}, key={idempotency_key}. "
                    f"Returning existing execution {existing.id}"
                )
                return existing

        # 3. Fetch agent and specified/current version
        query = (
            select(Agent)
            .options(
                selectinload(Agent.current_version),
                selectinload(Agent.versions),
                selectinload(Agent.owner),
            )
            .where(Agent.id == agent_id)
        )
        res = await db.execute(query)
        agent = res.scalar_one_or_none()

        if not agent:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Agent with ID '{agent_id}' not found",
            )

        if agent.status != AgentStatus.PUBLISHED.value:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Agent '{agent.name}' is in status '{agent.status}'. Only PUBLISHED agents may be executed.",
            )

        version = None
        if agent_version:
            for v in agent.versions:
                if v.version == agent_version:
                    version = v
                    break
            if not version:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Agent '{agent.name}' does not have published version '{agent_version}'",
                )
        else:
            version = agent.current_version

        if not version:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Agent has no published version available for execution",
            )

        # 4. Input schema validation
        try:
            jsonschema.validate(instance=input_data, schema=version.input_schema)
        except jsonschema.exceptions.ValidationError as e:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"Input does not conform to agent schema: {e.message}",
            )

        # 5. Compute canonical RFC 8785 SHA-256 hash
        input_hash = compute_canonical_hash(input_data)
        now = utc_now()
        correlation_id = request.headers.get("x-request-id", str(uuid.uuid4())) if request else str(uuid.uuid4())

        # 6. Create execution record (QUEUED) with orchestration linkage
        metadata: dict[str, Any] = {"correlation_id": correlation_id}
        if orchestration_id:
            metadata["orchestration_id"] = str(orchestration_id)
        if task_id:
            metadata["task_id"] = str(task_id)
        if task_attempt is not None:
            metadata["task_attempt"] = task_attempt

        execution = AgentExecution(
            agent_id=agent.id,
            agent_version_id=version.id,
            requested_by=requested_by_user_id,
            status=ExecutionStatus.QUEUED.value,
            input_data=input_data,
            input_hash=input_hash,
            queued_at=now,
            started_at=None,
            completed_at=None,
            idempotency_key=idempotency_key,
            attempt_count=0,
            max_attempts=version.runtime_config.get("max_retries", 3),
            queue_name="agent_executions",
            orchestration_id=orchestration_id,
            orchestration_task_id=task_id,
            task_attempt=task_attempt,
            metadata_json=metadata,
        )
        db.add(execution)

        try:
            await db.flush()
        except IntegrityError:
            await db.rollback()
            # If concurrent request used same idempotency key, retrieve it
            if idempotency_key:
                existing = await cls.get_execution_by_idempotency_key(
                    db=db,
                    user_id=requested_by_user_id,
                    idempotency_key=idempotency_key,
                )
                if existing:
                    return existing
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Duplicate execution creation conflict",
            )

        # 7. Audit log event: QUEUED
        await AuditService.record_event(
            db=db,
            event_type=AuditEventType.AGENT_EXECUTION_QUEUED,
            user_id=requested_by_user_id,
            request=request,
            metadata={
                "execution_id": str(execution.id),
                "agent_id": str(agent.id),
                "slug": agent.slug,
                "input_hash": input_hash,
                "idempotency_key": idempotency_key,
            },
        )

        # 8. Atomically persist outbox entry within the current DB transaction
        job_payload = {
            "execution_id": str(execution.id),
            "agent_id": str(agent.id),
            "agent_version_id": str(version.id),
            "requested_by": str(requested_by_user_id),
            "input_hash": input_hash,
            "attempt_number": 0,
            "created_at": now.isoformat(),
            "correlation_id": correlation_id,
            "queue_name": "agent_executions",
            "input_data": input_data,
        }
        from app.services.outbox import OutboxDispatcher

        outbox_entry = await OutboxDispatcher.create_outbox_entry(
            db=db,
            execution_id=execution.id,
            payload=job_payload,
        )

        # Commit transaction atomically persisting execution, audit event, and outbox record
        await db.commit()

        # 9. Dispatch outbox entry to Redis Streams post-commit
        try:
            await OutboxDispatcher.process_outbox_entry_by_id(outbox_entry.id)
        except Exception as e:
            logger.warning(
                f"Post-commit outbox dispatch for execution {execution.id} deferred to sweeper: {e}"
            )

        # 9. Publish initial SSE progress event
        try:
            redis = get_redis_client()
            await publish_execution_event(
                redis=redis,
                execution_id=str(execution.id),
                event_type="queued",
                sequence=1,
                payload={
                    "status": "QUEUED",
                    "execution_id": str(execution.id),
                    "agent_id": str(agent.id),
                    "input_hash": input_hash,
                },
            )
        except Exception as err:
            logger.warning(f"Failed to broadcast initial SSE event: {err}")

        # Return execution with relations loaded
        return await cls.get_execution_by_id(db, execution.id)  # type: ignore[return-value]

    @classmethod
    async def cancel_execution(
        cls,
        db: AsyncSession,
        execution_id: uuid.UUID,
        user_id: uuid.UUID,
        user_role: str,
        reason: str | None = None,
        request: Request | None = None,
    ) -> AgentExecution:
        """
        Request cancellation of an execution.
        Authorization: Only requester, agent owner, or admin.
        """
        execution = await cls.get_execution_by_id(db, execution_id)
        if not execution:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Execution '{execution_id}' not found",
            )

        # Authorization check
        is_requester = execution.requested_by == user_id
        is_owner = execution.agent.owner_user_id == user_id
        is_admin = user_role in (UserRole.ADMIN.value, UserRole.ADMIN)

        if not (is_requester or is_owner or is_admin):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You are not authorized to cancel this execution",
            )

        # Check terminal statuses
        terminal_statuses = {
            ExecutionStatus.SUCCEEDED.value,
            ExecutionStatus.FAILED.value,
            ExecutionStatus.TIMED_OUT.value,
            ExecutionStatus.CANCELLED.value,
        }
        if execution.status in terminal_statuses:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Execution cannot be cancelled because it is already {execution.status}",
            )

        redis = get_redis_client()
        # Signal cancellation to Redis
        await signal_cancellation(redis, str(execution_id))

        # Transition state
        await ExecutionStateMachine.transition_to_cancelled(
            db=db,
            execution=execution,
            reason=reason or "Cancellation requested by client",
        )

        # Audit event
        await AuditService.record_event(
            db=db,
            event_type=AuditEventType.AGENT_EXECUTION_CANCELLED,
            user_id=user_id,
            request=request,
            metadata={
                "execution_id": str(execution_id),
                "reason": reason,
            },
        )
        await db.commit()

        # Publish SSE event
        try:
            await publish_execution_event(
                redis=redis,
                execution_id=str(execution_id),
                event_type="cancelled",
                sequence=999,
                payload={
                    "status": "CANCELLED",
                    "execution_id": str(execution_id),
                    "reason": reason or "Cancellation requested",
                },
            )
        except Exception as err:
            logger.warning(f"Failed to publish cancel event: {err}")

        return execution

    @classmethod
    async def get_execution_by_id(
        cls,
        db: AsyncSession,
        execution_id: uuid.UUID,
    ) -> AgentExecution | None:
        query = (
            select(AgentExecution)
            .options(
                selectinload(AgentExecution.agent).selectinload(Agent.current_version),
                selectinload(AgentExecution.version),
                selectinload(AgentExecution.requester),
            )
            .where(AgentExecution.id == execution_id)
        )
        res = await db.execute(query)
        return res.scalar_one_or_none()

    @classmethod
    async def get_execution_by_idempotency_key(
        cls,
        db: AsyncSession,
        user_id: uuid.UUID,
        idempotency_key: str,
    ) -> AgentExecution | None:
        query = (
            select(AgentExecution)
            .options(
                selectinload(AgentExecution.agent).selectinload(Agent.current_version),
                selectinload(AgentExecution.version),
                selectinload(AgentExecution.requester),
            )
            .where(
                AgentExecution.requested_by == user_id,
                AgentExecution.idempotency_key == idempotency_key,
            )
        )
        res = await db.execute(query)
        return res.scalar_one_or_none()

    # Backward compatibility alias
    @classmethod
    async def execute_agent(
        cls,
        db: AsyncSession,
        agent_id: uuid.UUID,
        requested_by_user_id: uuid.UUID,
        input_data: dict[str, Any],
        request: Request | None = None,
        idempotency_key: str | None = None,
    ) -> AgentExecution:
        return await cls.enqueue_agent_execution(
            db=db,
            agent_id=agent_id,
            requested_by_user_id=requested_by_user_id,
            input_data=input_data,
            idempotency_key=idempotency_key,
            request=request,
        )
