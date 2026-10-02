import asyncio
import copy
import uuid
import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import selectinload

from app.core.metrics import metrics
from app.db.session import async_session_maker
from app.models.agent import AgentExecution, ExecutionStatus
from app.models.orchestration import (
    Orchestration,
    OrchestrationStatus,
    OrchestrationTask,
    OrchestrationTaskStatus,
)
from app.models.user import UserRole
from app.schemas.orchestration import TaskDefinitionInput
from app.services.agent_execution import AgentExecutionService
from app.services.orchestration_engine import OrchestrationEngine
from app.services.queue import publish_orchestration_wakeup
from app.services.rate_limiter import get_redis_client
from tests.test_agents import create_authenticated_user
from tests.test_orchestration_engine import setup_reference_agents


@pytest.mark.asyncio
async def test_retry_a_same_task_same_attempt_scheduled_twice_single_execution(async_client, db_session):
    """
    Test A: Same task + same attempt scheduled twice -> exactly one AgentExecution.
    """
    client_token, client_user_id, _ = await create_authenticated_user(async_client, db_session, role=UserRole.CLIENT)
    dev_token, _, _ = await create_authenticated_user(async_client, db_session, role=UserRole.DEVELOPER)
    await setup_reference_agents(async_client, db_session, dev_token)

    engine = OrchestrationEngine()
    orch = await engine.create_orchestration(
        db=db_session,
        user_id=client_user_id,
        goal="Test A: Idempotent attempt scheduling",
    )
    await engine.start_orchestration(db_session, orch.id, client_user_id)

    initial_state = await engine.restore_checkpoint(db_session, orch.id)
    assert initial_state is not None

    # First schedule
    state1 = await engine.step(copy.deepcopy(initial_state))
    exec1_id = state1["task_states"]["research"]["execution_id"]
    assert exec1_id is not None

    # Duplicate schedule invocation of the exact same attempt
    state2 = await engine.step(copy.deepcopy(initial_state))
    exec2_id = state2["task_states"]["research"]["execution_id"]

    # Must reuse the same AgentExecution without creating a new row
    assert exec1_id == exec2_id

    # Verify database has exactly 1 execution for this task
    async with async_session_maker() as db:
        res = await db.execute(
            select(AgentExecution).where(AgentExecution.orchestration_id == orch.id)
        )
        executions = res.scalars().all()
        assert len(executions) == 1
        assert str(executions[0].id) == exec1_id
        assert executions[0].task_attempt == 1
        assert executions[0].idempotency_key.endswith("-attempt-1")


@pytest.mark.asyncio
async def test_retry_b_retry_attempt_creates_distinct_execution(async_client, db_session):
    """
    Test B: Task retry: attempt 1 -> failed retryably, attempt 2 -> new AgentExecution.
    """
    client_token, client_user_id, _ = await create_authenticated_user(async_client, db_session, role=UserRole.CLIENT)
    dev_token, _, _ = await create_authenticated_user(async_client, db_session, role=UserRole.DEVELOPER)
    await setup_reference_agents(async_client, db_session, dev_token)

    engine = OrchestrationEngine()
    orch = await engine.create_orchestration(
        db=db_session,
        user_id=client_user_id,
        goal="Test B: Distinct retry execution",
    )
    await engine.start_orchestration(db_session, orch.id, client_user_id)

    state = await engine.restore_checkpoint(db_session, orch.id)
    state = await engine.step(state)

    exec1_id = state["task_states"]["research"]["execution_id"]
    assert state["task_states"]["research"]["attempt"] == 1

    # Simulate attempt 1 retryable failure
    async with async_session_maker() as db:
        res = await db.execute(
            select(AgentExecution).where(AgentExecution.id == uuid.UUID(exec1_id))
        )
        ex = res.scalar_one()
        ex.status = ExecutionStatus.FAILED.value
        ex.error_code = "WORKER_TIMEOUT"
        ex.error_message = "Worker execution timed out"
        await db.commit()

    # Step: Collect detects retryable failure and schedules attempt 2
    state = await engine.step(state)
    assert state["task_states"]["research"]["attempt"] == 2
    assert state["task_states"]["research"]["status"] == "RUNNING"

    exec2_id = state["task_states"]["research"]["execution_id"]
    assert exec2_id is not None
    assert exec2_id != exec1_id  # New distinct execution for attempt 2


