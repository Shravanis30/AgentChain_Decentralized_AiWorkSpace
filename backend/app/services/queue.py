from abc import ABC, abstractmethod
import asyncio
from dataclasses import asdict, dataclass
import json
import logging
from typing import Any
from redis.asyncio import Redis

from app.models.base import utc_now
from app.services.rate_limiter import get_redis_client

logger = logging.getLogger(__name__)

DEFAULT_QUEUE_NAME = "agent_executions"
DEFAULT_CONSUMER_GROUP = "agentchain:workers"


@dataclass
class QueueJob:
    execution_id: str
    agent_id: str
    agent_version_id: str
    requested_by: str
    input_hash: str
    attempt_number: int
    created_at: str
    correlation_id: str
    queue_name: str = DEFAULT_QUEUE_NAME
    message_id: str | None = None
    input_data: dict[str, Any] | None = None

    def to_stream_dict(self) -> dict[str, str]:
        data = {
            "execution_id": self.execution_id,
            "agent_id": self.agent_id,
            "agent_version_id": self.agent_version_id,
            "requested_by": self.requested_by,
            "input_hash": self.input_hash,
            "attempt_number": str(self.attempt_number),
            "created_at": self.created_at,
            "correlation_id": self.correlation_id,
            "queue_name": self.queue_name,
        }
        if self.input_data is not None:
            data["input_data"] = json.dumps(self.input_data)
        return data

    @classmethod
    def from_stream_dict(cls, message_id: str, data: dict[bytes | str, bytes | str]) -> "QueueJob":
        parsed = {}
        for k, v in data.items():
            key = k.decode("utf-8") if isinstance(k, bytes) else k
            val = v.decode("utf-8") if isinstance(v, bytes) else v
            parsed[key] = val

        input_data = None
        if "input_data" in parsed and parsed["input_data"]:
            try:
                input_data = json.loads(parsed["input_data"])
            except Exception:
                pass

        return cls(
            execution_id=parsed["execution_id"],
            agent_id=parsed["agent_id"],
            agent_version_id=parsed["agent_version_id"],
            requested_by=parsed["requested_by"],
            input_hash=parsed["input_hash"],
            attempt_number=int(parsed.get("attempt_number", 0)),
            created_at=parsed["created_at"],
            correlation_id=parsed.get("correlation_id", ""),
            queue_name=parsed.get("queue_name", DEFAULT_QUEUE_NAME),
            message_id=message_id,
            input_data=input_data,
        )


class ExecutionQueue(ABC):
    @abstractmethod
    async def enqueue_execution(self, job: QueueJob) -> str:
        """Enqueue execution job to durable storage and return queue message ID."""
        pass

    @abstractmethod
    async def claim_execution(
        self,
        worker_id: str,
        queue_name: str = DEFAULT_QUEUE_NAME,
        batch_size: int = 1,
        block_ms: int = 2000,
    ) -> list[QueueJob]:
        """Claim one or more jobs for the given worker."""
        pass

    @abstractmethod
    async def ack_execution(
        self,
        message_id: str,
        queue_name: str = DEFAULT_QUEUE_NAME,
    ) -> None:
        """Acknowledge completed processing of a job."""
        pass

    @abstractmethod
    async def requeue_execution(
        self,
        job: QueueJob,
        delay_seconds: float = 0.0,
        max_attempts: int = 3,
    ) -> str | None:
        """Requeue job for transient retry, or move to dead letter if exceeded max attempts."""
        pass

    @abstractmethod
    async def dead_letter_execution(
        self,
        job: QueueJob,
        reason: str,
    ) -> str:
        """Move job to dead-letter queue."""
        pass

    @abstractmethod
    async def get_queue_depth(self, queue_name: str = DEFAULT_QUEUE_NAME) -> int:
        """Get number of pending/queued messages in the queue."""
        pass

    @abstractmethod
    async def reclaim_stale_executions(
        self,
        worker_id: str,
        queue_name: str = DEFAULT_QUEUE_NAME,
        stale_threshold_seconds: int = 60,
        batch_size: int = 10,
    ) -> list[QueueJob]:
        """Reclaim jobs abandoned by crashed/stalled workers."""
        pass


