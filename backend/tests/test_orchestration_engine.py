import copy
import uuid
import pytest
from sqlalchemy import select

from app.agents.analysis_agent import ANALYSIS_AGENT_MANIFEST
from app.agents.research_agent import RESEARCH_AGENT_MANIFEST
from app.agents.synthesis_agent import SYNTHESIS_AGENT_MANIFEST
from app.core.config import settings
from app.models.agent import Agent, AgentExecution, AgentStatus, ExecutionStatus
from app.models.orchestration import (
    Artifact,
    Orchestration,
    OrchestrationCheckpoint,
    OrchestrationStatus,
    OrchestrationTask,
    OrchestrationTaskStatus,
)
from app.models.outbox import ExecutionOutbox
from app.models.user import UserRole
from app.schemas.orchestration import TaskDefinitionInput
from app.services.agent_selector import DeterministicAgentSelector
from app.services.hashing import compute_canonical_hash
from app.services.orchestration_engine import OrchestrationEngine, orchestration_engine
from app.services.queue import get_execution_queue
from agentchain_worker.worker import AgentExecutionWorker
from agentchain_worker.sandbox.local_runner import LocalProcessSandboxRunner
from tests.test_agents import create_authenticated_user


async def setup_reference_agents(async_client, db_session, dev_token):
    """Helper to publish research, analysis, and synthesis agents."""
    agents_map = {}
    manifests = [
        ("research", RESEARCH_AGENT_MANIFEST),
        ("analysis", ANALYSIS_AGENT_MANIFEST),
        ("synthesis", SYNTHESIS_AGENT_MANIFEST),
    ]
    for key, manifest_obj in manifests:
        manifest = manifest_obj.model_dump()
        slug = f"{manifest['agent']['slug']}-{uuid.uuid4().hex[:6]}"
        create_res = await async_client.post(
            "/api/v1/agents",
            json={
                "name": manifest["agent"]["name"],
                "slug": slug,
                "manifest": manifest,
            },
            headers={"Authorization": f"Bearer {dev_token}"},
        )
        assert create_res.status_code == 201
        agent_id = create_res.json()["id"]

        # Validate & publish
        v_res = await async_client.post(
            f"/api/v1/agents/{agent_id}/validate",
            headers={"Authorization": f"Bearer {dev_token}"},
        )
        assert v_res.status_code == 200

        p_res = await async_client.post(
            f"/api/v1/agents/{agent_id}/publish",
            headers={"Authorization": f"Bearer {dev_token}"},
        )
        assert p_res.status_code == 200
        agents_map[key] = p_res.json()

    return agents_map


@pytest.mark.asyncio
async def test_deterministic_agent_selection(async_client, db_session):
    dev_token, _, _ = await create_authenticated_user(async_client, db_session, role=UserRole.DEVELOPER)
    agents_map = await setup_reference_agents(async_client, db_session, dev_token)

    selector = DeterministicAgentSelector()

    # 1. Select by explicit agent_id and valid pinned version
    res_agent = agents_map["research"]
    task_explicit = TaskDefinitionInput(
        task_key="res_task",
        agent_id=uuid.UUID(res_agent["id"]),
        agent_version="1.0.0",
        depends_on=[],
    )
    res = await selector.select_agent(db_session, task_explicit)
    ref = res.agent_ref
    assert ref.agent_id == uuid.UUID(res_agent["id"])
    assert ref.agent_version == "1.0.0"

    # 2. Select by capability
    task_cap = TaskDefinitionInput(
        task_key="any_task",
        capability="analysis",
        depends_on=[],
    )
    res_cap = await selector.select_agent(db_session, task_cap)
    ref_cap = res_cap.agent_ref
    assert ref_cap.agent_version == "1.0.0"
    assert "analysis" in ref_cap.slug.lower()

    # 3. Reject non-existent version
    bad_ver_task = TaskDefinitionInput(
        task_key="bad_ver",
        agent_id=uuid.UUID(res_agent["id"]),
        agent_version="9.9.9",
        depends_on=[],
    )
    with pytest.raises(ValueError, match="does not have published version"):
        await selector.select_agent(db_session, bad_ver_task)


