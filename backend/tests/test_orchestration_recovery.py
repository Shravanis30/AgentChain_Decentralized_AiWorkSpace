import copy
import uuid
import pytest
from sqlalchemy import select

from app.agents.research_agent import RESEARCH_AGENT_MANIFEST
from app.core.config import settings
from app.core.metrics import metrics
from app.db.session import async_session_maker
from app.models.agent import AgentExecution, ExecutionStatus
from app.models.orchestration import (
    Orchestration,
    OrchestrationCheckpoint,
    OrchestrationStatus,
    OrchestrationTask,
    OrchestrationTaskStatus,
)
from app.models.user import UserRole
from app.schemas.orchestration import TaskDefinitionInput
from app.services.hashing import compute_canonical_hash
from app.services.orchestration_engine import OrchestrationEngine
from app.services.queue import get_execution_queue, is_cancellation_requested
from app.services.rate_limiter import get_redis_client
from app.services.state_machine import ExecutionStateMachine, InvalidStateTransitionError
from agentchain_worker.worker import AgentExecutionWorker
from agentchain_worker.sandbox.local_runner import LocalProcessSandboxRunner
from tests.test_agents import create_authenticated_user
from tests.test_orchestration_engine import setup_reference_agents


@pytest.mark.asyncio
async def test_case_a_crash_before_child_execution(async_client, db_session):
    """
    Case A: Orchestrator crashes before child execution creation.
    Recovery: Restart recovers from initial checkpoint and schedules child execution cleanly.
    """
    client_token, client_user_id, _ = await create_authenticated_user(async_client, db_session, role=UserRole.CLIENT)
    dev_token, _, _ = await create_authenticated_user(async_client, db_session, role=UserRole.DEVELOPER)
    await setup_reference_agents(async_client, db_session, dev_token)

    engine_1 = OrchestrationEngine()
    orch = await engine_1.create_orchestration(
        db=db_session,
        user_id=client_user_id,
        goal="Crash test before child execution",
    )
    orch = await engine_1.start_orchestration(db_session, orch.id, client_user_id)

    # Simulate crash before step: drop engine_1 instance
    del engine_1

    # Simulate orchestrator restart
    engine_2 = OrchestrationEngine()
    state = await engine_2.restore_checkpoint(db_session, orch.id)
    assert state is not None
    assert state["status"] == OrchestrationStatus.READY.value

    # First step schedules research task cleanly
    state = await engine_2.step(state)
    assert "research" in state["running_tasks"]
    exec_id = state["task_states"]["research"]["execution_id"]
    assert exec_id is not None


@pytest.mark.asyncio
async def test_case_b_and_d_crash_after_child_exec_before_checkpoint_and_duplicate_schedule(async_client, db_session):
    """
    Case B & D: Orchestrator crashes after child execution creation but before checkpoint update.
    Duplicate scheduling of the same DAG task must be idempotent and reuse the execution.
    """
    client_token, client_user_id, _ = await create_authenticated_user(async_client, db_session, role=UserRole.CLIENT)
    dev_token, _, _ = await create_authenticated_user(async_client, db_session, role=UserRole.DEVELOPER)
    await setup_reference_agents(async_client, db_session, dev_token)

    engine = OrchestrationEngine()
    orch = await engine.create_orchestration(
        db=db_session,
        user_id=client_user_id,
        goal="Duplicate scheduling idempotency",
    )
    await engine.start_orchestration(db_session, orch.id, client_user_id)

    initial_state = await engine.restore_checkpoint(db_session, orch.id)
    assert initial_state is not None

    # Step 1: Normal scheduling
    state_after_step = await engine.step(copy.deepcopy(initial_state))
    first_exec_id = state_after_step["task_states"]["research"]["execution_id"]
    assert first_exec_id is not None

    # Simulate crash right before checkpoint persisted in engine:
    # Replay with initial_state (checkpoint was not updated in caller)
    replayed_state = await engine.step(copy.deepcopy(initial_state))
    second_exec_id = replayed_state["task_states"]["research"]["execution_id"]

    # Invariant 6: Must reuse the exact same execution, no duplicate child execution created
    assert first_exec_id == second_exec_id
    assert metrics.counters["orchestration_duplicate_schedule_total"] >= 1


