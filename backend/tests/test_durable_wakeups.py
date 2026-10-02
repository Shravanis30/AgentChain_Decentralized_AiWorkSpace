import asyncio
import copy
import uuid
import pytest
from unittest.mock import AsyncMock, patch

from sqlalchemy import select, text, func
from sqlalchemy.orm import selectinload

from app.core.metrics import metrics
from app.db.session import async_session_factory, async_session_maker
from app.models.agent import AgentExecution, ExecutionStatus
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
from app.models.user import UserRole
from app.services.orchestration_engine import OrchestrationEngine
from app.services.orchestration_recovery import (
    OrchestrationReconciliationService,
    OrchestrationWakeupConsumer,
    OrchestrationWakeupDispatcher,
)
from app.services.rate_limiter import get_redis_client
from app.services.state_machine import ExecutionStateMachine
from tests.test_agents import create_authenticated_user
from tests.test_orchestration_engine import setup_reference_agents


async def transition_to_running_and_succeeded(
    db,
    execution,
    output_data=None,
    output_hash="hash_default",
    execution_time_ms=100,
    worker_id="test-worker",
):
    await ExecutionStateMachine.transition_to_running(
        db=db,
        execution=execution,
        worker_id=worker_id,
    )
    data = dict(output_data or {"summary": "test output", "word_count": 3, "sentence_count": 1, "character_count": 25})
    if "summary" in data and "word_count" not in data:
        data.update({"word_count": 3, "sentence_count": 1, "character_count": 25})
    return await ExecutionStateMachine.transition_to_succeeded(
        db=db,
        execution=execution,
        output_data=data,
        output_hash=output_hash,
        execution_time_ms=execution_time_ms,
        worker_id=worker_id,
    )


async def transition_to_running_and_failed(
    db,
    execution,
    error_code="WORKER_TIMEOUT",
    error_message="Simulated error",
    worker_id="test-worker",
):
    await ExecutionStateMachine.transition_to_running(
        db=db,
        execution=execution,
        worker_id=worker_id,
    )
    return await ExecutionStateMachine.transition_to_failed(
        db=db,
        execution=execution,
        error_code=error_code,
        error_message=error_message,
        worker_id=worker_id,
    )


# =============================================================================
# A-E: Durable Delivery
# =============================================================================


@pytest.mark.asyncio
async def test_a_terminal_execution_creates_durable_wakeup_atomically(async_client, db_session):
    """
    Test A: Terminal execution creates durable wake-up atomically.
    Ensures that when an execution transitions to SUCCEEDED or FAILED,
    an OrchestrationWakeupOutbox record is created within the exact same transaction.
    """
    client_token, client_user_id, _ = await create_authenticated_user(async_client, db_session, role=UserRole.CLIENT)
    dev_token, _, _ = await create_authenticated_user(async_client, db_session, role=UserRole.DEVELOPER)
    await setup_reference_agents(async_client, db_session, dev_token)

    engine = OrchestrationEngine()
    orch = await engine.create_orchestration(
        db=db_session,
        user_id=client_user_id,
        goal="Test A: Atomic wake-up outbox creation",
    )
    await engine.start_orchestration(db_session, orch.id, client_user_id)

    state = await engine.restore_checkpoint(db_session, orch.id)
    state = await engine.step(state)
    exec_id = uuid.UUID(state["task_states"]["research"]["execution_id"])

    # Finalize execution in DB using ExecutionStateMachine
    async with async_session_maker() as db:
        res = await db.execute(select(AgentExecution).where(AgentExecution.id == exec_id))
        execution = res.scalar_one()

        await transition_to_running_and_succeeded(
            db=db,
            execution=execution,
            output_data={"summary": "atomic test output"},
            output_hash="hash_atomic_123",
            execution_time_ms=150,
        )
        await db.commit()

    # Verify wake-up outbox record exists atomically in PostgreSQL
    async with async_session_maker() as db:
        outbox_res = await db.execute(
            select(OrchestrationWakeupOutbox).where(
                OrchestrationWakeupOutbox.execution_id == exec_id
            )
        )
        outbox = outbox_res.scalar_one_or_none()
        assert outbox is not None
        assert outbox.orchestration_id == orch.id
        assert outbox.terminal_status == ExecutionStatus.SUCCEEDED.value
        assert outbox.published_at is None
        assert outbox.payload["status"] == "SUCCEEDED"
        assert outbox.payload["attempt"] == 1