class RedisStreamExecutionQueue(ExecutionQueue):
    def __init__(self, redis: Redis | None = None):
        self._redis = redis

    @property
    def redis(self) -> Redis:
        if self._redis is None:
            self._redis = get_redis_client()
        return self._redis

    def _stream_name(self, queue_name: str) -> str:
        return f"agentchain:stream:{queue_name}"

    def _dead_letter_stream(self, queue_name: str) -> str:
        return f"agentchain:dead_letter:{queue_name}"

    async def ensure_group(self, queue_name: str = DEFAULT_QUEUE_NAME) -> None:
        stream_name = self._stream_name(queue_name)
        try:
            await self.redis.xgroup_create(
                name=stream_name,
                groupname=DEFAULT_CONSUMER_GROUP,
                id="0",
                mkstream=True,
            )
        except Exception as e:
            if "BUSYGROUP" not in str(e):
                logger.debug(f"Consumer group setup note: {e}")

    async def enqueue_execution(self, job: QueueJob) -> str:
        stream_name = self._stream_name(job.queue_name)
        await self.ensure_group(job.queue_name)
        fields = job.to_stream_dict()
        msg_id = await self.redis.xadd(stream_name, fields)
        if isinstance(msg_id, bytes):
            msg_id = msg_id.decode("utf-8")
        job.message_id = msg_id
        return msg_id

    async def claim_execution(
        self,
        worker_id: str,
        queue_name: str = DEFAULT_QUEUE_NAME,
        batch_size: int = 1,
        block_ms: int = 2000,
    ) -> list[QueueJob]:
        stream_name = self._stream_name(queue_name)
        await self.ensure_group(queue_name)

        # 1. First check if this worker already has pending uncompleted messages
        # Read from pending ("0") first to recover any in-flight items
        pending_res = await self.redis.xreadgroup(
            groupname=DEFAULT_CONSUMER_GROUP,
            consumername=worker_id,
            streams={stream_name: "0"},
            count=batch_size,
        )

        jobs: list[QueueJob] = []
        if pending_res:
            for s_name, messages in pending_res:
                for msg_id, fields in messages:
                    if fields:  # Non-empty payload
                        msg_id_str = msg_id.decode("utf-8") if isinstance(msg_id, bytes) else str(msg_id)
                        jobs.append(QueueJob.from_stream_dict(msg_id_str, fields))
                        if len(jobs) >= batch_size:
                            return jobs

        # 2. Otherwise read new messages (">")
        res = await self.redis.xreadgroup(
            groupname=DEFAULT_CONSUMER_GROUP,
            consumername=worker_id,
            streams={stream_name: ">"},
            count=batch_size,
            block=block_ms,
        )

        if not res:
            return jobs

        for s_name, messages in res:
            for msg_id, fields in messages:
                msg_id_str = msg_id.decode("utf-8") if isinstance(msg_id, bytes) else str(msg_id)
                jobs.append(QueueJob.from_stream_dict(msg_id_str, fields))

        return jobs

    async def ack_execution(
        self,
        message_id: str,
        queue_name: str = DEFAULT_QUEUE_NAME,
    ) -> None:
        stream_name = self._stream_name(queue_name)
        await self.redis.xack(stream_name, DEFAULT_CONSUMER_GROUP, message_id)

    async def requeue_execution(
        self,
        job: QueueJob,
        delay_seconds: float = 0.0,
        max_attempts: int = 3,
    ) -> str | None:
        # Acknowledge original message to remove from pending
        if job.message_id:
            await self.ack_execution(job.message_id, job.queue_name)

        job.attempt_number += 1
        if job.attempt_number >= max_attempts:
            logger.warning(
                f"Job {job.execution_id} exceeded max attempts ({job.attempt_number}/{max_attempts}). Moving to dead letter."
            )
            await self.dead_letter_execution(job, reason="MAX_ATTEMPTS_EXCEEDED")
            return None

        if delay_seconds > 0:
            await asyncio.sleep(delay_seconds)

        return await self.enqueue_execution(job)

    async def dead_letter_execution(
        self,
        job: QueueJob,
        reason: str,
    ) -> str:
        dead_stream = self._dead_letter_stream(job.queue_name)
        fields = job.to_stream_dict()
        fields["dead_letter_reason"] = reason
        fields["dead_letter_at"] = utc_now().isoformat()
        msg_id = await self.redis.xadd(dead_stream, fields)
        if job.message_id:
            await self.ack_execution(job.message_id, job.queue_name)
        return msg_id.decode("utf-8") if isinstance(msg_id, bytes) else str(msg_id)

    async def get_queue_depth(self, queue_name: str = DEFAULT_QUEUE_NAME) -> int:
        stream_name = self._stream_name(queue_name)
        try:
            return await self.redis.xlen(stream_name)
        except Exception:
            return 0

    async def reclaim_stale_executions(
        self,
        worker_id: str,
        queue_name: str = DEFAULT_QUEUE_NAME,
        stale_threshold_seconds: int = 60,
        batch_size: int = 10,
    ) -> list[QueueJob]:
        stream_name = self._stream_name(queue_name)
        min_idle_ms = stale_threshold_seconds * 1000

        try:
            # Check pending entries
            pending = await self.redis.xpending_range(
                stream_name,
                DEFAULT_CONSUMER_GROUP,
                min="-",
                max="+",
                count=batch_size,
            )
            if not pending:
                return []

            candidate_ids = []
            for item in pending:
                # item format: {'message_id': ..., 'consumer': ..., 'idle': ..., 'times_delivered': ...}
                idle = item.get("idle", 0) if isinstance(item, dict) else (item[2] if len(item) > 2 else 0)
                msg_id = item.get("message_id") if isinstance(item, dict) else item[0]
                if idle >= min_idle_ms:
                    candidate_ids.append(msg_id)

            if not candidate_ids:
                return []

            # Claim messages for this worker
            claimed = await self.redis.xclaim(
                stream_name,
                DEFAULT_CONSUMER_GROUP,
                worker_id,
                min_idle_time=min_idle_ms,
                message_ids=candidate_ids,
            )

            jobs = []
            for msg_id, fields in claimed:
                msg_id_str = msg_id.decode("utf-8") if isinstance(msg_id, bytes) else str(msg_id)
                jobs.append(QueueJob.from_stream_dict(msg_id_str, fields))
            return jobs
        except Exception as e:
            logger.warning(f"Error reclaiming stale executions: {e}")
            return []