@pytest.mark.asyncio
async def test_case_c_restart_after_task_completion(async_client, db_session):
    """
    Case C: Orchestrator restarts after task completion.
    Recovery: Completed task remains completed, next DAG task proceeds.
    """
    client_token, client_user_id, _ = await create_authenticated_user(async_client, db_session, role=UserRole.CLIENT)
    dev_token, _, _ = await create_authenticated_user(async_client, db_session, role=UserRole.DEVELOPER)
    await setup_reference_agents(async_client, db_session, dev_token)

    real_queue = get_execution_queue()
    worker = AgentExecutionWorker(queue=real_queue, sandbox_runner=LocalProcessSandboxRunner())

    engine = OrchestrationEngine()
    orch = await engine.create_orchestration(
        db=db_session,
        user_id=client_user_id,
        goal="Restart after completion",
    )
    await engine.start_orchestration(db_session, orch.id, client_user_id)
    state = await engine.restore_checkpoint(db_session, orch.id)

    # Schedule research
    state = await engine.step(state)
    res_exec_id = state["task_states"]["research"]["execution_id"]

    # Worker completes research
    claimed = await worker.queue.claim_execution(worker.worker_id)
    target = next((j for j in claimed if j.execution_id == res_exec_id), None)
    assert target is not None
    await worker.process_job(target)

    # Collect research -> SUCCEEDED
    state = await engine.step(state)
    assert "research" in state["completed_tasks"]

    # Simulate crash & restart
    new_engine = OrchestrationEngine()
    restored = await new_engine.restore_checkpoint(db_session, orch.id)
    assert "research" in restored["completed_tasks"]

    # Next step schedules analysis
    restored = await new_engine.step(restored)
    assert "analysis" in restored["running_tasks"]


@pytest.mark.asyncio
async def test_case_e_worker_duplicate_delivery(async_client, db_session):
    """
    Case E: Worker duplicate delivery.
    Terminal execution cannot be executed or finalized twice.
    """
    client_token, client_user_id, _ = await create_authenticated_user(async_client, db_session, role=UserRole.CLIENT)
    dev_token, _, _ = await create_authenticated_user(async_client, db_session, role=UserRole.DEVELOPER)
    await setup_reference_agents(async_client, db_session, dev_token)

    real_queue = get_execution_queue()
    worker = AgentExecutionWorker(queue=real_queue, sandbox_runner=LocalProcessSandboxRunner())

    engine = OrchestrationEngine()
    orch = await engine.create_orchestration(
        db=db_session,
        user_id=client_user_id,
        goal="Duplicate delivery test",
    )
    await engine.start_orchestration(db_session, orch.id, client_user_id)
    state = await engine.restore_checkpoint(db_session, orch.id)
    state = await engine.step(state)
    exec_id = state["task_states"]["research"]["execution_id"]

    # First delivery
    claimed = await worker.queue.claim_execution(worker.worker_id)
    target = next((j for j in claimed if j.execution_id == exec_id), None)
    assert target is not None
    await worker.process_job(target)

    # Verify execution is now SUCCEEDED
    exec_res = await db_session.execute(
        select(AgentExecution).where(AgentExecution.id == uuid.UUID(exec_id))
    )
    child_exec = exec_res.scalar_one()
    assert child_exec.status == ExecutionStatus.SUCCEEDED.value

    # Duplicate delivery attempt: worker attempting to transition terminal execution raises
    with pytest.raises(InvalidStateTransitionError):
        await ExecutionStateMachine.transition_to_running(
            db=db_session,
            execution=child_exec,
            worker_id="second_worker",
        )


