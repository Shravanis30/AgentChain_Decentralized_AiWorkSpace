from datetime import timedelta
import uuid
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.research_agent import RESEARCH_AGENT_MANIFEST
from app.core.metrics import metrics
from app.models.agent import AgentExecution, ExecutionStatus
from app.models.base import utc_now
from app.models.user import UserRole
from app.services.state_machine import (
    ExecutionStateMachine,
    InvalidStateTransitionError,
    StaleLeaseConflictError,
)
from tests.test_agents import create_authenticated_user


def make_dummy_execution(status: str) -> AgentExecution:
    return AgentExecution(
        id=uuid.uuid4(),
        agent_id=uuid.uuid4(),
        agent_version_id=uuid.uuid4(),
        requested_by=uuid.uuid4(),
        status=status,
        input_data={"test": "data"},
        input_hash="x" * 64,
        queued_at=utc_now(),
    )


async def create_real_execution(async_client, db_session) -> AgentExecution:
    dev_token, _, _ = await create_authenticated_user(async_client, db_session, role=UserRole.DEVELOPER)
    client_token, _, _ = await create_authenticated_user(async_client, db_session, role=UserRole.CLIENT)
    manifest = RESEARCH_AGENT_MANIFEST.model_dump()
    slug = f"sm-agent-{uuid.uuid4().hex[:8]}"
    create_res = await async_client.post(
        "/api/v1/agents",
        json={"name": "SM Agent", "slug": slug, "manifest": manifest},
        headers={"Authorization": f"Bearer {dev_token}"},
    )
    agent_id = create_res.json()["id"]
    await async_client.post(
        f"/api/v1/agents/{agent_id}/publish",
        headers={"Authorization": f"Bearer {dev_token}"},
    )
    exec_res = await async_client.post(
        f"/api/v1/agents/{agent_id}/execute",
        json={"input": {"text": "State machine test."}},
        headers={"Authorization": f"Bearer {client_token}"},
    )
    exec_id = exec_res.json()["execution_id"]
    return await db_session.get(AgentExecution, uuid.UUID(exec_id))


def test_state_machine_valid_transitions():
    assert ExecutionStateMachine.can_transition(ExecutionStatus.QUEUED.value, ExecutionStatus.RUNNING.value)
    assert ExecutionStateMachine.can_transition(ExecutionStatus.QUEUED.value, ExecutionStatus.CANCELLED.value)
    assert ExecutionStateMachine.can_transition(ExecutionStatus.RUNNING.value, ExecutionStatus.SUCCEEDED.value)
    assert ExecutionStateMachine.can_transition(ExecutionStatus.RUNNING.value, ExecutionStatus.FAILED.value)
    assert ExecutionStateMachine.can_transition(ExecutionStatus.RUNNING.value, ExecutionStatus.TIMED_OUT.value)
    assert ExecutionStateMachine.can_transition(ExecutionStatus.RUNNING.value, ExecutionStatus.CANCELLED.value)
    assert ExecutionStateMachine.can_transition(ExecutionStatus.RUNNING.value, ExecutionStatus.QUEUED.value)


def test_state_machine_invalid_transitions():
    # Terminal states cannot transition to anything
    for terminal in [
        ExecutionStatus.SUCCEEDED.value,
        ExecutionStatus.FAILED.value,
        ExecutionStatus.TIMED_OUT.value,
        ExecutionStatus.CANCELLED.value,
    ]:
        assert not ExecutionStateMachine.can_transition(terminal, ExecutionStatus.RUNNING.value)
        assert not ExecutionStateMachine.can_transition(terminal, ExecutionStatus.QUEUED.value)
        assert not ExecutionStateMachine.can_transition(terminal, ExecutionStatus.SUCCEEDED.value)

    # QUEUED cannot jump directly to SUCCEEDED or TIMED_OUT
    assert not ExecutionStateMachine.can_transition(ExecutionStatus.QUEUED.value, ExecutionStatus.SUCCEEDED.value)
    assert not ExecutionStateMachine.can_transition(ExecutionStatus.QUEUED.value, ExecutionStatus.TIMED_OUT.value)


@pytest.mark.asyncio
async def test_state_machine_validation_raises(db_session: AsyncSession):
    exec_queued = make_dummy_execution(ExecutionStatus.QUEUED.value)
    with pytest.raises(InvalidStateTransitionError):
        ExecutionStateMachine.validate_transition(exec_queued, ExecutionStatus.SUCCEEDED.value)

    exec_succeeded = make_dummy_execution(ExecutionStatus.SUCCEEDED.value)
    with pytest.raises(InvalidStateTransitionError):
        ExecutionStateMachine.validate_transition(exec_succeeded, ExecutionStatus.RUNNING.value)


