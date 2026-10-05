import asyncio
from datetime import datetime, timedelta
import json
import logging
from typing import Any
import uuid

from redis.asyncio import Redis
from redis.exceptions import ResponseError
from sqlalchemy import distinct, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.core.metrics import metrics
from app.db.session import async_session_factory
from app.models.agent import AgentExecution, ExecutionStatus
from app.models.base import utc_now
from app.models.orchestration import (
    Orchestration,
    OrchestrationStatus,
    OrchestrationTask,
    OrchestrationTaskStatus,
)
from app.models.orchestration_outbox import (
    OrchestrationWakeupEventType,
    OrchestrationWakeupOutbox,
)
from app.services.orchestration_engine import orchestration_engine
from app.services.rate_limiter import get_redis_client

logger = logging.getLogger("agentchain.orchestration_recovery")


class OrchestrationWakeupDispatcher:
    """
    Durable Transactional Outbox Dispatcher for Orchestration Wake-ups.
    Reads pending wake-up outbox records from PostgreSQL and publishes them to Redis Streams.
    Guarantees at-least-once durable wake-up delivery without relying on Redis Pub/Sub.
    """

    @classmethod
    async def publish_entry(
        cls,
        db: AsyncSession,
        entry: OrchestrationWakeupOutbox,
        redis: Redis | None = None,
    ) -> bool:
        """
        Publishes a single outbox record to Redis Streams and (optionally) Redis Pub/Sub.
        Marks published_at only after successful publication.
        Retries transient failures with bounded exponential backoff.
        """
        if entry.published_at is not None:
            # Already published; idempotent early exit
            return True

        redis = redis or get_redis_client()
        stream_name = settings.ORCHESTRATION_WAKEUP_STREAM

        stream_dict = entry.to_stream_dict()

        try:
            # 1. Durable transport: Redis Streams
            msg_id = await redis.xadd(stream_name, stream_dict)

            # 2. Low-latency best-effort notification: Redis Pub/Sub
            try:
                raw_payload = json.dumps(stream_dict)
                await redis.publish(
                    f"agentchain:orchestration:{entry.orchestration_id}:wakeup",
                    raw_payload,
                )
                await redis.publish("agentchain:orchestration:wakeups", raw_payload)
            except Exception as pubsub_err:
                logger.debug(
                    f"Best-effort Pub/Sub broadcast skipped/failed for outbox {entry.id}: {pubsub_err}"
                )

            # 3. Mark durable record published
            now = utc_now()
            entry.published_at = now
            entry.last_error = None
            await db.flush()

            metrics.inc_counter("orchestration_wakeup_dispatch_total")
            logger.info(
                f"Durable wake-up outbox {entry.id} dispatched to stream {stream_name} (stream_id: {msg_id})",
                extra={
                    "orchestration_id": str(entry.orchestration_id),
                    "task_id": str(entry.orchestration_task_id) if entry.orchestration_task_id else None,
                    "execution_id": str(entry.execution_id),
                    "wakeup_event_id": str(entry.id),
                    "delivery_attempt": entry.attempts + 1,
                    "terminal_status": entry.terminal_status,
                },
            )
            return True

        except Exception as e:
            now = utc_now()
            entry.attempts += 1
            entry.last_error = str(e)[:1000]
            # Bounded exponential backoff: min(300s, 2 ** attempts)
            backoff_sec = min(300.0, float(2 ** min(entry.attempts, 8)))
            entry.available_at = now + timedelta(seconds=backoff_sec)
            await db.flush()

            metrics.inc_counter("orchestration_wakeup_dispatch_failure_total")
            metrics.inc_counter("orchestration_wakeup_dispatch_retry_total")
            logger.warning(
                f"Failed to dispatch wake-up outbox {entry.id} (attempt {entry.attempts}): {e}. Next retry in {backoff_sec}s",
                extra={
                    "orchestration_id": str(entry.orchestration_id),
                    "execution_id": str(entry.execution_id),
                    "wakeup_event_id": str(entry.id),
                    "delivery_attempt": entry.attempts,
                    "error": str(e),
                },
            )
            return False

    @classmethod
    async def dispatch_pending(
        cls,
        db: AsyncSession,
        limit: int = 50,
        redis: Redis | None = None,
    ) -> int:
        """
        Polls for unpublished wake-up outbox entries that are ready for delivery,
        and publishes them using row-level locking (FOR UPDATE SKIP LOCKED).
        """
        now = utc_now()
        query = (
            select(OrchestrationWakeupOutbox)
            .where(
                OrchestrationWakeupOutbox.published_at.is_(None),
                OrchestrationWakeupOutbox.available_at <= now,
            )
            .order_by(OrchestrationWakeupOutbox.created_at.asc())
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        res = await db.execute(query)
        entries = list(res.scalars().all())

        if not entries:
            return 0

        published_count = 0
        for entry in entries:
            success = await cls.publish_entry(db, entry, redis=redis)
            if success:
                published_count += 1

        await db.commit()
        return published_count

    @classmethod
    async def dispatch_entry_by_execution_id(
        cls,
        execution_id: uuid.UUID,
        redis: Redis | None = None,
    ) -> bool:
        """
        Processes an unpublished outbox record for a given execution immediately post-commit.
        Reduces latency while preserving database durability.
        """
        async with async_session_factory() as db:
            query = select(OrchestrationWakeupOutbox).where(
                OrchestrationWakeupOutbox.execution_id == execution_id,
                OrchestrationWakeupOutbox.published_at.is_(None),
            )
            res = await db.execute(query)
            entry = res.scalar_one_or_none()
            if not entry:
                return False

            success = await cls.publish_entry(db, entry, redis=redis)
            await db.commit()
            return success


class OrchestrationWakeupConsumer:
    """
    Durable Consumer for Orchestration Wake-up Events from Redis Streams.
    Reads events using Consumer Groups, invokes idempotent orchestration advancement,
    and acknowledges stream messages only after successful handoff.
    """

    def __init__(
        self,
        consumer_name: str | None = None,
        stream_name: str | None = None,
        group_name: str | None = None,
    ) -> None:
        self.stream_name = stream_name or settings.ORCHESTRATION_WAKEUP_STREAM
        self.group_name = group_name or settings.ORCHESTRATION_WAKEUP_GROUP
        self.consumer_name = consumer_name or f"orch-consumer-{uuid.uuid4().hex[:8]}"

    async def ensure_consumer_group(self, redis: Redis) -> None:
        """Ensures the consumer group exists on the stream."""
        try:
            await redis.xgroup_create(
                name=self.stream_name,
                groupname=self.group_name,
                id="0",
                mkstream=True,
            )
            logger.info(f"Created consumer group {self.group_name} on stream {self.stream_name}")
        except ResponseError as e:
            if "BUSYGROUP" in str(e):
                pass  # Group already exists
            else:
                raise

    async def consume_batch(
        self,
        redis: Redis,
        count: int = 10,
        block_ms: int = 1000,
    ) -> int:
        """
        Reads a batch of wake-up events from the Redis Stream,
        advances the corresponding orchestrations authoritatively,
        marks the outbox record processed in PostgreSQL,
        and acknowledges the message in Redis.
        """
        await self.ensure_consumer_group(redis)

        try:
            streams_res = await redis.xreadgroup(
                groupname=self.group_name,
                consumername=self.consumer_name,
                streams={self.stream_name: ">"},
                count=count,
                block=block_ms,
            )
        except Exception as read_err:
            logger.error(f"Error reading stream {self.stream_name}: {read_err}")
            return 0

        if not streams_res:
            return 0

        processed_count = 0
        for stream_key, messages in streams_res:
            for message_id, raw_data in messages:
                # Decode bytes to strings
                data: dict[str, str] = {}
                for k, v in raw_data.items():
                    key = k.decode("utf-8") if isinstance(k, bytes) else str(k)
                    val = v.decode("utf-8") if isinstance(v, bytes) else str(v)
                    data[key] = val

                event_id = data.get("event_id")
                orch_id_str = data.get("orchestration_id")
                exec_id_str = data.get("execution_id")
                task_id_str = data.get("task_id")
                status = data.get("terminal_status")
                correlation_id = data.get("correlation_id") or f"stream-{message_id}"

                if not orch_id_str or not exec_id_str:
                    # Invalid stream message, acknowledge to avoid poison loop
                    await redis.xack(self.stream_name, self.group_name, message_id)
                    continue

                try:
                    orch_id = uuid.UUID(orch_id_str)
                    exec_id = uuid.UUID(exec_id_str)
                    task_id = uuid.UUID(task_id_str) if task_id_str else None

                    attempt = int(data.get("attempt", 1)) if data.get("attempt") else None

                    # 1. Advance orchestration authoritatively
                    await orchestration_engine.handle_execution_completed_wakeup(
                        orchestration_id=orch_id,
                        execution_id=exec_id,
                        task_id=task_id,
                        status=status,
                        correlation_id=correlation_id,
                        attempt=attempt,
                        wakeup_event_id=event_id,
                    )

                    # 2. Mark outbox processed in PostgreSQL
                    if event_id:
                        async with async_session_factory() as db:
                            outbox = await db.get(OrchestrationWakeupOutbox, uuid.UUID(event_id))
                            if outbox and outbox.processed_at is None:
                                outbox.processed_at = utc_now()
                                await db.commit()

                    # 3. Acknowledge message in Redis Stream
                    await redis.xack(self.stream_name, self.group_name, message_id)
                    processed_count += 1

                except Exception as proc_err:
                    logger.error(
                        f"Error processing wake-up message {message_id} for orchestration {orch_id_str}: {proc_err}",
                        extra={
                            "orchestration_id": orch_id_str,
                            "execution_id": exec_id_str,
                            "error": str(proc_err),
                        },
                    )

        return processed_count


class OrchestrationReconciliationService:
    """
    Automatic Recovery Sweep / Reconciliation Service.
    Operates independently of Redis Pub/Sub and Redis Streams.
    Periodically inspects PostgreSQL to detect:
    1. Active (RUNNING) orchestrations where child AgentExecutions are in a terminal state
       (SUCCEEDED, FAILED, TIMED_OUT, CANCELLED) but the orchestration task is still non-terminal.
    2. Active orchestrations with stuck or pending wake-up outbox records.
    Guarantees:
    - Bounded execution (limit parameter).
    - Idempotent execution (advance_orchestration verifies authoritative state).
    - Safe concurrency: acquires PostgreSQL advisory lock (pg_try_advisory_xact_lock)
      before advancing. Skips without blocking if another worker/wake-up is active.
    - Observability: increments metrics and logs structured context.
    """

    @classmethod
    async def reconcile_stuck_orchestrations(
        cls,
        db: AsyncSession,
        stale_threshold_seconds: float = 10.0,
        limit: int = 50,
        orchestration_ids: list[uuid.UUID] | None = None,
    ) -> list[uuid.UUID]:
        """
        Identifies and recovers any orchestrations whose child executions completed
        without the orchestration being advanced.
        """
        stale_cutoff = utc_now() - timedelta(seconds=stale_threshold_seconds)

        # 1. Find RUNNING orchestrations with tasks whose child AgentExecution is terminal
        terminal_statuses = [
            ExecutionStatus.SUCCEEDED.value,
            ExecutionStatus.FAILED.value,
            ExecutionStatus.TIMED_OUT.value,
            ExecutionStatus.CANCELLED.value,
        ]
        non_terminal_task_statuses = [
            OrchestrationTaskStatus.PENDING.value,
            OrchestrationTaskStatus.READY.value,
            OrchestrationTaskStatus.RUNNING.value,
        ]

        task_conditions = [
            Orchestration.status == OrchestrationStatus.RUNNING.value,
            OrchestrationTask.status.in_(non_terminal_task_statuses),
            AgentExecution.status.in_(terminal_statuses),
        ]
        if orchestration_ids:
            task_conditions.append(Orchestration.id.in_(orchestration_ids))

        stuck_tasks_query = (
            select(OrchestrationTask.orchestration_id)
            .join(AgentExecution, OrchestrationTask.execution_id == AgentExecution.id)
            .join(Orchestration, OrchestrationTask.orchestration_id == Orchestration.id)
            .where(*task_conditions)
            .distinct()
            .limit(limit)
        )
        tasks_res = await db.execute(stuck_tasks_query)
        stuck_by_task = set(tasks_res.scalars().all())

        # 2. Find RUNNING orchestrations with stuck/unprocessed wake-up outbox entries
        outbox_conditions = [
            Orchestration.status == OrchestrationStatus.RUNNING.value,
            (
                (
                    OrchestrationWakeupOutbox.published_at.is_(None)
                    & (OrchestrationWakeupOutbox.available_at <= stale_cutoff)
                )
                | (
                    OrchestrationWakeupOutbox.processed_at.is_(None)
                    & (OrchestrationWakeupOutbox.created_at <= stale_cutoff)
                )
            ),
        ]
        if orchestration_ids:
            outbox_conditions.append(Orchestration.id.in_(orchestration_ids))

        stuck_outbox_query = (
            select(OrchestrationWakeupOutbox.orchestration_id)
            .join(Orchestration, OrchestrationWakeupOutbox.orchestration_id == Orchestration.id)
            .where(*outbox_conditions)
            .distinct()
            .limit(limit)
        )
        outbox_res = await db.execute(stuck_outbox_query)
        stuck_by_outbox = set(outbox_res.scalars().all())

        all_candidate_ids = list(stuck_by_task.union(stuck_by_outbox))[:limit]

        if not all_candidate_ids:
            return []

        reconciled_ids: list[uuid.UUID] = []
        for orch_id in all_candidate_ids:
            correlation_id = f"reconcile-{uuid.uuid4().hex[:8]}"
            recovery_reason = (
                "terminal_execution_unadvanced"
                if orch_id in stuck_by_task
                else "stuck_wakeup_outbox"
            )

            metrics.inc_counter("orchestration_wakeup_reconciliation_total")
            metrics.inc_counter("orchestration_recovery_total")
            metrics.inc_counter("orchestration_wakeup_recovery_total")
            if recovery_reason == "stuck_wakeup_outbox":
                metrics.inc_counter("orchestration_wakeup_recovery_stale_total")

            logger.info(
                f"Reconciling stuck orchestration {orch_id} (reason: {recovery_reason})",
                extra={
                    "orchestration_id": str(orch_id),
                    "correlation_id": correlation_id,
                    "recovery_reason": recovery_reason,
                    "event_type": "ORCHESTRATION_RECONCILIATION",
                },
            )

            # Advance orchestration using PostgreSQL advisory lock
            try:
                state = await orchestration_engine.advance_orchestration(
                    orchestration_id=orch_id,
                    correlation_id=correlation_id,
                )
                if state is not None:
                    reconciled_ids.append(orch_id)
            except Exception as adv_err:
                logger.error(
                    f"Error advancing orchestration {orch_id} during reconciliation: {adv_err}",
                    extra={
                        "orchestration_id": str(orch_id),
                        "correlation_id": correlation_id,
                        "error": str(adv_err),
                    },
                )

        return reconciled_ids

    @classmethod
    async def run_reconciliation_cycle(
        cls,
        stale_threshold_seconds: float = 10.0,
        limit: int = 50,
        redis: Redis | None = None,
    ) -> dict[str, int]:
        """
        Executes a single pass of outbox dispatch followed by database state reconciliation.
        """
        async with async_session_factory() as db:
            # 1. Dispatch pending outbox records
            dispatched = await OrchestrationWakeupDispatcher.dispatch_pending(
                db=db, limit=limit, redis=redis
            )

            # 2. Reconcile any stuck orchestrations
            reconciled = await cls.reconcile_stuck_orchestrations(
                db=db,
                stale_threshold_seconds=stale_threshold_seconds,
                limit=limit,
            )

            return {
                "outbox_dispatched": dispatched,
                "orchestrations_reconciled": len(reconciled),
            }