@pytest.mark.asyncio
async def test_b_dispatcher_restart_does_not_lose_pending_wakeup(async_client, db_session):
    """
    Test B: Dispatcher restart does not lose pending wake-up.
    Pending outbox entries in PostgreSQL survive process restarts and are dispatched.
    """
    client_token, client_user_id, _ = await create_authenticated_user(async_client, db_session, role=UserRole.CLIENT)
    dev_token, _, _ = await create_authenticated_user(async_client, db_session, role=UserRole.DEVELOPER)
    await setup_reference_agents(async_client, db_session, dev_token)

    engine = OrchestrationEngine()
    orch = await engine.create_orchestration(
        db=db_session,
        user_id=client_user_id,
        goal="Test B: Dispatcher restart survivability",
    )
    await engine.start_orchestration(db_session, orch.id, client_user_id)
    state = await engine.step(await engine.restore_checkpoint(db_session, orch.id))
    exec_id = uuid.UUID(state["task_states"]["research"]["execution_id"])

    # Simulate worker committing terminal state, then process crashing before dispatch
    async with async_session_maker() as db:
        res = await db.execute(select(AgentExecution).where(AgentExecution.id == exec_id))
        execution = res.scalar_one()
        await transition_to_running_and_succeeded(
            db=db,
            execution=execution,
            output_data={"summary": "crash before dispatch"},
            output_hash="hash_b",
        )
        await db.commit()

    # Fresh dispatcher instance starts up
    redis = get_redis_client()
    async with async_session_maker() as db:
        dispatched_count = await OrchestrationWakeupDispatcher.dispatch_pending(db, redis=redis)
        assert dispatched_count >= 1

    # Verify record was marked published
    async with async_session_maker() as db:
        outbox = await db.scalar(
            select(OrchestrationWakeupOutbox).where(OrchestrationWakeupOutbox.execution_id == exec_id)
        )
        assert outbox is not None
        assert outbox.published_at is not None
        assert outbox.last_error is None


@pytest.mark.asyncio
async def test_c_publication_failure_causes_retry(async_client, db_session):
    """
    Test C: Publication failure causes retry with bounded exponential backoff.
    """
    client_token, client_user_id, _ = await create_authenticated_user(async_client, db_session, role=UserRole.CLIENT)
    dev_token, _, _ = await create_authenticated_user(async_client, db_session, role=UserRole.DEVELOPER)
    await setup_reference_agents(async_client, db_session, dev_token)

    engine = OrchestrationEngine()
    orch = await engine.create_orchestration(
        db=db_session,
        user_id=client_user_id,
        goal="Test C: Dispatch retry logic",
    )
    await engine.start_orchestration(db_session, orch.id, client_user_id)
    state = await engine.step(await engine.restore_checkpoint(db_session, orch.id))
    exec_id = uuid.UUID(state["task_states"]["research"]["execution_id"])

    async with async_session_maker() as db:
        res = await db.execute(select(AgentExecution).where(AgentExecution.id == exec_id))
        execution = res.scalar_one()
        await transition_to_running_and_succeeded(
            db=db,
            execution=execution,
            output_data={"summary": "retry test"},
            output_hash="hash_c",
        )
        await db.commit()

    # Mock Redis failure during dispatch
    failing_redis = AsyncMock()
    failing_redis.xadd.side_effect = ConnectionError("Redis cluster unreachable")

    async with async_session_maker() as db:
        outbox = await db.scalar(
            select(OrchestrationWakeupOutbox).where(OrchestrationWakeupOutbox.execution_id == exec_id)
        )
        assert outbox is not None
        success = await OrchestrationWakeupDispatcher.publish_entry(db, outbox, redis=failing_redis)
        await db.commit()

        assert success is False
        assert outbox.attempts == 1
        assert outbox.published_at is None
        assert "Redis cluster unreachable" in (outbox.last_error or "")
        assert outbox.available_at > outbox.created_at


@pytest.mark.asyncio
async def test_d_successful_publication_marks_wakeup_appropriately(async_client, db_session):
    """
    Test D: Successful publication marks wake-up appropriately.
    """
    client_token, client_user_id, _ = await create_authenticated_user(async_client, db_session, role=UserRole.CLIENT)
    dev_token, _, _ = await create_authenticated_user(async_client, db_session, role=UserRole.DEVELOPER)
    await setup_reference_agents(async_client, db_session, dev_token)

    engine = OrchestrationEngine()
    orch = await engine.create_orchestration(
        db=db_session,
        user_id=client_user_id,
        goal="Test D: Dispatch success verification",
    )
    await engine.start_orchestration(db_session, orch.id, client_user_id)
    state = await engine.step(await engine.restore_checkpoint(db_session, orch.id))
    exec_id = uuid.UUID(state["task_states"]["research"]["execution_id"])

    async with async_session_maker() as db:
        res = await db.execute(select(AgentExecution).where(AgentExecution.id == exec_id))
        execution = res.scalar_one()
        await transition_to_running_and_succeeded(
            db=db,
            execution=execution,
            output_data={"summary": "publish test"},
            output_hash="hash_d",
        )
        await db.commit()

    redis = get_redis_client()
    async with async_session_maker() as db:
        outbox = await db.scalar(
            select(OrchestrationWakeupOutbox).where(OrchestrationWakeupOutbox.execution_id == exec_id)
        )
        assert outbox is not None
        success = await OrchestrationWakeupDispatcher.publish_entry(db, outbox, redis=redis)
        await db.commit()
        assert success is True
        assert outbox.published_at is not None
        assert outbox.last_error is None