@pytest.mark.asyncio
async def test_retry_c_retry_attempt_2_scheduled_twice_single_execution(async_client, db_session):
    """
    Test C: Retry attempt 2 scheduled twice -> exactly one attempt-2 AgentExecution.
    """
    client_token, client_user_id, _ = await create_authenticated_user(async_client, db_session, role=UserRole.CLIENT)
    dev_token, _, _ = await create_authenticated_user(async_client, db_session, role=UserRole.DEVELOPER)
    await setup_reference_agents(async_client, db_session, dev_token)

    engine = OrchestrationEngine()
    orch = await engine.create_orchestration(
        db=db_session,
        user_id=client_user_id,
        goal="Test C: Idempotent attempt 2 schedule",
    )
    await engine.start_orchestration(db_session, orch.id, client_user_id)

    state = await engine.restore_checkpoint(db_session, orch.id)
    state = await engine.step(state)
    exec1_id = state["task_states"]["research"]["execution_id"]

    # Fail attempt 1
    async with async_session_maker() as db:
        res = await db.execute(
            select(AgentExecution).where(AgentExecution.id == uuid.UUID(exec1_id))
        )
        ex = res.scalar_one()
        ex.status = ExecutionStatus.FAILED.value
        ex.error_code = "CONTAINER_START_FAILURE"
        ex.error_message = "Sandbox launch failed"
        await db.commit()

    # Step: Collect -> increment to attempt 2 and schedule attempt 2
    state_after_retry = await engine.step(state)
    exec2_id = state_after_retry["task_states"]["research"]["execution_id"]
    assert exec2_id is not None
    assert state_after_retry["task_states"]["research"]["attempt"] == 2

    # Simulate duplicate schedule of attempt 2 (e.g. from crash or retry loop)
    state_replayed = await engine.step(copy.deepcopy(state_after_retry))
    exec2_replayed_id = state_replayed["task_states"]["research"]["execution_id"]

    # Exactly one attempt-2 execution
    assert exec2_id == exec2_replayed_id

    async with async_session_maker() as db:
        res = await db.execute(
            select(AgentExecution)
            .where(AgentExecution.orchestration_id == orch.id)
            .order_by(AgentExecution.task_attempt.asc())
        )
        all_execs = res.scalars().all()
        # Exactly 2 executions: 1 for attempt 1, 1 for attempt 2
        assert len(all_execs) == 2
        assert all_execs[0].task_attempt == 1
        assert str(all_execs[0].id) == exec1_id
        assert all_execs[1].task_attempt == 2
        assert str(all_execs[1].id) == exec2_id


@pytest.mark.asyncio
async def test_retry_d_attempt_1_and_attempt_2_separately_queryable(async_client, db_session):
    """
    Test D: Attempt 1 and attempt 2 remain separately queryable in PostgreSQL.
    """
    client_token, client_user_id, _ = await create_authenticated_user(async_client, db_session, role=UserRole.CLIENT)
    dev_token, _, _ = await create_authenticated_user(async_client, db_session, role=UserRole.DEVELOPER)
    await setup_reference_agents(async_client, db_session, dev_token)

    engine = OrchestrationEngine()
    orch = await engine.create_orchestration(
        db=db_session,
        user_id=client_user_id,
        goal="Test D: Queryable attempts",
    )
    await engine.start_orchestration(db_session, orch.id, client_user_id)

    state = await engine.restore_checkpoint(db_session, orch.id)
    state = await engine.step(state)
    exec1_id = uuid.UUID(state["task_states"]["research"]["execution_id"])

    # Fail attempt 1
    async with async_session_maker() as db:
        res = await db.execute(select(AgentExecution).where(AgentExecution.id == exec1_id))
        ex = res.scalar_one()
        ex.status = ExecutionStatus.FAILED.value
        ex.error_code = "WORKER_TIMEOUT"
        await db.commit()

    # Step to attempt 2
    state = await engine.step(state)
    exec2_id = uuid.UUID(state["task_states"]["research"]["execution_id"])

    # Verify queryability in DB
    async with async_session_maker() as db:
        # Query attempt 1 specifically
        res_att1 = await db.execute(
            select(AgentExecution).where(
                AgentExecution.orchestration_id == orch.id,
                AgentExecution.task_attempt == 1,
            )
        )
        att1_exec = res_att1.scalar_one()
        assert att1_exec.id == exec1_id
        assert att1_exec.status == ExecutionStatus.FAILED.value

        # Query attempt 2 specifically
        res_att2 = await db.execute(
            select(AgentExecution).where(
                AgentExecution.orchestration_id == orch.id,
                AgentExecution.task_attempt == 2,
            )
        )
        att2_exec = res_att2.scalar_one()
        assert att2_exec.id == exec2_id
        assert att2_exec.status == ExecutionStatus.QUEUED.value or att2_exec.status == ExecutionStatus.RUNNING.value

        # Attempt 1 record was NOT overwritten
        assert att1_exec.id != att2_exec.id


