import asyncio
import uuid
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agentchain_worker.sandbox.local_runner import LocalProcessSandboxRunner
from agentchain_worker.worker import AgentExecutionWorker
from app.agents.research_agent import RESEARCH_AGENT_MANIFEST
from app.core.metrics import metrics
from app.models.agent import AgentExecution, ExecutionStatus
from app.models.outbox import ExecutionOutbox
from app.services.outbox import OutboxDispatcher
from app.services.queue import ExecutionQueue, QueueJob, get_execution_queue
from app.services.state_machine import ExecutionStateMachine
from tests.test_agents import create_authenticated_user


class FailingQueue(ExecutionQueue):
    """Queue simulation that fails on enqueue to simulate Redis outage."""
    def __init__(self, real_queue: ExecutionQueue):
        self.real_queue = real_queue
        self.fail = True

    async def enqueue_execution(self, job: QueueJob) -> str:
        if self.fail:
            raise ConnectionError("Simulated Redis connection failure")
        return await self.real_queue.enqueue_execution(job)

    async def claim_execution(self, *args, **kwargs):
        return await self.real_queue.claim_execution(*args, **kwargs)

    async def ack_execution(self, *args, **kwargs):
        return await self.real_queue.ack_execution(*args, **kwargs)

    async def requeue_execution(self, *args, **kwargs):
        return await self.real_queue.requeue_execution(*args, **kwargs)

    async def dead_letter_execution(self, *args, **kwargs):
        return await self.real_queue.dead_letter_execution(*args, **kwargs)

    async def reclaim_stale_executions(self, *args, **kwargs):
        return await self.real_queue.reclaim_stale_executions(*args, **kwargs)

    async def get_queue_depth(self, *args, **kwargs):
        return await self.real_queue.get_queue_depth(*args, **kwargs)


@pytest.mark.asyncio
async def test_case_a_redis_unavailable_then_recovers(async_client, db_session: AsyncSession):
    """
    Case A: PostgreSQL commit succeeds. Redis unavailable.
    Expected:
    - execution = QUEUED
    - outbox = PENDING (published_at is None)
    After Redis recovers:
    - outbox dispatcher -> Redis -> worker executes to SUCCEEDED
    """
    dev_token, _, _ = await create_authenticated_user(async_client, db_session)
    client_token, client_user_id, _ = await create_authenticated_user(async_client, db_session)

    manifest = RESEARCH_AGENT_MANIFEST.model_dump()
    slug = f"recov-a-{uuid.uuid4().hex[:8]}"
    create_res = await async_client.post(
        "/api/v1/agents",
        json={"name": "Recovery Agent A", "slug": slug, "manifest": manifest},
        headers={"Authorization": f"Bearer {dev_token}"},
    )
    agent_id = create_res.json()["id"]
    await async_client.post(f"/api/v1/agents/{agent_id}/publish", headers={"Authorization": f"Bearer {dev_token}"})

    real_queue = get_execution_queue()
    failing_queue = FailingQueue(real_queue)

    # 1. Simulate dispatch when Redis is down
    from app.services.agent_execution import AgentExecutionService
    # Temporarily monkeypatch get_execution_queue in outbox
    import app.services.outbox as outbox_mod
    orig_get_queue = outbox_mod.get_execution_queue
    outbox_mod.get_execution_queue = lambda: failing_queue

    try:
        exec_res = await async_client.post(
            f"/api/v1/agents/{agent_id}/execute",
            json={"input": {"text": "Recovery Case A testing."}},
            headers={"Authorization": f"Bearer {client_token}"},
        )
        assert exec_res.status_code == 200
        execution_id = uuid.UUID(exec_res.json()["execution_id"])

        # Verify DB state: execution is QUEUED
        exec_record = await db_session.get(AgentExecution, execution_id)
        assert exec_record is not None
        assert exec_record.status == ExecutionStatus.QUEUED.value

        # Verify outbox state: unpublished with attempt recorded
        outbox_res = await db_session.execute(
            select(ExecutionOutbox).where(ExecutionOutbox.execution_id == execution_id)
        )
        outbox_entry = outbox_res.scalar_one_or_none()
        assert outbox_entry is not None
        assert outbox_entry.published_at is None
        assert outbox_entry.attempts >= 1
        assert "Simulated Redis connection failure" in (outbox_entry.last_error or "")

        # 2. Redis recovers!
        failing_queue.fail = False
        outbox_entry.available_at = outbox_entry.created_at  # Reset backoff for immediate test run
        await db_session.flush()

        published_count = await OutboxDispatcher.dispatch_pending(db=db_session, limit=10, queue=failing_queue)
        assert published_count >= 1

        # Verify outbox entry is now published
        await db_session.refresh(outbox_entry)
        assert outbox_entry.published_at is not None

        # Verify worker claims and completes execution
        worker = AgentExecutionWorker(queue=real_queue, sandbox_runner=LocalProcessSandboxRunner())
        jobs = await real_queue.claim_execution(worker_id="worker-case-a", batch_size=1, block_ms=1000)
        assert len(jobs) >= 1
        job = next(j for j in jobs if j.execution_id == str(execution_id))
        processed = await worker.process_job(job)
        assert processed is True

        await db_session.refresh(exec_record)
        assert exec_record.status == ExecutionStatus.SUCCEEDED.value

    finally:
        outbox_mod.get_execution_queue = orig_get_queue