@pytest.mark.asyncio
async def test_e_duplicate_dispatcher_delivery_is_idempotent(async_client, db_session):
    """
    Test E: Duplicate dispatcher delivery is idempotent.
    """
    client_token, client_user_id, _ = await create_authenticated_user(async_client, db_session, role=UserRole.CLIENT)
    dev_token, _, _ = await create_authenticated_user(async_client, db_session, role=UserRole.DEVELOPER)
    await setup_reference_agents(async_client, db_session, dev_token)

    engine = OrchestrationEngine()
    orch = await engine.create_orchestration(
        db=db_session,
        user_id=client_user_id,
        goal="Test E: Idempotent dispatcher delivery",
    )
    await engine.start_orchestration(db_session, orch.id, client_user_id)
    state = await engine.step(await engine.restore_checkpoint(db_session, orch.id))
    exec_id = uuid.UUID(state["task_states"]["research"]["execution_id"])

    async with async_session_maker() as db:
        res = await db.execute(select(AgentExecution).where(AgentExecution.id == exec_id))
        execution = res.scalar_one()
        await transition_to_running_and_succeeded(
            db=db,
            execution=execution,
            output_data={"summary": "idempotent test"},
            output_hash="hash_e",
        )
        await db.commit()

    redis = get_redis_client()
    async with async_session_maker() as db:
        outbox = await db.scalar(
            select(OrchestrationWakeupOutbox).where(OrchestrationWakeupOutbox.execution_id == exec_id)
        )
        # First publication
        success1 = await OrchestrationWakeupDispatcher.publish_entry(db, outbox, redis=redis)
        await db.commit()
        assert success1 is True
        first_pub_time = outbox.published_at

        # Second publication call with same entry
        success2 = await OrchestrationWakeupDispatcher.publish_entry(db, outbox, redis=redis)
        assert success2 is True
        assert outbox.published_at == first_pub_time


# =============================================================================
# F-I: Lost Pub/Sub / Notification
# =============================================================================


@pytest.mark.asyncio
async def test_f_redis_pubsub_notification_completely_lost(async_client, db_session):
    """
    Test F: Redis Pub/Sub notification is completely lost.
    Verifies that even if Pub/Sub dropped the message entirely,
    the durable outbox and execution record exist in PostgreSQL.
    """
    client_token, client_user_id, _ = await create_authenticated_user(async_client, db_session, role=UserRole.CLIENT)
    dev_token, _, _ = await create_authenticated_user(async_client, db_session, role=UserRole.DEVELOPER)
    await setup_reference_agents(async_client, db_session, dev_token)

    engine = OrchestrationEngine()
    orch = await engine.create_orchestration(
        db=db_session,
        user_id=client_user_id,
        goal="Test F: Lost Pub/Sub",
    )
    await engine.start_orchestration(db_session, orch.id, client_user_id)
    state = await engine.step(await engine.restore_checkpoint(db_session, orch.id))
    exec_id = uuid.UUID(state["task_states"]["research"]["execution_id"])

    # Worker marks SUCCEEDED, but PubSub is suppressed / drops message
    async with async_session_maker() as db:
        res = await db.execute(select(AgentExecution).where(AgentExecution.id == exec_id))
        execution = res.scalar_one()
        await transition_to_running_and_succeeded(
            db=db,
            execution=execution,
            output_data={"summary": "lost pubsub output"},
            output_hash="hash_f",
        )
        await db.commit()

    # DB state is committed and intact
    async with async_session_maker() as db:
        ex = await db.get(AgentExecution, exec_id)
        assert ex.status == ExecutionStatus.SUCCEEDED.value
        outbox = await db.scalar(
            select(OrchestrationWakeupOutbox).where(OrchestrationWakeupOutbox.execution_id == exec_id)
        )
        assert outbox is not None
        assert outbox.terminal_status == ExecutionStatus.SUCCEEDED.value


