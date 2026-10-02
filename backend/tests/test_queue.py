import asyncio
import uuid
import pytest

from app.models.base import utc_now
from app.services.queue import (
    DEFAULT_QUEUE_NAME,
    QueueJob,
    RedisStreamExecutionQueue,
    get_execution_queue,
)
from app.services.rate_limiter import get_redis_client


@pytest.mark.asyncio
async def test_queue_enqueue_and_claim():
    queue = RedisStreamExecutionQueue()
    test_queue = f"test_queue_{uuid.uuid4().hex[:8]}"

    job = QueueJob(
        execution_id=str(uuid.uuid4()),
        agent_id=str(uuid.uuid4()),
        agent_version_id=str(uuid.uuid4()),
        requested_by=str(uuid.uuid4()),
        input_hash="a" * 64,
        attempt_number=0,
        created_at=utc_now().isoformat(),
        correlation_id=str(uuid.uuid4()),
        queue_name=test_queue,
        input_data={"test": "data"},
    )

    msg_id = await queue.enqueue_execution(job)
    assert msg_id is not None
    assert isinstance(msg_id, str)

    # Claim job
    worker_id = f"test-worker-{uuid.uuid4().hex[:6]}"
    claimed = await queue.claim_execution(
        worker_id=worker_id,
        queue_name=test_queue,
        batch_size=1,
        block_ms=1000,
    )

    assert len(claimed) == 1
    c_job = claimed[0]
    assert c_job.execution_id == job.execution_id
    assert c_job.agent_id == job.agent_id
    assert c_job.agent_version_id == job.agent_version_id
    assert c_job.requested_by == job.requested_by
    assert c_job.input_hash == job.input_hash
    assert c_job.attempt_number == 0
    assert c_job.correlation_id == job.correlation_id
    assert c_job.input_data == {"test": "data"}

    # Cleanup
    await queue.ack_execution(c_job.message_id, test_queue)


@pytest.mark.asyncio
async def test_queue_acknowledgement():
    queue = RedisStreamExecutionQueue()
    test_queue = f"test_ack_{uuid.uuid4().hex[:8]}"

    job = QueueJob(
        execution_id=str(uuid.uuid4()),
        agent_id=str(uuid.uuid4()),
        agent_version_id=str(uuid.uuid4()),
        requested_by=str(uuid.uuid4()),
        input_hash="b" * 64,
        attempt_number=0,
        created_at=utc_now().isoformat(),
        correlation_id=str(uuid.uuid4()),
        queue_name=test_queue,
    )
    await queue.enqueue_execution(job)

    worker_id = f"worker-ack-{uuid.uuid4().hex[:6]}"
    claimed = await queue.claim_execution(worker_id, queue_name=test_queue, batch_size=1)
    assert len(claimed) == 1

    # Ack message
    await queue.ack_execution(claimed[0].message_id, test_queue)

    # Verify no more pending for this worker
    pending = await queue.redis.xpending(queue._stream_name(test_queue), "agentchain:workers")
    assert pending["pending"] == 0


@pytest.mark.asyncio
async def test_queue_requeue_and_dead_letter():
    queue = RedisStreamExecutionQueue()
    test_queue = f"test_retry_{uuid.uuid4().hex[:8]}"

    job = QueueJob(
        execution_id=str(uuid.uuid4()),
        agent_id=str(uuid.uuid4()),
        agent_version_id=str(uuid.uuid4()),
        requested_by=str(uuid.uuid4()),
        input_hash="c" * 64,
        attempt_number=0,
        created_at=utc_now().isoformat(),
        correlation_id=str(uuid.uuid4()),
        queue_name=test_queue,
    )
    await queue.enqueue_execution(job)

    worker_id = f"worker-retry-{uuid.uuid4().hex[:6]}"
    claimed = await queue.claim_execution(worker_id, queue_name=test_queue, batch_size=1)
    assert len(claimed) == 1
    c_job = claimed[0]

    # Requeue attempt 1 (max_attempts = 2)
    new_msg_id = await queue.requeue_execution(c_job, delay_seconds=0.0, max_attempts=2)
    assert new_msg_id is not None
    assert c_job.attempt_number == 1

    # Claim again
    claimed2 = await queue.claim_execution(worker_id, queue_name=test_queue, batch_size=1)
    assert len(claimed2) == 1
    assert claimed2[0].attempt_number == 1

    # Requeue attempt 2 -> exceeds max_attempts (2) -> dead letter!
    dead_id = await queue.requeue_execution(claimed2[0], delay_seconds=0.0, max_attempts=2)
    assert dead_id is None  # Moved to dead letter

    # Check dead letter stream
    dl_stream = queue._dead_letter_stream(test_queue)
    dl_len = await queue.redis.xlen(dl_stream)
    assert dl_len >= 1


@pytest.mark.asyncio
async def test_queue_depth_and_stale_reclamation():
    queue = RedisStreamExecutionQueue()
    test_queue = f"test_stale_{uuid.uuid4().hex[:8]}"

    job = QueueJob(
        execution_id=str(uuid.uuid4()),
        agent_id=str(uuid.uuid4()),
        agent_version_id=str(uuid.uuid4()),
        requested_by=str(uuid.uuid4()),
        input_hash="d" * 64,
        attempt_number=0,
        created_at=utc_now().isoformat(),
        correlation_id=str(uuid.uuid4()),
        queue_name=test_queue,
    )
    await queue.enqueue_execution(job)

    depth = await queue.get_queue_depth(test_queue)
    assert depth >= 1

    # Claim by dead worker
    dead_worker = "crashed-worker-999"
    claimed = await queue.claim_execution(dead_worker, queue_name=test_queue, batch_size=1)
    assert len(claimed) == 1

    # Reclaim with 0 threshold (immediately stale for testing)
    live_worker = "live-worker-100"
    reclaimed = await queue.reclaim_stale_executions(
        worker_id=live_worker,
        queue_name=test_queue,
        stale_threshold_seconds=0,
        batch_size=10,
    )
    assert len(reclaimed) == 1
    assert reclaimed[0].execution_id == job.execution_id

    # Clean up
    await queue.ack_execution(reclaimed[0].message_id, test_queue)