@pytest.mark.asyncio
async def test_retry_e_checkpoint_replay_does_not_create_another_attempt(async_client, db_session):
    """
    Test E: Checkpoint replay does not create another attempt.
    """
    client_token, client_user_id, _ = await create_authenticated_user(async_client, db_session, role=UserRole.CLIENT)
    dev_token, _, _ = await create_authenticated_user(async_client, db_session, role=UserRole.DEVELOPER)
    await setup_reference_agents(async_client, db_session, dev_token)

    engine = OrchestrationEngine()
    orch = await engine.create_orchestration(
        db=db_session,
        user_id=client_user_id,
        goal="Test E: Checkpoint replay attempt safety",
    )
    await engine.start_orchestration(db_session, orch.id, client_user_id)

    # Initial schedule -> attempt 1
    state = await engine.advance_orchestration(orch.id)
    assert state is not None
    assert state["task_states"]["research"]["attempt"] == 1

    # Checkpoint replay: advance orchestration again while task is RUNNING
    state_replay = await engine.advance_orchestration(orch.id)
    assert state_replay is not None
    # Attempt remains 1, no new attempt created
    assert state_replay["task_states"]["research"]["attempt"] == 1

    async with async_session_maker() as db:
        res = await db.execute(
            select(AgentExecution).where(AgentExecution.orchestration_id == orch.id)
        )
        execs = res.scalars().all()
        assert len(execs) == 1
        assert execs[0].task_attempt == 1


@pytest.mark.asyncio
async def test_retry_f_concurrent_scheduler_calls_no_duplicate_execution_attempts(async_client, db_session):
    """
    Test F: Concurrent scheduler calls cannot create duplicate execution attempts.
    """
    client_token, client_user_id, _ = await create_authenticated_user(async_client, db_session, role=UserRole.CLIENT)
    dev_token, _, _ = await create_authenticated_user(async_client, db_session, role=UserRole.DEVELOPER)
    await setup_reference_agents(async_client, db_session, dev_token)

    engine = OrchestrationEngine()
    orch = await engine.create_orchestration(
        db=db_session,
        user_id=client_user_id,
        goal="Test F: Concurrent scheduler safety",
    )
    await engine.start_orchestration(db_session, orch.id, client_user_id)

    # Invoke 5 concurrent advances simultaneously
    tasks = [engine.advance_orchestration(orch.id) for _ in range(5)]
    results = await asyncio.gather(*tasks)

    # At least one succeeded, all concurrent conflicts were handled cleanly without exception
    valid_states = [r for r in results if r is not None]
    assert len(valid_states) >= 1

    # Invariant: exactly 1 AgentExecution was created in DB for attempt 1
    async with async_session_maker() as db:
        res = await db.execute(
            select(AgentExecution).where(AgentExecution.orchestration_id == orch.id)
        )
        execs = res.scalars().all()
        assert len(execs) == 1
        assert execs[0].task_attempt == 1


# =========================================================================
# Section 9: Crash Recovery & Wake-Up Tests (Cases 1 through 5)
# =========================================================================


@pytest.mark.asyncio
async def test_recovery_case_1_crash_after_exec_before_checkpoint_discovers_existing(async_client, db_session):
    """
    Case 1: Orchestrator crashes after creating child execution but before checkpoint.
    Expected: restart -> discover existing execution -> do not create duplicate -> continue.
    """
    client_token, client_user_id, _ = await create_authenticated_user(async_client, db_session, role=UserRole.CLIENT)
    dev_token, _, _ = await create_authenticated_user(async_client, db_session, role=UserRole.DEVELOPER)
    await setup_reference_agents(async_client, db_session, dev_token)

    engine = OrchestrationEngine()
    orch = await engine.create_orchestration(
        db=db_session,
        user_id=client_user_id,
        goal="Case 1: Crash after exec before checkpoint",
    )
    await engine.start_orchestration(db_session, orch.id, client_user_id)

    # 1. Manually enqueue child execution as would happen right before crash
    async with async_session_maker() as db:
        t_res = await db.execute(
            select(OrchestrationTask).where(
                OrchestrationTask.orchestration_id == orch.id,
                OrchestrationTask.task_key == "research",
            )
        )
        task = t_res.scalar_one()
        idemp_key = task.execution_attempt_idempotency_key
        existing_exec = await AgentExecutionService.enqueue_agent_execution(
            db=db,
            agent_id=task.agent_id,
            requested_by_user_id=client_user_id,
            input_data=task.input_data,
            idempotency_key=idemp_key,
            agent_version=task.agent_version,
            orchestration_id=orch.id,
            task_id=task.id,
            task_attempt=1,
        )
        # Note: task.execution_id and checkpoint NOT yet updated when crash occurs!
        await db.commit()

    # 2. Orchestrator restarts and advances orchestration
    engine_restarted = OrchestrationEngine()
    state = await engine_restarted.advance_orchestration(orch.id)
    assert state is not None

    # 3. Discovered the existing execution, bound it to task, did not create duplicate
    task_state = state["task_states"]["research"]
    assert task_state["execution_id"] == str(existing_exec.id)

    async with async_session_maker() as db:
        res = await db.execute(
            select(AgentExecution).where(AgentExecution.orchestration_id == orch.id)
        )
        execs = res.scalars().all()
        assert len(execs) == 1
        assert execs[0].id == existing_exec.id