@pytest.mark.asyncio
async def test_case_f_and_g_task_retry_and_exceed_max_attempts(async_client, db_session):
    """
    Case F & G:
    F: Transient/timeout error triggers task retry when attempt < max_attempts.
    G: Exceeding max attempts marks task and orchestration as FAILED.
    """
    client_token, client_user_id, _ = await create_authenticated_user(async_client, db_session, role=UserRole.CLIENT)
    dev_token, _, _ = await create_authenticated_user(async_client, db_session, role=UserRole.DEVELOPER)
    await setup_reference_agents(async_client, db_session, dev_token)

    engine = OrchestrationEngine()
    # Create orchestration with task having max_attempts=2
    custom_tasks = [
        TaskDefinitionInput(
            task_key="flaky_task",
            capability="text_summarization",
            input={"text": "retry test"},
            depends_on=[],
            max_attempts=2,
        )
    ]
    orch = await engine.create_orchestration(
        db=db_session,
        user_id=client_user_id,
        goal="Retry failure test",
        tasks=custom_tasks,
    )
    await engine.start_orchestration(db_session, orch.id, client_user_id)
    state = await engine.restore_checkpoint(db_session, orch.id)

    # Attempt 1: Schedule
    state = await engine.step(state)
    exec_1_id = state["task_states"]["flaky_task"]["execution_id"]

    # Simulate child execution TIMED_OUT (retryable error)
    async with async_session_maker() as db:
        res = await db.execute(
            select(AgentExecution).where(AgentExecution.id == uuid.UUID(exec_1_id))
        )
        ex = res.scalar_one()
        ex.status = ExecutionStatus.TIMED_OUT.value
        ex.error_code = "TIMED_OUT"
        ex.error_message = "Worker execution timed out"
        await db.commit()

    # Step: Collect triggers retry (attempt 1 -> 2) and automatically schedules attempt 2 (RUNNING)
    state = await engine.step(state)
    assert state["task_states"]["flaky_task"]["attempt"] == 2
    assert state["task_states"]["flaky_task"]["status"] == "RUNNING"
    assert metrics.counters["orchestration_task_retry_total"] >= 1
    exec_2_id = state["task_states"]["flaky_task"]["execution_id"]
    assert exec_2_id != exec_1_id  # New execution for attempt 2

    # Simulate second timeout failure
    async with async_session_maker() as db:
        res = await db.execute(
            select(AgentExecution).where(AgentExecution.id == uuid.UUID(exec_2_id))
        )
        ex = res.scalar_one()
        ex.status = ExecutionStatus.TIMED_OUT.value
        ex.error_code = "TIMED_OUT"
        ex.error_message = "Worker execution timed out second time"
        await db.commit()

    # Step: Collect should now mark task FAILED because attempt (2) >= max_attempts (2)
    state = await engine.step(state)
    assert "flaky_task" in state["failed_tasks"]
    assert state["status"] == OrchestrationStatus.FAILED.value
    assert metrics.counters["orchestration_task_failed_total"] >= 1
    assert metrics.counters["orchestration_failed_total"] >= 1


@pytest.mark.asyncio
async def test_case_h_one_branch_fails_downstream_skipped(async_client, db_session):
    """
    Case H: One DAG branch fails. Orchestration fails and downstream tasks are SKIPPED.
    """
    client_token, client_user_id, _ = await create_authenticated_user(async_client, db_session, role=UserRole.CLIENT)
    dev_token, _, _ = await create_authenticated_user(async_client, db_session, role=UserRole.DEVELOPER)
    await setup_reference_agents(async_client, db_session, dev_token)

    engine = OrchestrationEngine()
    tasks = [
        TaskDefinitionInput(
            task_key="critical_root",
            capability="text_summarization",
            input={"text": "root"},
            depends_on=[],
            max_attempts=1,
        ),
        TaskDefinitionInput(
            task_key="downstream",
            capability="analysis",
            input={"text": "downstream"},
            depends_on=["critical_root"],
        ),
    ]
    orch = await engine.create_orchestration(
        db=db_session,
        user_id=client_user_id,
        goal="Branch failure test",
        tasks=tasks,
    )
    await engine.start_orchestration(db_session, orch.id, client_user_id)
    state = await engine.restore_checkpoint(db_session, orch.id)

    # Schedule critical_root
    state = await engine.step(state)
    exec_id = state["task_states"]["critical_root"]["execution_id"]

    # Non-retryable failure (invalid input / schema error)
    async with async_session_maker() as db:
        res = await db.execute(
            select(AgentExecution).where(AgentExecution.id == uuid.UUID(exec_id))
        )
        ex = res.scalar_one()
        ex.status = ExecutionStatus.FAILED.value
        ex.error_code = "SCHEMA_ERROR"
        ex.error_message = "Permanent schema error"
        await db.commit()

    # Collect failure: critical_root fails -> fail_orchestration node runs
    state = await engine.step(state)
    assert state["status"] == OrchestrationStatus.FAILED.value

    # Verify downstream task marked SKIPPED in DB
    async with async_session_maker() as db:
        orch_db = await engine.get_orchestration(db, orch.id)
        downstream = next(t for t in orch_db.tasks if t.task_key == "downstream")
        assert downstream.status == OrchestrationTaskStatus.SKIPPED.value