# Global queue singleton
_execution_queue: ExecutionQueue | None = None


def get_execution_queue() -> ExecutionQueue:
    global _execution_queue
    if _execution_queue is None:
        _execution_queue = RedisStreamExecutionQueue()
    return _execution_queue


# Event publishing and cancellation signaling
async def publish_execution_event(
    redis: Redis,
    execution_id: str,
    event_type: str,
    sequence: int,
    payload: dict[str, Any],
) -> None:
    """Publish real-time execution lifecycle events for SSE clients."""
    event_data = {
        "execution_id": execution_id,
        "event_type": event_type,
        "timestamp": utc_now().isoformat(),
        "sequence": sequence,
        "payload": payload,
    }
    channel = f"agentchain:events:{execution_id}"
    await redis.publish(channel, json.dumps(event_data))


async def signal_cancellation(redis: Redis, execution_id: str) -> None:
    """Store cancellation flag in Redis and broadcast cancel event to active workers."""
    await redis.set(f"agentchain:cancel:{execution_id}", "1", ex=3600)
    await redis.publish(
        f"agentchain:cancellations",
        json.dumps({"execution_id": execution_id, "timestamp": utc_now().isoformat()}),
    )


async def is_cancellation_requested(redis: Redis, execution_id: str) -> bool:
    val = await redis.get(f"agentchain:cancel:{execution_id}")
    return val is not None


async def publish_orchestration_event(
    redis: Redis,
    orchestration_id: str,
    event_type: str,
    sequence: int,
    payload: dict[str, Any],
) -> None:
    """Publish real-time orchestration lifecycle events for SSE clients and history buffer."""
    event_data = {
        "orchestration_id": orchestration_id,
        "event_type": event_type,
        "timestamp": utc_now().isoformat(),
        "sequence": sequence,
        "payload": payload,
    }
    raw = json.dumps(event_data)
    channel = f"agentchain:orchestration:{orchestration_id}:events"
    history_key = f"agentchain:orchestration:{orchestration_id}:history"

    await redis.publish(channel, raw)
    await redis.rpush(history_key, raw)
    await redis.expire(history_key, 3600)


async def publish_orchestration_wakeup(
    redis: Redis,
    orchestration_id: str,
    execution_id: str,
    task_id: str | None = None,
    status: str = "SUCCEEDED",
) -> None:
    """
    Publish a bounded execution completion event to trigger orchestration wake-up.
    Contains ONLY bounded identifiers/metadata. PostgreSQL remains authoritative.
    """
    event_data = {
        "event_type": "AGENT_EXECUTION_COMPLETED",
        "execution_id": execution_id,
        "orchestration_id": orchestration_id,
        "orchestration_task_id": task_id,
        "status": status,
        "timestamp": utc_now().isoformat(),
    }
    raw = json.dumps(event_data)
    await redis.publish(f"agentchain:orchestration:{orchestration_id}:wakeup", raw)
    await redis.publish("agentchain:orchestration:wakeups", raw)