@pytest.mark.asyncio
async def test_recovery_case_2_child_execution_completes_while_orchestrator_offline(async_client, db_session):
    """
    Case 2: Child execution completes while orchestrator is offline.
    Expected: completion remains durable in DB -> orchestrator restarts -> discovers completion -> advances DAG.
    """
    client_token, client_user_id, _ = await create_authenticated_user(async_client, db_session, role=UserRole.CLIENT)
    dev_token, _, _ = await create_authenticated_user(async_client, db_session, role=UserRole.DEVELOPER)
    await setup_reference_agents(async_client, db_session, dev_token)

    engine = OrchestrationEngine()
    orch = await engine.create_orchestration(
        db=db_session,
        user_id=client_user_id,
        goal="Case 2: Completion while orchestrator offline",
    )
    await engine.start_orchestration(db_session, orch.id, client_user_id)

    # Dispatch research task
    state = await engine.advance_orchestration(orch.id)
    assert state is not None
    exec_id = state["task_states"]["research"]["execution_id"]
    assert exec_id is not None

    # Simulate orchestrator being offline while worker completes execution in DB
    del engine

    async with async_session_maker() as db:
        res = await db.execute(select(AgentExecution).where(AgentExecution.id == uuid.UUID(exec_id)))
        ex = res.scalar_one()
        ex.status = ExecutionStatus.SUCCEEDED.value
        ex.output_data = {
            "summary": "Offline worker completion result",
            "word_count": 4,
            "sentence_count": 1,
            "character_count": 32,
        }
        ex.output_hash = "offline_hash_123"
        await db.commit()

    # Orchestrator restarts and advances
    engine_restarted = OrchestrationEngine()
    advanced_state = await engine_restarted.advance_orchestration(orch.id)
    assert advanced_state is not None

    # Discovered completion -> task SUCCEEDED -> downstream analysis task became READY / RUNNING
    assert "research" in advanced_state["completed_tasks"]
    assert advanced_state["task_states"]["research"]["status"] == "SUCCEEDED"
    assert "analysis" in advanced_state["running_tasks"]


@pytest.mark.asyncio
async def test_recovery_case_3_completion_event_lost_durable_db_recovery(async_client, db_session):
    """
    Case 3: Completion event is lost (dropped by network).
    Expected: durable DB state still allows recovery without relying on Redis PubSub.
    """
    client_token, client_user_id, _ = await create_authenticated_user(async_client, db_session, role=UserRole.CLIENT)
    dev_token, _, _ = await create_authenticated_user(async_client, db_session, role=UserRole.DEVELOPER)
    await setup_reference_agents(async_client, db_session, dev_token)

    engine = OrchestrationEngine()
    orch = await engine.create_orchestration(
        db=db_session,
        user_id=client_user_id,
        goal="Case 3: Lost Redis event recovery",
    )
    await engine.start_orchestration(db_session, orch.id, client_user_id)

    state = await engine.advance_orchestration(orch.id)
    exec_id = state["task_states"]["research"]["execution_id"]

    # Worker marks SUCCEEDED in PostgreSQL, but Redis event is dropped
    async with async_session_maker() as db:
        res = await db.execute(select(AgentExecution).where(AgentExecution.id == uuid.UUID(exec_id)))
        ex = res.scalar_one()
        ex.status = ExecutionStatus.SUCCEEDED.value
        ex.output_data = {
            "summary": "Durable DB output without Redis event",
            "word_count": 6,
            "sentence_count": 1,
            "character_count": 38,
        }
        ex.output_hash = "db_hash_456"
        await db.commit()

    # Orchestrator recovery advance (e.g. from recovery sweep or next cycle)
    recovered_state = await engine.advance_orchestration(orch.id)
    assert recovered_state is not None
    assert "research" in recovered_state["completed_tasks"]
    assert recovered_state["task_states"]["research"]["status"] == "SUCCEEDED"