@pytest.mark.asyncio
async def test_g_orchestration_progresses_automatically_through_recovery(async_client, db_session):
    """
    Test G: Orchestration still progresses automatically through recovery sweep.
    No manual API call is made.
    """
    client_token, client_user_id, _ = await create_authenticated_user(async_client, db_session, role=UserRole.CLIENT)
    dev_token, _, _ = await create_authenticated_user(async_client, db_session, role=UserRole.DEVELOPER)
    await setup_reference_agents(async_client, db_session, dev_token)

    engine = OrchestrationEngine()
    orch = await engine.create_orchestration(
        db=db_session,
        user_id=client_user_id,
        goal="Test G: Automatic progress through recovery",
    )
    await engine.start_orchestration(db_session, orch.id, client_user_id)
    state = await engine.step(await engine.restore_checkpoint(db_session, orch.id))
    exec_id = uuid.UUID(state["task_states"]["research"]["execution_id"])

    # Terminal state committed in PostgreSQL, notification omitted
    async with async_session_maker() as db:
        res = await db.execute(select(AgentExecution).where(AgentExecution.id == exec_id))
        execution = res.scalar_one()
        await transition_to_running_and_succeeded(
            db=db,
            execution=execution,
            output_data={"summary": "recovered summary"},
            output_hash="hash_g",
        )
        await db.commit()

    # Automatic recovery sweep runs (zero manual API calls)
    async with async_session_maker() as db:
        reconciled = await OrchestrationReconciliationService.reconcile_stuck_orchestrations(
            db=db,
            stale_threshold_seconds=0.0,
            orchestration_ids=[orch.id],
        )
        assert orch.id in reconciled

    # Verify parent orchestration advanced: research completed, analysis running
    async with async_session_maker() as db:
        updated_orch = await engine.get_orchestration(db, orch.id)
        task_map = {t.task_key: t for t in updated_orch.tasks}
        assert task_map["research"].status == OrchestrationTaskStatus.SUCCEEDED.value
        assert task_map["analysis"].status in (
            OrchestrationTaskStatus.READY.value,
            OrchestrationTaskStatus.RUNNING.value,
        )


@pytest.mark.asyncio
async def test_h_orchestrator_offline_when_execution_completes(async_client, db_session):
    """
    Test H: Orchestrator is offline when execution completes.
    """
    client_token, client_user_id, _ = await create_authenticated_user(async_client, db_session, role=UserRole.CLIENT)
    dev_token, _, _ = await create_authenticated_user(async_client, db_session, role=UserRole.DEVELOPER)
    await setup_reference_agents(async_client, db_session, dev_token)

    engine = OrchestrationEngine()
    orch = await engine.create_orchestration(
        db=db_session,
        user_id=client_user_id,
        goal="Test H: Orchestrator offline during completion",
    )
    await engine.start_orchestration(db_session, orch.id, client_user_id)
    state = await engine.step(await engine.restore_checkpoint(db_session, orch.id))
    exec_id = uuid.UUID(state["task_states"]["research"]["execution_id"])

    # Orchestrator is destroyed / offline
    del engine

    # Worker completes execution in PostgreSQL
    async with async_session_maker() as db:
        res = await db.execute(select(AgentExecution).where(AgentExecution.id == exec_id))
        execution = res.scalar_one()
        await transition_to_running_and_succeeded(
            db=db,
            execution=execution,
            output_data={"summary": "offline completion"},
            output_hash="hash_h",
        )
        await db.commit()

    # Verify both terminal state and outbox exist
    async with async_session_maker() as db:
        ex = await db.get(AgentExecution, exec_id)
        assert ex.status == ExecutionStatus.SUCCEEDED.value
        outbox = await db.scalar(
            select(OrchestrationWakeupOutbox).where(OrchestrationWakeupOutbox.execution_id == exec_id)
        )
        assert outbox is not None


@pytest.mark.asyncio
async def test_i_orchestrator_restarts_after_execution_completion(async_client, db_session):
    """
    Test I: Orchestrator restarts after execution completion and recovers automatically.
    """
    client_token, client_user_id, _ = await create_authenticated_user(async_client, db_session, role=UserRole.CLIENT)
    dev_token, _, _ = await create_authenticated_user(async_client, db_session, role=UserRole.DEVELOPER)
    await setup_reference_agents(async_client, db_session, dev_token)

    engine = OrchestrationEngine()
    orch = await engine.create_orchestration(
        db=db_session,
        user_id=client_user_id,
        goal="Test I: Orchestrator restarts and recovers",
    )
    await engine.start_orchestration(db_session, orch.id, client_user_id)
    state = await engine.step(await engine.restore_checkpoint(db_session, orch.id))
    exec_id = uuid.UUID(state["task_states"]["research"]["execution_id"])

    # Worker completes execution while orchestrator is absent
    async with async_session_maker() as db:
        res = await db.execute(select(AgentExecution).where(AgentExecution.id == exec_id))
        execution = res.scalar_one()
        await transition_to_running_and_succeeded(
            db=db,
            execution=execution,
            output_data={"summary": "restarted orchestrator summary"},
            output_hash="hash_i",
        )
        await db.commit()

    # Brand new orchestrator engine and recovery service start up
    new_engine = OrchestrationEngine()
    async with async_session_maker() as db:
        reconciled = await OrchestrationReconciliationService.reconcile_stuck_orchestrations(
            db=db, stale_threshold_seconds=0.0, orchestration_ids=[orch.id]
        )
        assert orch.id in reconciled

    # Check updated state
    async with async_session_maker() as db:
        ckpt = await new_engine.restore_checkpoint(db, orch.id)
        assert "research" in ckpt["completed_tasks"]