@pytest.mark.asyncio
async def test_case_b_dispatcher_crash_before_mark_published_duplicate_publication_idempotent(
    async_client, db_session: AsyncSession
):
    """
    Case B: Redis publish succeeds. Dispatcher crashes before marking outbox published.
    Expected: duplicate publication may occur, but execution and finalization remain idempotent.
    """
    dev_token, _, _ = await create_authenticated_user(async_client, db_session)
    client_token, _, _ = await create_authenticated_user(async_client, db_session)

    manifest = RESEARCH_AGENT_MANIFEST.model_dump()
    slug = f"recov-b-{uuid.uuid4().hex[:8]}"
    create_res = await async_client.post(
        "/api/v1/agents",
        json={"name": "Recovery Agent B", "slug": slug, "manifest": manifest},
        headers={"Authorization": f"Bearer {dev_token}"},
    )
    agent_id = create_res.json()["id"]
    await async_client.post(f"/api/v1/agents/{agent_id}/publish", headers={"Authorization": f"Bearer {dev_token}"})

    exec_res = await async_client.post(
        f"/api/v1/agents/{agent_id}/execute",
        json={"input": {"text": "Recovery Case B duplicate text."}},
        headers={"Authorization": f"Bearer {client_token}"},
    )
    execution_id = uuid.UUID(exec_res.json()["execution_id"])

    real_queue = get_execution_queue()
    worker = AgentExecutionWorker(queue=real_queue, sandbox_runner=LocalProcessSandboxRunner())

    # Worker processes the original job to completion
    jobs = await real_queue.claim_execution(worker_id="worker-case-b", batch_size=1, block_ms=1000)
    job = next(j for j in jobs if j.execution_id == str(execution_id))
    ok = await worker.process_job(job)
    assert ok is True

    exec_record = await db_session.get(AgentExecution, execution_id)
    assert exec_record.status == ExecutionStatus.SUCCEEDED.value
    original_output = exec_record.output_data

    # Now duplicate message arrives (simulating duplicate publish)
    dup_job = QueueJob(
        execution_id=str(execution_id),
        agent_id=str(agent_id),
        agent_version_id=str(exec_record.agent_version_id),
        requested_by=str(exec_record.requested_by),
        input_hash=exec_record.input_hash,
        attempt_number=0,
        created_at=exec_record.queued_at.isoformat(),
        correlation_id="dup-test",
        queue_name="agent_executions",
        input_data=exec_record.input_data,
        message_id=job.message_id,
    )

    dup_count_before = metrics.counters.get("execution_duplicate_delivery_total", 0)
    # Worker receives duplicate job
    res = await worker.process_job(dup_job)
    assert res is True  # Safely handled without error
    dup_count_after = metrics.counters.get("execution_duplicate_delivery_total", 0)
    assert dup_count_after > dup_count_before

    # Verify execution output was not modified or corrupted
    await db_session.refresh(exec_record)
    assert exec_record.status == ExecutionStatus.SUCCEEDED.value
    assert exec_record.output_data == original_output


