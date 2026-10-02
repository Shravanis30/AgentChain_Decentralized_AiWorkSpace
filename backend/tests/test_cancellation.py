import uuid
import pytest

from app.agents.research_agent import RESEARCH_AGENT_MANIFEST
from app.models.agent import ExecutionStatus
from app.models.user import UserRole
from tests.test_agents import create_authenticated_user


@pytest.mark.asyncio
async def test_cancellation_authorization_and_queued_cancel(async_client, db_session):
    dev_token, _, _ = await create_authenticated_user(async_client, db_session, role=UserRole.DEVELOPER)
    client_token, _, _ = await create_authenticated_user(async_client, db_session, role=UserRole.CLIENT)
    intruder_token, _, _ = await create_authenticated_user(async_client, db_session, role=UserRole.CLIENT)

    manifest = RESEARCH_AGENT_MANIFEST.model_dump()
    slug = f"cancel-agent-{uuid.uuid4().hex[:8]}"
    create_res = await async_client.post(
        "/api/v1/agents",
        json={"name": "Cancel Test Agent", "slug": slug, "manifest": manifest},
        headers={"Authorization": f"Bearer {dev_token}"},
    )
    agent_id = create_res.json()["id"]
    await async_client.post(
        f"/api/v1/agents/{agent_id}/publish",
        headers={"Authorization": f"Bearer {dev_token}"},
    )

    # Submit execution -> QUEUED
    exec_res = await async_client.post(
        f"/api/v1/agents/{agent_id}/execute",
        json={"input": {"text": "Cancellation test text."}},
        headers={"Authorization": f"Bearer {client_token}"},
    )
    execution_id = exec_res.json()["execution_id"]
    assert exec_res.json()["status"] == "QUEUED"

    # 1. Intruder cannot cancel -> 403 Forbidden
    intruder_cancel = await async_client.post(
        f"/api/v1/executions/{execution_id}/cancel",
        headers={"Authorization": f"Bearer {intruder_token}"},
    )
    assert intruder_cancel.status_code == 403

    # 2. Requester can cancel -> 200 OK
    cancel_res = await async_client.post(
        f"/api/v1/executions/{execution_id}/cancel",
        json={"reason": "User changed mind"},
        headers={"Authorization": f"Bearer {client_token}"},
    )
    assert cancel_res.status_code == 200
    cancel_data = cancel_res.json()
    assert cancel_data["status"] == "CANCELLED"
    assert cancel_data["cancellation_requested"] is True
    assert cancel_data["cancelled_at"] is not None

    # 3. Repeat cancellation on already completed/cancelled execution -> 400 Bad Request
    repeat_cancel = await async_client.post(
        f"/api/v1/executions/{execution_id}/cancel",
        headers={"Authorization": f"Bearer {client_token}"},
    )
    assert repeat_cancel.status_code == 400
    assert "already" in repeat_cancel.json()["detail"].lower()


@pytest.mark.asyncio
async def test_cancellation_during_running_worker(async_client, db_session):
    dev_token, _, _ = await create_authenticated_user(async_client, db_session, role=UserRole.DEVELOPER)
    client_token, _, _ = await create_authenticated_user(async_client, db_session, role=UserRole.CLIENT)

    manifest = RESEARCH_AGENT_MANIFEST.model_dump()
    slug = f"cancel-running-{uuid.uuid4().hex[:8]}"
    create_res = await async_client.post(
        "/api/v1/agents",
        json={"name": "Cancel Running Agent", "slug": slug, "manifest": manifest},
        headers={"Authorization": f"Bearer {dev_token}"},
    )
    agent_id = create_res.json()["id"]
    await async_client.post(
        f"/api/v1/agents/{agent_id}/publish",
        headers={"Authorization": f"Bearer {dev_token}"},
    )

    # Submit execution
    exec_res = await async_client.post(
        f"/api/v1/agents/{agent_id}/execute",
        json={"input": {"text": "Cancellation while running test."}},
        headers={"Authorization": f"Bearer {client_token}"},
    )
    execution_id = exec_res.json()["execution_id"]

    # Cancel execution
    cancel_res = await async_client.post(
        f"/api/v1/executions/{execution_id}/cancel",
        json={"reason": "Aborting long running task"},
        headers={"Authorization": f"Bearer {client_token}"},
    )
    assert cancel_res.status_code == 200
    assert cancel_res.json()["status"] == "CANCELLED"

    # Worker picks up job: should detect cancellation and finalize
    from agentchain_worker.sandbox.local_runner import LocalProcessSandboxRunner
    from agentchain_worker.worker import AgentExecutionWorker

    worker = AgentExecutionWorker(sandbox_runner=LocalProcessSandboxRunner())
    claimed = await worker.queue.claim_execution(worker.worker_id)
    target_job = next((j for j in claimed if j.execution_id == execution_id), None)
    if target_job:
        success = await worker.process_job(target_job)
        assert success is True

    # Retrieve execution: must be CANCELLED
    get_res = await async_client.get(
        f"/api/v1/executions/{execution_id}",
        headers={"Authorization": f"Bearer {client_token}"},
    )
    assert get_res.json()["status"] == "CANCELLED"