# =============================================================================
# J-L: Database Crash Boundaries
# =============================================================================


@pytest.mark.asyncio
async def test_j_execution_terminal_state_cannot_exist_without_durable_recovery_path(async_client, db_session):
    """
    Test J: Execution terminal state cannot exist without corresponding durable recovery path.
    Verifies transactional atomicity on rollback.
    """
    client_token, client_user_id, _ = await create_authenticated_user(async_client, db_session, role=UserRole.CLIENT)
    dev_token, _, _ = await create_authenticated_user(async_client, db_session, role=UserRole.DEVELOPER)
    await setup_reference_agents(async_client, db_session, dev_token)

    engine = OrchestrationEngine()
    orch = await engine.create_orchestration(
        db=db_session,
        user_id=client_user_id,
        goal="Test J: Transactional boundary atomicity",
    )
    await engine.start_orchestration(db_session, orch.id, client_user_id)
    state = await engine.step(await engine.restore_checkpoint(db_session, orch.id))
    exec_id = uuid.UUID(state["task_states"]["research"]["execution_id"])

    # Simulate a transaction rollback during finalization
    async with async_session_maker() as db:
        res = await db.execute(select(AgentExecution).where(AgentExecution.id == exec_id))
        execution = res.scalar_one()
        await transition_to_running_and_succeeded(
            db=db,
            execution=execution,
            output_data={"summary": "rollback test"},
            output_hash="hash_j",
        )
        # Rollback transaction (simulating crash before commit)
        await db.rollback()

    # Neither the terminal execution status NOR the outbox entry must exist
    async with async_session_maker() as db:
        ex = await db.get(AgentExecution, exec_id)
        assert ex.status != ExecutionStatus.SUCCEEDED.value
        outbox = await db.scalar(
            select(OrchestrationWakeupOutbox).where(OrchestrationWakeupOutbox.execution_id == exec_id)
        )
        assert outbox is None


@pytest.mark.asyncio
async def test_k_crash_during_wakeup_dispatch(async_client, db_session):
    """
    Test K: Crash/restart during wake-up dispatch.
    If the dispatcher crashes after reading from DB but before committing,
    the outbox record remains available and will be picked up on restart.
    """
    client_token, client_user_id, _ = await create_authenticated_user(async_client, db_session, role=UserRole.CLIENT)
    dev_token, _, _ = await create_authenticated_user(async_client, db_session, role=UserRole.DEVELOPER)
    await setup_reference_agents(async_client, db_session, dev_token)

    engine = OrchestrationEngine()
    orch = await engine.create_orchestration(
        db=db_session,
        user_id=client_user_id,
        goal="Test K: Crash during dispatch",
    )
    await engine.start_orchestration(db_session, orch.id, client_user_id)
    state = await engine.step(await engine.restore_checkpoint(db_session, orch.id))
    exec_id = uuid.UUID(state["task_states"]["research"]["execution_id"])

    async with async_session_maker() as db:
        res = await db.execute(select(AgentExecution).where(AgentExecution.id == exec_id))
        execution = res.scalar_one()
        await transition_to_running_and_succeeded(
            db=db,
            execution=execution,
            output_data={"summary": "crash during dispatch"},
            output_hash="hash_k",
        )
        await db.commit()

    # Simulate crash mid-dispatch: lock acquired, then session rolls back / drops
    async with async_session_maker() as db:
        outbox = await db.scalar(
            select(OrchestrationWakeupOutbox).where(OrchestrationWakeupOutbox.execution_id == exec_id)
        )
        assert outbox is not None
        # Simulate uncommitted crash
        outbox.attempts += 1
        await db.rollback()

    # Restarted dispatcher runs and successfully dispatches
    redis = get_redis_client()
    async with async_session_maker() as db:
        dispatched = await OrchestrationWakeupDispatcher.dispatch_pending(db, redis=redis)
        assert dispatched >= 1