@pytest.mark.asyncio
async def test_case_c_worker_crashes_before_execution_job_reclaimed(
    async_client, db_session: AsyncSession
):
    """
    Case C: Worker crashes before execution.
    Expected: job is reclaimed and processed by another worker.
    """
    dev_token, _, _ = await create_authenticated_user(async_client, db_session)
    client_token, _, _ = await create_authenticated_user(async_client, db_session)

    manifest = RESEARCH_AGENT_MANIFEST.model_dump()
    slug = f"recov-c-{uuid.uuid4().hex[:8]}"
    create_res = await async_client.post(
        "/api/v1/agents",
        json={"name": "Recovery Agent C", "slug": slug, "manifest": manifest},
        headers={"Authorization": f"Bearer {dev_token}"},
    )
    agent_id = create_res.json()["id"]
    await async_client.post(f"/api/v1/agents/{agent_id}/publish", headers={"Authorization": f"Bearer {dev_token}"})

    exec_res = await async_client.post(
        f"/api/v1/agents/{agent_id}/execute",
        json={"input": {"text": "Recovery Case C text."}},
        headers={"Authorization": f"Bearer {client_token}"},
    )
    execution_id = uuid.UUID(exec_res.json()["execution_id"])

    real_queue = get_execution_queue()

    # Worker 1 claims the message, but "crashes" immediately without acking or processing
    jobs = await real_queue.claim_execution(worker_id="crashed-worker-1", batch_size=1, block_ms=1000)
    assert len(jobs) >= 1
    target_job = next(j for j in jobs if j.execution_id == str(execution_id))

    # Worker 2 reclaims stale executions with 0 second threshold (simulating crash detection)
    reclaimed = await real_queue.reclaim_stale_executions(
        worker_id="worker-2-recovered",
        stale_threshold_seconds=0,
        batch_size=5,
    )
    assert any(j.execution_id == str(execution_id) for j in reclaimed)

    # Worker 2 executes the reclaimed job
    worker2 = AgentExecutionWorker(queue=real_queue, sandbox_runner=LocalProcessSandboxRunner())
    reclaimed_job = next(j for j in reclaimed if j.execution_id == str(execution_id))
    ok = await worker2.process_job(reclaimed_job)
    assert ok is True

    exec_record = await db_session.get(AgentExecution, execution_id)
    assert exec_record.status == ExecutionStatus.SUCCEEDED.value


@pytest.mark.asyncio
async def test_case_d_worker_crashes_after_execution_before_xack(
    async_client, db_session: AsyncSession
):
    """
    Case D: Worker crashes after execution but before XACK.
    Expected: job is redelivered, but terminal execution is NOT overwritten.
    """
    dev_token, _, _ = await create_authenticated_user(async_client, db_session)
    client_token, _, _ = await create_authenticated_user(async_client, db_session)

    manifest = RESEARCH_AGENT_MANIFEST.model_dump()
    slug = f"recov-d-{uuid.uuid4().hex[:8]}"
    create_res = await async_client.post(
        "/api/v1/agents",
        json={"name": "Recovery Agent D", "slug": slug, "manifest": manifest},
        headers={"Authorization": f"Bearer {dev_token}"},
    )
    agent_id = create_res.json()["id"]
    await async_client.post(f"/api/v1/agents/{agent_id}/publish", headers={"Authorization": f"Bearer {dev_token}"})

    exec_res = await async_client.post(
        f"/api/v1/agents/{agent_id}/execute",
        json={"input": {"text": "Recovery Case D text."}},
        headers={"Authorization": f"Bearer {client_token}"},
    )
    execution_id = uuid.UUID(exec_res.json()["execution_id"])

    real_queue = get_execution_queue()
    worker = AgentExecutionWorker(queue=real_queue, sandbox_runner=LocalProcessSandboxRunner())

    # Worker executes to completion
    jobs = await real_queue.claim_execution(worker_id="worker-d1", batch_size=1, block_ms=1000)
    target_job = next(j for j in jobs if j.execution_id == str(execution_id))
    await worker.process_job(target_job)

    exec_record = await db_session.get(AgentExecution, execution_id)
    assert exec_record.status == ExecutionStatus.SUCCEEDED.value
    expected_output = exec_record.output_data

    # Simulate message redelivery: Worker 2 receives the un-acked job
    worker2 = AgentExecutionWorker(queue=real_queue, sandbox_runner=LocalProcessSandboxRunner())
    ok = await worker2.process_job(target_job)
    assert ok is True  # Duplicate gracefully acknowledged

    # Verify output data was NOT overwritten
    await db_session.refresh(exec_record)
    assert exec_record.status == ExecutionStatus.SUCCEEDED.value
    assert exec_record.output_data == expected_output