@pytest.mark.asyncio
async def test_checkpoint_persistence_and_restore(async_client, db_session):
    dev_token, dev_user_id, _ = await create_authenticated_user(async_client, db_session, role=UserRole.DEVELOPER)
    agents_map = await setup_reference_agents(async_client, db_session, dev_token)

    engine = OrchestrationEngine()
    orch = await engine.create_orchestration(
        db=db_session,
        user_id=dev_user_id,
        goal="Test checkpointing",
    )

    # 1. Restore initial checkpoint
    state = await engine.restore_checkpoint(db_session, orch.id)
    assert state is not None
    assert state["orchestration_id"] == str(orch.id)
    assert state["status"] == "READY"
    assert "research" in state["task_states"]

    # 2. Verify state hash verification rejects tampered checkpoint
    ckpt_res = await db_session.execute(
        select(OrchestrationCheckpoint).where(OrchestrationCheckpoint.orchestration_id == orch.id)
    )
    ckpt = ckpt_res.scalars().first()
    assert ckpt is not None

    # Corrupt state hash
    ckpt.state_hash = "0" * 64
    await db_session.commit()

    with pytest.raises(ValueError, match="Corrupt checkpoint detected"):
        await engine.restore_checkpoint(db_session, orch.id)


@pytest.mark.asyncio
async def test_orchestration_api_crud_and_auth(async_client, db_session):
    user_a_token, user_a_id, _ = await create_authenticated_user(async_client, db_session, role=UserRole.CLIENT)
    user_b_token, user_b_id, _ = await create_authenticated_user(async_client, db_session, role=UserRole.CLIENT)
    dev_token, _, _ = await create_authenticated_user(async_client, db_session, role=UserRole.DEVELOPER)
    await setup_reference_agents(async_client, db_session, dev_token)

    # 1. Create orchestration
    create_res = await async_client.post(
        "/api/v1/orchestrations",
        json={"goal": "Summarize AgentChain architecture"},
        headers={"Authorization": f"Bearer {user_a_token}"},
    )
    assert create_res.status_code == 201
    orch_data = create_res.json()
    orch_id = orch_data["id"]
    assert orch_data["status"] == "READY"
    assert len(orch_data["tasks"]) == 3

    # 2. List user A orchestrations
    list_res = await async_client.post(
        "/api/v1/orchestrations",
        json={"goal": "Second goal for User A"},
        headers={"Authorization": f"Bearer {user_a_token}"},
    )
    assert list_res.status_code == 201

    get_list = await async_client.get(
        "/api/v1/orchestrations",
        headers={"Authorization": f"Bearer {user_a_token}"},
    )
    assert get_list.status_code == 200
    assert get_list.json()["total"] == 2

    # User B lists orchestrations -> 0
    get_list_b = await async_client.get(
        "/api/v1/orchestrations",
        headers={"Authorization": f"Bearer {user_b_token}"},
    )
    assert get_list_b.status_code == 200
    assert get_list_b.json()["total"] == 0

    # 3. User B cannot access User A's orchestration -> 403 Forbidden
    unauth_get = await async_client.get(
        f"/api/v1/orchestrations/{orch_id}",
        headers={"Authorization": f"Bearer {user_b_token}"},
    )
    assert unauth_get.status_code == 403

    # User A gets orchestration -> 200 OK
    auth_get = await async_client.get(
        f"/api/v1/orchestrations/{orch_id}",
        headers={"Authorization": f"Bearer {user_a_token}"},
    )
    assert auth_get.status_code == 200
    assert auth_get.json()["id"] == orch_id

    # 4. Get tasks
    tasks_res = await async_client.get(
        f"/api/v1/orchestrations/{orch_id}/tasks",
        headers={"Authorization": f"Bearer {user_a_token}"},
    )
    assert tasks_res.status_code == 200
    assert len(tasks_res.json()) == 3


@pytest.mark.asyncio
async def test_orchestration_hardened_sse_flow(async_client, db_session):
    client_token, user_id, _ = await create_authenticated_user(async_client, db_session, role=UserRole.CLIENT)
    dev_token, _, _ = await create_authenticated_user(async_client, db_session, role=UserRole.DEVELOPER)
    await setup_reference_agents(async_client, db_session, dev_token)

    create_res = await async_client.post(
        "/api/v1/orchestrations",
        json={"goal": "SSE Test Goal"},
        headers={"Authorization": f"Bearer {client_token}"},
    )
    orch_id = create_res.json()["id"]

    # 1. Request short-lived single-use SSE ticket
    ticket_res = await async_client.post(
        f"/api/v1/orchestrations/{orch_id}/sse-token",
        headers={"Authorization": f"Bearer {client_token}"},
    )
    assert ticket_res.status_code == 200
    ticket = ticket_res.json()["token"]
    assert ticket.startswith("sse_orch_")

    # Cancel orchestration so the SSE generator emits snapshot and terminates cleanly
    await async_client.post(
        f"/api/v1/orchestrations/{orch_id}/cancel",
        json={"reason": "Testing SSE"},
        headers={"Authorization": f"Bearer {client_token}"},
    )

    from httpx import ASGITransport, AsyncClient
    from app.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        # 2. Connect to SSE with valid ticket
        async with c.stream(
            "GET",
            f"/api/v1/orchestrations/{orch_id}/events?sse_token={ticket}",
        ) as response:
            assert response.status_code == 200
            assert "text/event-stream" in response.headers.get("content-type", "")

        # 3. Secondary connection with same ticket rejected (single-use enforced)
        bad_res = await c.get(
            f"/api/v1/orchestrations/{orch_id}/events?sse_token={ticket}"
        )
        assert bad_res.status_code == 401