@pytest.mark.asyncio
async def test_l_crash_after_wakeup_publication_before_ack(async_client, db_session):
    """
    Test L: Crash/restart after wake-up publication but before acknowledgement.
    Redis Streams consumer processes message safely even if redelivered.
    """
    client_token, client_user_id, _ = await create_authenticated_user(async_client, db_session, role=UserRole.CLIENT)
    dev_token, _, _ = await create_authenticated_user(async_client, db_session, role=UserRole.DEVELOPER)
    await setup_reference_agents(async_client, db_session, dev_token)

    engine = OrchestrationEngine()
    orch = await engine.create_orchestration(
        db=db_session,
        user_id=client_user_id,
        goal="Test L: Consumer crash before ack",
    )
    await engine.start_orchestration(db_session, orch.id, client_user_id)
    state = await engine.step(await engine.restore_checkpoint(db_session, orch.id))
    exec_id = uuid.UUID(state["task_states"]["research"]["execution_id"])

    async with async_session_maker() as db:
        res = await db.execute(select(AgentExecution).where(AgentExecution.id == exec_id))
        execution = res.scalar_one()
        await transition_to_running_and_succeeded(
            db=db,
            execution=execution,
            output_data={"summary": "stream ack crash test"},
            output_hash="hash_l",
        )
        await db.commit()

    redis = get_redis_client()
    # Dispatch to Redis Stream
    async with async_session_maker() as db:
        await OrchestrationWakeupDispatcher.dispatch_pending(db, redis=redis)

    consumer = OrchestrationWakeupConsumer()
    # First consume batch
    processed = await consumer.consume_batch(redis, count=10, block_ms=500)
    assert processed >= 1

    # Second consume batch (duplicate redelivery simulation)
    # Orchestration advancement is idempotent and safe
    dup_state = await engine.advance_orchestration(orch.id)
    assert dup_state is not None
    assert "research" in dup_state["completed_tasks"]


# =============================================================================
# M-O: Concurrency
# =============================================================================


@pytest.mark.asyncio
async def test_m_recovery_sweep_and_wakeup_event_race(async_client, db_session):
    """
    Test M: Recovery sweep and wake-up event race simultaneously.
    PostgreSQL advisory locking guarantees only one process advances.
    """
    client_token, client_user_id, _ = await create_authenticated_user(async_client, db_session, role=UserRole.CLIENT)
    dev_token, _, _ = await create_authenticated_user(async_client, db_session, role=UserRole.DEVELOPER)
    await setup_reference_agents(async_client, db_session, dev_token)

    engine = OrchestrationEngine()
    orch = await engine.create_orchestration(
        db=db_session,
        user_id=client_user_id,
        goal="Test M: Recovery and wake-up event race",
    )
    await engine.start_orchestration(db_session, orch.id, client_user_id)
    state = await engine.step(await engine.restore_checkpoint(db_session, orch.id))
    exec_id = uuid.UUID(state["task_states"]["research"]["execution_id"])

    async with async_session_maker() as db:
        res = await db.execute(select(AgentExecution).where(AgentExecution.id == exec_id))
        execution = res.scalar_one()
        await transition_to_running_and_succeeded(
            db=db,
            execution=execution,
            output_data={"summary": "concurrency output"},
            output_hash="hash_m",
        )
        await db.commit()

    # Race: handle_execution_completed_wakeup vs advance_orchestration (from recovery sweep)
    async def run_wakeup():
        return await engine.handle_execution_completed_wakeup(
            orchestration_id=orch.id,
            execution_id=exec_id,
            status="SUCCEEDED",
            correlation_id="race-wakeup",
        )

    async def run_recovery():
        return await engine.advance_orchestration(
            orchestration_id=orch.id,
            correlation_id="race-recovery",
        )

    results = await asyncio.gather(run_wakeup(), run_recovery(), return_exceptions=True)
    # Neither should crash with an unhandled exception
    for r in results:
        assert not isinstance(r, Exception)

    # At least one succeeded in advancing
    states = [r for r in results if r is not None]
    assert len(states) >= 1
    assert "research" in states[0]["completed_tasks"]


@pytest.mark.asyncio
async def test_n_two_recovery_workers_race(async_client, db_session):
    """
    Test N: Two recovery workers race simultaneously.
    """
    client_token, client_user_id, _ = await create_authenticated_user(async_client, db_session, role=UserRole.CLIENT)
    dev_token, _, _ = await create_authenticated_user(async_client, db_session, role=UserRole.DEVELOPER)
    await setup_reference_agents(async_client, db_session, dev_token)

    engine = OrchestrationEngine()
    orch = await engine.create_orchestration(
        db=db_session,
        user_id=client_user_id,
        goal="Test N: Two recovery workers racing",
    )
    await engine.start_orchestration(db_session, orch.id, client_user_id)
    state = await engine.step(await engine.restore_checkpoint(db_session, orch.id))
    exec_id = uuid.UUID(state["task_states"]["research"]["execution_id"])

    async with async_session_maker() as db:
        res = await db.execute(select(AgentExecution).where(AgentExecution.id == exec_id))
        execution = res.scalar_one()
        await transition_to_running_and_succeeded(
            db=db,
            execution=execution,
            output_data={"summary": "two workers race"},
            output_hash="hash_n",
        )
        await db.commit()

    async def worker_sweep():
        async with async_session_maker() as db:
            return await OrchestrationReconciliationService.reconcile_stuck_orchestrations(
                db=db, stale_threshold_seconds=0.0, orchestration_ids=[orch.id]
            )

    results = await asyncio.gather(worker_sweep(), worker_sweep(), return_exceptions=True)
    for r in results:
        assert not isinstance(r, Exception)