@pytest.mark.asyncio
async def test_case_e_api_restarts_state_durable(
    async_client, db_session: AsyncSession
):
    """
    Case E: API restarts.
    Expected: Outbox and execution records remain durable in PostgreSQL.
    """
    dev_token, _, _ = await create_authenticated_user(async_client, db_session)
    client_token, _, _ = await create_authenticated_user(async_client, db_session)

    manifest = RESEARCH_AGENT_MANIFEST.model_dump()
    slug = f"recov-e-{uuid.uuid4().hex[:8]}"
    create_res = await async_client.post(
        "/api/v1/agents",
        json={"name": "Recovery Agent E", "slug": slug, "manifest": manifest},
        headers={"Authorization": f"Bearer {dev_token}"},
    )
    agent_id = create_res.json()["id"]
    await async_client.post(f"/api/v1/agents/{agent_id}/publish", headers={"Authorization": f"Bearer {dev_token}"})

    exec_res = await async_client.post(
        f"/api/v1/agents/{agent_id}/execute",
        json={"input": {"text": "Recovery Case E text."}},
        headers={"Authorization": f"Bearer {client_token}"},
    )
    execution_id = uuid.UUID(exec_res.json()["execution_id"])

    # Simulate "API restart": close current session, query afresh from DB engine
    await db_session.commit()

    from app.db.session import async_session_factory
    async with async_session_factory() as new_session:
        restored_exec = await new_session.get(AgentExecution, execution_id)
        assert restored_exec is not None
        assert restored_exec.status in {ExecutionStatus.QUEUED.value, ExecutionStatus.SUCCEEDED.value}

        outbox_res = await new_session.execute(
            select(ExecutionOutbox).where(ExecutionOutbox.execution_id == execution_id)
        )
        restored_outbox = outbox_res.scalar_one_or_none()
        assert restored_outbox is not None
        assert restored_outbox.execution_id == execution_id


@pytest.mark.asyncio
async def test_case_f_worker_restarts_queued_jobs_continue(
    async_client, db_session: AsyncSession
):
    """
    Case F: Worker restarts.
    Expected: queued jobs continue processing after worker reboot.
    """
    dev_token, _, _ = await create_authenticated_user(async_client, db_session)
    client_token, _, _ = await create_authenticated_user(async_client, db_session)

    manifest = RESEARCH_AGENT_MANIFEST.model_dump()
    slug = f"recov-f-{uuid.uuid4().hex[:8]}"
    create_res = await async_client.post(
        "/api/v1/agents",
        json={"name": "Recovery Agent F", "slug": slug, "manifest": manifest},
        headers={"Authorization": f"Bearer {dev_token}"},
    )
    agent_id = create_res.json()["id"]
    await async_client.post(f"/api/v1/agents/{agent_id}/publish", headers={"Authorization": f"Bearer {dev_token}"})

    exec_res = await async_client.post(
        f"/api/v1/agents/{agent_id}/execute",
        json={"input": {"text": "Recovery Case F text."}},
        headers={"Authorization": f"Bearer {client_token}"},
    )
    execution_id = uuid.UUID(exec_res.json()["execution_id"])

    real_queue = get_execution_queue()

    # Worker 1 boots and immediately shuts down without claiming
    worker1 = AgentExecutionWorker(queue=real_queue, sandbox_runner=LocalProcessSandboxRunner())
    await worker1.start()
    await worker1.stop()

    # Worker 2 boots up, claims the queued job, and executes to completion
    worker2 = AgentExecutionWorker(queue=real_queue, sandbox_runner=LocalProcessSandboxRunner())
    jobs = await real_queue.claim_execution(worker_id="worker-2-after-restart", batch_size=1, block_ms=1000)
    target_job = next(j for j in jobs if j.execution_id == str(execution_id))
    ok = await worker2.process_job(target_job)
    assert ok is True

    exec_record = await db_session.get(AgentExecution, execution_id)
    assert exec_record.status == ExecutionStatus.SUCCEEDED.value