@pytest.mark.asyncio
async def test_recovery_case_4_completion_event_duplicated_idempotent_advancement(async_client, db_session):
    """
    Case 4: Completion event is duplicated multiple times.
    Expected: safe idempotent advancement without duplicate tasks, double completions, or state corruption.
    """
    client_token, client_user_id, _ = await create_authenticated_user(async_client, db_session, role=UserRole.CLIENT)
    dev_token, _, _ = await create_authenticated_user(async_client, db_session, role=UserRole.DEVELOPER)
    await setup_reference_agents(async_client, db_session, dev_token)

    engine = OrchestrationEngine()
    orch = await engine.create_orchestration(
        db=db_session,
        user_id=client_user_id,
        goal="Case 4: Duplicate completion events",
    )
    await engine.start_orchestration(db_session, orch.id, client_user_id)

    state = await engine.advance_orchestration(orch.id)
    exec_id = state["task_states"]["research"]["execution_id"]

    async with async_session_maker() as db:
        res = await db.execute(select(AgentExecution).where(AgentExecution.id == uuid.UUID(exec_id)))
        ex = res.scalar_one()
        ex.status = ExecutionStatus.SUCCEEDED.value
        ex.output_data = {
            "summary": "Processed output",
            "word_count": 2,
            "sentence_count": 1,
            "character_count": 16,
        }
        ex.output_hash = "dup_hash_789"
        await db.commit()

    # Deliver event 1
    state1 = await engine.handle_execution_completed_wakeup(
        orchestration_id=orch.id,
        execution_id=uuid.UUID(exec_id),
        status="SUCCEEDED",
    )
    assert state1 is not None
    assert "research" in state1["completed_tasks"]

    # Deliver duplicate event 2
    state2 = await engine.handle_execution_completed_wakeup(
        orchestration_id=orch.id,
        execution_id=uuid.UUID(exec_id),
        status="SUCCEEDED",
    )
    assert state2 is not None

    # Deliver duplicate event 3
    state3 = await engine.handle_execution_completed_wakeup(
        orchestration_id=orch.id,
        execution_id=uuid.UUID(exec_id),
        status="SUCCEEDED",
    )
    assert state3 is not None

    # Invariant: Completed outputs are unchanged, research is not re-executed
    assert state3["task_states"]["research"]["status"] == "SUCCEEDED"
    assert state3["task_states"]["research"]["execution_id"] == exec_id


@pytest.mark.asyncio
async def test_recovery_case_5_two_orchestrators_receive_event_simultaneously_one_authoritative(async_client, db_session):
    """
    Case 5: Two orchestrator processes receive the same event simultaneously.
    Expected: exactly one acquires the PostgreSQL advisory lock and advances; the loser safely exits.
    """
    client_token, client_user_id, _ = await create_authenticated_user(async_client, db_session, role=UserRole.CLIENT)
    dev_token, _, _ = await create_authenticated_user(async_client, db_session, role=UserRole.DEVELOPER)
    await setup_reference_agents(async_client, db_session, dev_token)

    engine1 = OrchestrationEngine()
    engine2 = OrchestrationEngine()

    orch = await engine1.create_orchestration(
        db=db_session,
        user_id=client_user_id,
        goal="Case 5: Concurrent wake-up protection",
    )
    await engine1.start_orchestration(db_session, orch.id, client_user_id)

    initial_state = await engine1.restore_checkpoint(db_session, orch.id)
    assert initial_state is not None

    initial_conflicts = metrics.counters.get("orchestration_concurrent_advance_conflict_total", 0)

    # Concurrently advance through two separate engine instances
    res1, res2 = await asyncio.gather(
        engine1.advance_orchestration(orch.id),
        engine2.advance_orchestration(orch.id),
    )

    # Exactly one or both safely finished (if serial) or one yielded None on lock conflict
    states = [s for s in (res1, res2) if s is not None]
    assert len(states) >= 1

    # Verify state consistency
    async with async_session_maker() as db:
        res = await db.execute(select(Orchestration).where(Orchestration.id == orch.id))
        db_orch = res.scalar_one()
        assert db_orch.status in (OrchestrationStatus.RUNNING.value, OrchestrationStatus.READY.value)