@pytest.mark.asyncio
async def test_vertical_slice_end_to_end(async_client, db_session):
    """
    Vertical slice proving:
    Goal submitted -> DAG persisted -> Checkpoint created ->
    Research task -> AgentExecution -> Outbox -> Redis -> Worker -> Sandbox ->
    Task completion -> Analysis becomes READY -> Analysis executes ->
    Synthesis becomes READY -> Synthesis executes -> Final orchestration result!
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
        goal="Research AgentChain and produce a structured summary.",
    )
    assert orch.status == OrchestrationStatus.READY.value

    # Start orchestration
    orch = await engine.start_orchestration(db_session, orch.id, client_user_id)
    assert orch.status == OrchestrationStatus.RUNNING.value

    state = await engine.restore_checkpoint(db_session, orch.id)
    assert state is not None

    # Step 1: Schedule Research Task
    state = await engine.step(state)
    assert "research" in state["running_tasks"]
    res_exec_id = state["task_states"]["research"]["execution_id"]
    assert res_exec_id is not None

    # Verify Invariants 1, 2, 3:
    # 1. Child execution was created in PostgreSQL
    exec_res = await db_session.execute(
        select(AgentExecution).where(AgentExecution.id == uuid.UUID(res_exec_id))
    )
    child_exec = exec_res.scalar_one()
    assert child_exec.status == ExecutionStatus.QUEUED.value
    # 3. Outbox record exists for transactional dispatch
    outbox_res = await db_session.execute(
        select(ExecutionOutbox).where(ExecutionOutbox.execution_id == child_exec.id)
    )
    outbox_entry = outbox_res.scalar_one_or_none()
    assert outbox_entry is not None

    # Worker executes Research task via local sandbox
    claimed_1 = await worker.queue.claim_execution(worker.worker_id)
    target_1 = next((j for j in claimed_1 if j.execution_id == str(child_exec.id)), None)
    assert target_1 is not None
    assert await worker.process_job(target_1) is True

    # Step 2: Collect Research result -> Succeeded
    state = await engine.step(state)
    assert "research" in state["completed_tasks"]
    assert state["task_states"]["research"]["status"] == "SUCCEEDED"
    assert "summary" in state["task_states"]["research"]["output_data"]

    # Step 3: Analysis task scheduled (depends on research)
    state = await engine.step(state)
    assert "analysis" in state["running_tasks"]
    analysis_exec_id = state["task_states"]["analysis"]["execution_id"]

    # Worker executes Analysis task
    claimed_2 = await worker.queue.claim_execution(worker.worker_id)
    target_2 = next((j for j in claimed_2 if j.execution_id == str(analysis_exec_id)), None)
    assert target_2 is not None
    assert await worker.process_job(target_2) is True

    # Step 4: Collect Analysis result
    state = await engine.step(state)
    assert "analysis" in state["completed_tasks"]

    # Step 5: Synthesis task scheduled (depends on analysis)
    state = await engine.step(state)
    assert "synthesis" in state["running_tasks"]
    synthesis_exec_id = state["task_states"]["synthesis"]["execution_id"]

    # Worker executes Synthesis task
    claimed_3 = await worker.queue.claim_execution(worker.worker_id)
    target_3 = next((j for j in claimed_3 if j.execution_id == str(synthesis_exec_id)), None)
    assert target_3 is not None
    assert await worker.process_job(target_3) is True

    # Step 6: Collect Synthesis result and complete orchestration
    state = await engine.step(state)
    assert "synthesis" in state["completed_tasks"]
    assert state["status"] == OrchestrationStatus.SUCCEEDED.value
    assert state["final_result"] is not None

    # Verify Orchestration DB final state
    orch_id = orch.id
    from app.db.session import async_session_maker
    async with async_session_maker() as verify_session:
        db_orch = await engine.get_orchestration(verify_session, orch_id)
        assert db_orch is not None
        assert db_orch.status == OrchestrationStatus.SUCCEEDED.value
        assert db_orch.completed_at is not None
        assert db_orch.result is not None
        assert len(db_orch.artifacts) == 3

        # Invariant 10: Artifact hashes remain consistent
        for art in db_orch.artifacts:
            assert compute_canonical_hash(art.content_json) == art.sha256
