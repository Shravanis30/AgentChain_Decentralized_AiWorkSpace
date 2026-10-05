from datetime import datetime, timedelta
import logging
import time
import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.metrics import metrics
from app.db.session import async_session_factory
from app.models.agent import AgentExecution
from app.models.base import utc_now
from app.models.outbox import ExecutionOutbox, OutboxEventType
from app.services.queue import ExecutionQueue, QueueJob, get_execution_queue

logger = logging.getLogger("agentchain.outbox")


class OutboxDispatcher:
    """
    Transactional Outbox Dispatcher.
    Guarantees reliable, at-least-once publishing of queued agent executions to Redis Streams.
    Avoids the PostgreSQL commit -> Redis failure gap.
    """

    @classmethod
    async def create_outbox_entry(
        cls,
        db: AsyncSession,
        execution_id: uuid.UUID,
        payload: dict[str, Any],
        event_type: str = OutboxEventType.EXECUTION_ENQUEUED.value,
    ) -> ExecutionOutbox:
        """
        Creates an unpublished ExecutionOutbox record within the caller's active database transaction.
        Must be committed atomically with the AgentExecution record.
        """
        now = utc_now()
        outbox = ExecutionOutbox(
            execution_id=execution_id,
            event_type=event_type,
            payload=payload,
            created_at=now,
            available_at=now,
            published_at=None,
            attempts=0,
        )
        db.add(outbox)
        await db.flush()
        return outbox

    @classmethod
    async def publish_entry(
        cls,
        db: AsyncSession,
        entry: ExecutionOutbox,
        queue: ExecutionQueue | None = None,
    ) -> bool:
        """
        Attempts to publish a single outbox entry to Redis Streams.
        On success, marks published_at, updates execution queue_message_id, and records metrics.
        On failure, increments attempts, calculates exponential backoff, and leaves published_at NULL.
        """
        if entry.published_at is not None:
            # Already published; avoid duplicate logical dispatch
            return True

        queue = queue or get_execution_queue()
        payload = entry.payload
        start_time = time.perf_counter()

        job = QueueJob(
            execution_id=str(payload["execution_id"]),
            agent_id=str(payload["agent_id"]),
            agent_version_id=str(payload["agent_version_id"]),
            requested_by=str(payload["requested_by"]),
            input_hash=str(payload["input_hash"]),
            attempt_number=int(payload.get("attempt_number", 0)),
            created_at=str(payload.get("created_at", utc_now().isoformat())),
            correlation_id=str(payload.get("correlation_id", "")),
            queue_name=str(payload.get("queue_name", "agent_executions")),
            input_data=payload.get("input_data"),
        )

        try:
            msg_id = await queue.enqueue_execution(job)
            now = utc_now()
            entry.published_at = now
            entry.last_error = None

            # Update the parent execution with the queue message ID if present
            execution = await db.get(AgentExecution, entry.execution_id)
            if execution:
                execution.queue_message_id = msg_id

            await db.flush()

            latency_s = time.perf_counter() - start_time
            metrics.observe_histogram("outbox_publish_latency_seconds", latency_s)
            metrics.inc_counter("outbox_publish_success_total")
            logger.info(
                f"Outbox event {entry.id} for execution {entry.execution_id} published to Redis ({msg_id}) in {latency_s*1000:.1f}ms"
            )
            return True

        except Exception as e:
            now = utc_now()
            entry.attempts += 1
            entry.last_error = str(e)[:1000]
            # Exponential backoff: min(300s, 2 ** attempts)
            backoff_sec = min(300.0, float(2 ** min(entry.attempts, 8)))
            entry.available_at = now + timedelta(seconds=backoff_sec)
            await db.flush()

            metrics.inc_counter("outbox_publish_failure_total")
            logger.warning(
                f"Failed to publish outbox event {entry.id} (attempt {entry.attempts}): {e}. Next retry in {backoff_sec}s"
            )
            return False

    @classmethod
    async def dispatch_pending(
        cls,
        db: AsyncSession,
        limit: int = 50,
        queue: ExecutionQueue | None = None,
    ) -> int:
        """
        Polls for unpublished outbox entries that are ready for delivery,
        and attempts to publish them.
        """
        now = utc_now()
        # Update pending gauge
        pending_count_res = await db.execute(
            select(func.count(ExecutionOutbox.id)).where(ExecutionOutbox.published_at.is_(None))
        )
        pending_count = pending_count_res.scalar() or 0
        metrics.set_gauge("outbox_pending", float(pending_count))

        query = (
            select(ExecutionOutbox)
            .where(
                ExecutionOutbox.published_at.is_(None),
                ExecutionOutbox.available_at <= now,
            )
            .order_by(ExecutionOutbox.created_at.asc())
            .limit(limit)
        )
        res = await db.execute(query)
        entries = list(res.scalars().all())

        if not entries:
            return 0

        published_count = 0
        for entry in entries:
            success = await cls.publish_entry(db, entry, queue=queue)
            if success:
                published_count += 1

        await db.commit()
        return published_count

    @classmethod
    async def process_outbox_entry_by_id(
        cls,
        outbox_id: uuid.UUID,
        queue: ExecutionQueue | None = None,
    ) -> bool:
        """
        Processes a single outbox entry by ID in a new session.
        Useful for immediate post-commit dispatch.
        """
        async with async_session_factory() as db:
            entry = await db.get(ExecutionOutbox, outbox_id)
            if not entry or entry.published_at is not None:
                return False
            success = await cls.publish_entry(db, entry, queue=queue)
            await db.commit()
            return success