@pytest.mark.asyncio
async def test_state_machine_lease_ownership_and_stale_prevention(async_client, db_session: AsyncSession):
    """
    Verifies worker lease ownership:
    - Worker A acquires lease
    - Worker B is rejected while lease is active
    - When Worker A lease expires, Worker B can reclaim
    - Woken up Worker A is rejected when attempting to finalize
    - Worker B finalizes successfully
    """
    exec_record = await create_real_execution(async_client, db_session)

    # 1. Worker A acquires lease
    await ExecutionStateMachine.transition_to_running(
        db=db_session,
        execution=exec_record,
        worker_id="worker-A",
        timeout_seconds=300,
        lease_duration_seconds=30,
    )
    assert exec_record.status == ExecutionStatus.RUNNING.value
    assert exec_record.lease_owner == "worker-A"
    assert exec_record.lease_expires_at is not None

    # 2. Worker B tries to claim while Worker A lease is active -> StaleLeaseConflictError
    with pytest.raises(StaleLeaseConflictError):
        await ExecutionStateMachine.transition_to_running(
            db=db_session,
            execution=exec_record,
            worker_id="worker-B",
            timeout_seconds=300,
            lease_duration_seconds=30,
        )

    # 3. Simulate Worker A's lease expiring
    exec_record.lease_expires_at = utc_now() - timedelta(seconds=10)
    await db_session.flush()

    # 4. Worker B reclaims execution with expired lease
    await ExecutionStateMachine.transition_to_running(
        db=db_session,
        execution=exec_record,
        worker_id="worker-B",
        timeout_seconds=300,
        lease_duration_seconds=30,
    )
    assert exec_record.lease_owner == "worker-B"

    # 5. Stale Worker A wakes up and attempts to finalize -> Rejected!
    with pytest.raises(StaleLeaseConflictError):
        await ExecutionStateMachine.transition_to_succeeded(
            db=db_session,
            execution=exec_record,
            output_data={"result": "worker-A-output"},
            output_hash="hash-A",
            execution_time_ms=100,
            worker_id="worker-A",
        )

    # 6. Active Worker B finalizes -> Succeeded!
    await ExecutionStateMachine.transition_to_succeeded(
        db=db_session,
        execution=exec_record,
        output_data={"result": "worker-B-output"},
        output_hash="hash-B",
        execution_time_ms=200,
        worker_id="worker-B",
    )
    assert exec_record.status == ExecutionStatus.SUCCEEDED.value
    assert exec_record.output_data == {"result": "worker-B-output"}
    assert exec_record.lease_owner is None


@pytest.mark.asyncio
async def test_state_machine_idempotent_finalization(async_client, db_session: AsyncSession):
    """
    Verifies that a terminal execution cannot be overwritten by a second result:
    - Initial completion to SUCCEEDED
    - Second worker completion attempt is safely ignored
    - Output data remains unchanged
    - Transition from SUCCEEDED -> RUNNING raises InvalidStateTransitionError
    """
    exec_record = await create_real_execution(async_client, db_session)

    await ExecutionStateMachine.transition_to_running(
        db=db_session,
        execution=exec_record,
        worker_id="worker-1",
    )

    # First completion
    await ExecutionStateMachine.transition_to_succeeded(
        db=db_session,
        execution=exec_record,
        output_data={"answer": 42},
        output_hash="hash-original",
        execution_time_ms=150,
        worker_id="worker-1",
    )
    assert exec_record.status == ExecutionStatus.SUCCEEDED.value

    # Second completion attempt (e.g. duplicate worker or redelivered message)
    conflict_count_before = metrics.counters.get("execution_finalization_conflict_total", 0)
    await ExecutionStateMachine.transition_to_succeeded(
        db=db_session,
        execution=exec_record,
        output_data={"answer": 999},  # Malicious or conflicting second result
        output_hash="hash-duplicate",
        execution_time_ms=250,
        worker_id="worker-2",
    )

    # Verify original result was preserved and not overwritten
    assert exec_record.status == ExecutionStatus.SUCCEEDED.value
    assert exec_record.output_data == {"answer": 42}
    assert exec_record.output_hash == "hash-original"
    conflict_count_after = metrics.counters.get("execution_finalization_conflict_total", 0)
    assert conflict_count_after > conflict_count_before

    # Verify SUCCEEDED -> RUNNING transition is rejected
    with pytest.raises(InvalidStateTransitionError):
        await ExecutionStateMachine.transition_to_running(
            db=db_session,
            execution=exec_record,
            worker_id="worker-3",
        )