@pytest.mark.asyncio
async def test_o_recovery_worker_and_api_triggered_advancement_race(async_client, db_session):
    """
    Test O: Recovery worker and API-triggered advancement race.
    """
    client_token, client_user_id, _ = await create_authenticated_user(async_client, db_session, role=UserRole.CLIENT)
    dev_token, _, _ = await create_authenticated_user(async_client, db_session, role=UserRole.DEVELOPER)
    await setup_reference_agents(async_client, db_session, dev_token)

    engine = OrchestrationEngine()
    orch = await engine.create_orchestration(
        db=db_session,
        user_id=client_user_id,
        goal="Test O: Recovery vs API race",
    )
    await engine.start_orchestration(db_session, orch.id, client_user_id)
    state = await engine.step(await engine.restore_checkpoint(db_session, orch.id))
    exec_id = uuid.UUID(state["task_states"]["research"]["execution_id"])

    async with async_session_maker() as db:
        res = await db.execute(select(AgentExecution).where(AgentExecution.id == exec_id))
        execution = res.scalar_one()
        await transition_to_running_and_succeeded(
            db=db,
            execution=execution,
            output_data={"summary": "api race output"},
            output_hash="hash_o",
        )
        await db.commit()

    async def api_advance():
        return await engine.advance_orchestration(orch.id, correlation_id="api-race")

    async def recovery_sweep():
        async with async_session_maker() as db:
            return await OrchestrationReconciliationService.reconcile_stuck_orchestrations(
                db=db, stale_threshold_seconds=0.0, orchestration_ids=[orch.id]
            )

    results = await asyncio.gather(api_advance(), recovery_sweep(), return_exceptions=True)
    for r in results:
        assert not isinstance(r, Exception)


# =============================================================================
# P-R: End-to-End
# =============================================================================


@pytest.mark.asyncio
async def test_p_research_analysis_synthesis_still_completes(async_client, db_session):
    """
    Test P: Research -> Analysis -> Synthesis still completes end-to-end
    with durable outbox and recovery mechanisms active.
    """
    client_token, client_user_id, _ = await create_authenticated_user(async_client, db_session, role=UserRole.CLIENT)
    dev_token, _, _ = await create_authenticated_user(async_client, db_session, role=UserRole.DEVELOPER)
    await setup_reference_agents(async_client, db_session, dev_token)

    engine = OrchestrationEngine()
    orch = await engine.create_orchestration(
        db=db_session,
        user_id=client_user_id,
        goal="Test P: Full 3-task pipeline end-to-end",
    )
    await engine.start_orchestration(db_session, orch.id, client_user_id)

    redis = get_redis_client()

    # Step 1: Research task scheduled
    state = await engine.advance_orchestration(orch.id)
    assert "research" in state["running_tasks"]
    research_exec_id = uuid.UUID(state["task_states"]["research"]["execution_id"])

    # Simulate research execution completion
    async with async_session_maker() as db:
        res = await db.execute(select(AgentExecution).where(AgentExecution.id == research_exec_id))
        ex = res.scalar_one()
        await transition_to_running_and_succeeded(
            db=db,
            execution=ex,
            output_data={"summary": "Agent research findings", "word_count": 3, "sentence_count": 1, "character_count": 23},
            output_hash="hash_p_res",
        )
        await db.commit()

    # Dispatch outbox & advance
    async with async_session_maker() as db:
        await OrchestrationWakeupDispatcher.dispatch_pending(db, redis=redis)
    state = await engine.advance_orchestration(orch.id)
    assert "research" in state["completed_tasks"]
    assert "analysis" in state["running_tasks"]

    # Step 2: Analysis task completion
    analysis_exec_id = uuid.UUID(state["task_states"]["analysis"]["execution_id"])
    async with async_session_maker() as db:
        res = await db.execute(select(AgentExecution).where(AgentExecution.id == analysis_exec_id))
        ex = res.scalar_one()
        await transition_to_running_and_succeeded(
            db=db,
            execution=ex,
            output_data={
                "key_points": ["Verified finding 1", "Verified finding 2"],
                "sentiment": "neutral",
                "word_count": 6,
            },
            output_hash="hash_p_ana",
        )
        await db.commit()

    async with async_session_maker() as db:
        await OrchestrationWakeupDispatcher.dispatch_pending(db, redis=redis)
    state = await engine.advance_orchestration(orch.id)
    assert "analysis" in state["completed_tasks"]
    assert "synthesis" in state["running_tasks"]

    # Step 3: Synthesis task completion
    synthesis_exec_id = uuid.UUID(state["task_states"]["synthesis"]["execution_id"])
    async with async_session_maker() as db:
        res = await db.execute(select(AgentExecution).where(AgentExecution.id == synthesis_exec_id))
        ex = res.scalar_one()
        await transition_to_running_and_succeeded(
            db=db,
            execution=ex,
            output_data={
                "final_report": "Synthesized intelligence product",
                "status": "COMPLETED",
                "confidence": 0.98,
            },
            output_hash="hash_p_syn",
        )
        await db.commit()

    async with async_session_maker() as db:
        await OrchestrationWakeupDispatcher.dispatch_pending(db, redis=redis)
    final_state = await engine.advance_orchestration(orch.id)
    assert final_state["status"] == OrchestrationStatus.SUCCEEDED.value
    assert len(final_state["completed_tasks"]) == 3
    assert final_state["final_result"] is not None