@pytest.mark.asyncio
async def test_case_i_independent_branches_concurrency(async_client, db_session):
    """
    Case I: Independent branches execute concurrently, respecting MAX_CONCURRENT_TASKS_PER_ORCHESTRATION.
    """
    client_token, client_user_id, _ = await create_authenticated_user(async_client, db_session, role=UserRole.CLIENT)
    dev_token, _, _ = await create_authenticated_user(async_client, db_session, role=UserRole.DEVELOPER)
    await setup_reference_agents(async_client, db_session, dev_token)

    engine = OrchestrationEngine()
    # Create 5 parallel root tasks
    tasks = [
        TaskDefinitionInput(
            task_key=f"parallel_{i}",
            capability="text_summarization",
            input={"text": f"text {i}"},
            depends_on=[],
        )
        for i in range(5)
    ]
    orch = await engine.create_orchestration(
        db=db_session,
        user_id=client_user_id,
        goal="Concurrency test",
        tasks=tasks,
    )
    await engine.start_orchestration(db_session, orch.id, client_user_id)
    state = await engine.restore_checkpoint(db_session, orch.id)

    # Schedule: Should only schedule up to MAX_CONCURRENT_TASKS_PER_ORCHESTRATION (default 3)
    state = await engine.step(state)
    assert len(state["running_tasks"]) == settings.MAX_CONCURRENT_TASKS_PER_ORCHESTRATION
    assert len(state["running_tasks"]) == 3


@pytest.mark.asyncio
async def test_case_j_and_k_cancellation_propagation(async_client, db_session):
    """
    Case J & K:
    J: Cancellation while task is pending cancels pending tasks.
    K: Cancellation while task is running cancels execution and signals worker.
    """
    client_token, client_user_id, _ = await create_authenticated_user(async_client, db_session, role=UserRole.CLIENT)
    dev_token, _, _ = await create_authenticated_user(async_client, db_session, role=UserRole.DEVELOPER)
    await setup_reference_agents(async_client, db_session, dev_token)

    engine = OrchestrationEngine()
    orch = await engine.create_orchestration(
        db=db_session,
        user_id=client_user_id,
        goal="Cancellation propagation test",
    )
    await engine.start_orchestration(db_session, orch.id, client_user_id)
    state = await engine.restore_checkpoint(db_session, orch.id)

    # Schedule research (RUNNING), analysis is PENDING
    state = await engine.step(state)
    running_exec_id = state["task_states"]["research"]["execution_id"]
    assert running_exec_id is not None

    # Cancel orchestration
    cancel_res = await async_client.post(
        f"/api/v1/orchestrations/{orch.id}/cancel",
        json={"reason": "User requested abort"},
        headers={"Authorization": f"Bearer {client_token}"},
    )
    assert cancel_res.status_code == 200
    assert cancel_res.json()["status"] == "CANCELLED"

    # Verify:
    # 1. Running task child execution cancelled
    exec_res = await db_session.execute(
        select(AgentExecution).where(AgentExecution.id == uuid.UUID(running_exec_id))
    )
    child_exec = exec_res.scalar_one()
    assert child_exec.status == ExecutionStatus.CANCELLED.value

    # 2. Redis cancellation flag set
    redis = get_redis_client()
    assert await is_cancellation_requested(redis, running_exec_id) is True

    # 3. Pending tasks (analysis, synthesis) cancelled in DB
    async with async_session_maker() as db:
        orch_db = await engine.get_orchestration(db, orch.id)
        for t in orch_db.tasks:
            assert t.status == OrchestrationTaskStatus.CANCELLED.value


@pytest.mark.asyncio
async def test_case_l_checkpoint_restoration_integrity(async_client, db_session):
    """
    Case L: Checkpoint restoration after orchestrator restart verifies SHA-256 hash.
    """
    client_token, client_user_id, _ = await create_authenticated_user(async_client, db_session, role=UserRole.CLIENT)
    dev_token, _, _ = await create_authenticated_user(async_client, db_session, role=UserRole.DEVELOPER)
    await setup_reference_agents(async_client, db_session, dev_token)

    engine = OrchestrationEngine()
    orch = await engine.create_orchestration(
        db=db_session,
        user_id=client_user_id,
        goal="State hash integrity test",
    )

    restored = await engine.restore_checkpoint(db_session, orch.id)
    assert restored is not None
    assert compute_canonical_hash(restored) is not None


@pytest.mark.asyncio
async def test_case_m_cycle_rejection_via_api(async_client, db_session):
    """
    Case M: Cycle rejection via API.
    """
    client_token, _, _ = await create_authenticated_user(async_client, db_session, role=UserRole.CLIENT)

    cyclic_tasks = [
        {"task_key": "task_a", "depends_on": ["task_b"]},
        {"task_key": "task_b", "depends_on": ["task_a"]},
    ]
    res = await async_client.post(
        "/api/v1/orchestrations",
        json={"goal": "Cycle goal", "tasks": cyclic_tasks},
        headers={"Authorization": f"Bearer {client_token}"},
    )
    assert res.status_code == 422
    assert "Cycle detected" in res.json()["detail"]


@pytest.mark.asyncio
async def test_case_n_graph_size_limit_rejection_via_api(async_client, db_session):
    """
    Case N: Graph size limit rejection via API.
    """
    client_token, _, _ = await create_authenticated_user(async_client, db_session, role=UserRole.CLIENT)

    too_many_tasks = [
        {"task_key": f"task_{i}", "depends_on": []}
        for i in range(settings.MAX_GRAPH_TASKS + 1)
    ]
    res = await async_client.post(
        "/api/v1/orchestrations",
        json={"goal": "Too big goal", "tasks": too_many_tasks},
        headers={"Authorization": f"Bearer {client_token}"},
    )
    assert res.status_code == 422
    assert "exceeding the maximum allowed limit" in res.json()["detail"]


@pytest.mark.asyncio
async def test_case_o_cross_user_orchestration_access_rejection(async_client, db_session):
    """
    Case O: Cross-user orchestration access rejection.
    User B cannot view, start, or cancel User A's orchestration.
    """
    user_a_token, _, _ = await create_authenticated_user(async_client, db_session, role=UserRole.CLIENT)
    user_b_token, _, _ = await create_authenticated_user(async_client, db_session, role=UserRole.CLIENT)
    dev_token, _, _ = await create_authenticated_user(async_client, db_session, role=UserRole.DEVELOPER)
    await setup_reference_agents(async_client, db_session, dev_token)

    create_res = await async_client.post(
        "/api/v1/orchestrations",
        json={"goal": "User A Private Orchestration"},
        headers={"Authorization": f"Bearer {user_a_token}"},
    )
    orch_id = create_res.json()["id"]

    # 1. User B GET -> 403 Forbidden
    get_res = await async_client.get(
        f"/api/v1/orchestrations/{orch_id}",
        headers={"Authorization": f"Bearer {user_b_token}"},
    )
    assert get_res.status_code == 403

    # 2. User B START -> 403 Forbidden
    start_res = await async_client.post(
        f"/api/v1/orchestrations/{orch_id}/start",
        headers={"Authorization": f"Bearer {user_b_token}"},
    )
    assert start_res.status_code == 403

    # 3. User B CANCEL -> 403 Forbidden
    cancel_res = await async_client.post(
        f"/api/v1/orchestrations/{orch_id}/cancel",
        json={"reason": "Intruder cancel"},
        headers={"Authorization": f"Bearer {user_b_token}"},
    )
    assert cancel_res.status_code == 403

    # 4. User B tasks -> 403 Forbidden
    tasks_res = await async_client.get(
        f"/api/v1/orchestrations/{orch_id}/tasks",
        headers={"Authorization": f"Bearer {user_b_token}"},
    )
    assert tasks_res.status_code == 403