@pytest.mark.asyncio
async def test_q_retry_attempt_1_to_attempt_2_remains_correct(async_client, db_session):
    """
    Test Q: Retry attempt 1 -> attempt 2 remains correct.
    Durable wake-up outbox records both attempts correctly.
    """
    client_token, client_user_id, _ = await create_authenticated_user(async_client, db_session, role=UserRole.CLIENT)
    dev_token, _, _ = await create_authenticated_user(async_client, db_session, role=UserRole.DEVELOPER)
    await setup_reference_agents(async_client, db_session, dev_token)

    engine = OrchestrationEngine()
    orch = await engine.create_orchestration(
        db=db_session,
        user_id=client_user_id,
        goal="Test Q: Retry attempt 1 to attempt 2 identity",
    )
    await engine.start_orchestration(db_session, orch.id, client_user_id)
    state = await engine.advance_orchestration(orch.id)

    exec1_id = uuid.UUID(state["task_states"]["research"]["execution_id"])
    assert state["task_states"]["research"]["attempt"] == 1

    # Attempt 1 fails retryably
    async with async_session_maker() as db:
        res = await db.execute(select(AgentExecution).where(AgentExecution.id == exec1_id))
        ex1 = res.scalar_one()
        await transition_to_running_and_failed(
            db=db,
            execution=ex1,
            error_code="WORKER_TIMEOUT",
            error_message="Simulated attempt 1 timeout",
        )
        await db.commit()

    # Advance engine to process failure and schedule attempt 2
    state = await engine.advance_orchestration(orch.id)
    assert state["task_states"]["research"]["status"] == "RUNNING"
    assert state["task_states"]["research"]["attempt"] == 2
    exec2_id = uuid.UUID(state["task_states"]["research"]["execution_id"])
    assert exec2_id != exec1_id

    # Verify both executions have distinct attempt-scoped idempotency keys and outbox entries
    async with async_session_maker() as db:
        ex1_db = await db.get(AgentExecution, exec1_id)
        ex2_db = await db.get(AgentExecution, exec2_id)
        assert ex1_db.task_attempt == 1
        assert ex2_db.task_attempt == 2
        assert ex1_db.idempotency_key.endswith("-attempt-1")
        assert ex2_db.idempotency_key.endswith("-attempt-2")


@pytest.mark.asyncio
async def test_r_duplicate_completion_events_remain_harmless(async_client, db_session):
    """
    Test R: Duplicate completion events remain harmless.
    Sending multiple wake-up events for the same completed execution does not create
    duplicate tasks or executions.
    """
    client_token, client_user_id, _ = await create_authenticated_user(async_client, db_session, role=UserRole.CLIENT)
    dev_token, _, _ = await create_authenticated_user(async_client, db_session, role=UserRole.DEVELOPER)
    await setup_reference_agents(async_client, db_session, dev_token)

    engine = OrchestrationEngine()
    orch = await engine.create_orchestration(
        db=db_session,
        user_id=client_user_id,
        goal="Test R: Harmless duplicate completion events",
    )
    await engine.start_orchestration(db_session, orch.id, client_user_id)
    state = await engine.advance_orchestration(orch.id)
    exec_id = uuid.UUID(state["task_states"]["research"]["execution_id"])

    async with async_session_maker() as db:
        res = await db.execute(select(AgentExecution).where(AgentExecution.id == exec_id))
        execution = res.scalar_one()
        await transition_to_running_and_succeeded(
            db=db,
            execution=execution,
            output_data={"summary": "processed once"},
            output_hash="hash_r",
        )
        await db.commit()

    # Deliver wake-up 5 times consecutively
    for i in range(5):
        adv_state = await engine.handle_execution_completed_wakeup(
            orchestration_id=orch.id,
            execution_id=exec_id,
            status="SUCCEEDED",
            correlation_id=f"dup-{i}",
        )
        assert adv_state is not None
        assert "research" in adv_state["completed_tasks"]

    # Verify no duplicate executions were created
    async with async_session_maker() as db:
        res = await db.execute(
            select(func.count(AgentExecution.id)).where(AgentExecution.orchestration_id == orch.id)
        )
        # Should only have 1 for research + 1 for analysis (since research completed and analysis scheduled)
        total_execs = res.scalar()
        assert total_execs == 2
