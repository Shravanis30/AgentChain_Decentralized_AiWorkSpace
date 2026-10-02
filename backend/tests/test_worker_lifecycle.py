import asyncio
import uuid
import pytest
from sqlalchemy import select

from agentchain_worker.retry import calculate_backoff_delay, is_transient_error
from agentchain_worker.sandbox.base import SandboxConfig, SandboxResult, SandboxRunner
from agentchain_worker.sandbox.local_runner import LocalProcessSandboxRunner
from agentchain_worker.worker import AgentExecutionWorker
from app.agents.research_agent import RESEARCH_AGENT_MANIFEST
from app.models.agent import AgentExecution, ExecutionStatus
from app.models.user import UserRole
from app.services.hashing import compute_canonical_hash
from app.services.queue import QueueJob
from tests.test_agents import create_authenticated_user


class MockFailureSandboxRunner(SandboxRunner):
    def __init__(self, failure_type: str = "transient"):
        self.failure_type = failure_type

    async def run_agent(self, agent_manifest, input_data, context, config):
        if self.failure_type == "transient":
            return SandboxResult(
                success=False,
                output=None,
                error_code="CONTAINER_START_FAILURE",
                error_message="Simulated docker container startup failure",
            )
        elif self.failure_type == "permanent":
            return SandboxResult(
                success=False,
                output=None,
                error_code="INPUT_VALIDATION_ERROR",
                error_message="Simulated permanent schema failure",
            )
        elif self.failure_type == "timeout":
            return SandboxResult(
                success=False,
                output=None,
                error_code="EXECUTION_TIMEOUT",
                error_message="Simulated execution timeout",
                timed_out=True,
            )
        elif self.failure_type == "malformed_output":
            return SandboxResult(
                success=False,
                output=None,
                error_code="MALFORMED_SANDBOX_OUTPUT",
                error_message="Simulated broken output JSON",
            )
        return SandboxResult(success=True, output={"status": "ok"})


@pytest.mark.asyncio
async def test_worker_successful_execution_and_hashing(async_client, db_session):
    dev_token, _, _ = await create_authenticated_user(async_client, db_session, role=UserRole.DEVELOPER)
    client_token, client_user_id, _ = await create_authenticated_user(async_client, db_session, role=UserRole.CLIENT)

    manifest = RESEARCH_AGENT_MANIFEST.model_dump()
    slug = f"worker-test-{uuid.uuid4().hex[:8]}"
    create_res = await async_client.post(
        "/api/v1/agents",
        json={"name": "Worker Execution Agent", "slug": slug, "manifest": manifest},
        headers={"Authorization": f"Bearer {dev_token}"},
    )
    agent_id = create_res.json()["id"]
    await async_client.post(f"/api/v1/agents/{agent_id}/publish", headers={"Authorization": f"Bearer {dev_token}"})

    input_payload = {"text": "AgentChain worker isolation test. Verifying hash and status persistence."}
    exec_res = await async_client.post(
        f"/api/v1/agents/{agent_id}/execute",
        json={"input": input_payload},
        headers={"Authorization": f"Bearer {client_token}"},
    )
    assert exec_res.status_code == 200
    execution_id = exec_res.json()["execution_id"]

    # Process with worker using LocalProcessSandboxRunner
    worker = AgentExecutionWorker(sandbox_runner=LocalProcessSandboxRunner())
    claimed = await worker.queue.claim_execution(worker.worker_id)
    target = next((j for j in claimed if j.execution_id == execution_id), None)
    assert target is not None

    success = await worker.process_job(target)
    assert success is True

    # Verify execution in DB
    get_res = await async_client.get(
        f"/api/v1/executions/{execution_id}",
        headers={"Authorization": f"Bearer {client_token}"},
    )
    data = get_res.json()
    assert data["status"] == "SUCCEEDED"
    assert data["output_data"] is not None
    assert data["output_hash"] == compute_canonical_hash(data["output_data"])
    assert data["worker_id"] == worker.worker_id
    assert data["attempt_count"] == 1


@pytest.mark.asyncio
async def test_worker_transient_failure_and_retry(async_client, db_session):
    dev_token, _, _ = await create_authenticated_user(async_client, db_session, role=UserRole.DEVELOPER)
    client_token, _, _ = await create_authenticated_user(async_client, db_session, role=UserRole.CLIENT)

    manifest = RESEARCH_AGENT_MANIFEST.model_dump()
    slug = f"retry-test-{uuid.uuid4().hex[:8]}"
    create_res = await async_client.post(
        "/api/v1/agents",
        json={"name": "Retry Agent", "slug": slug, "manifest": manifest},
        headers={"Authorization": f"Bearer {dev_token}"},
    )
    agent_id = create_res.json()["id"]
    await async_client.post(f"/api/v1/agents/{agent_id}/publish", headers={"Authorization": f"Bearer {dev_token}"})

    exec_res = await async_client.post(
        f"/api/v1/agents/{agent_id}/execute",
        json={"input": {"text": "Transient retry testing."}},
        headers={"Authorization": f"Bearer {client_token}"},
    )
    execution_id = exec_res.json()["execution_id"]

    # Worker configured with Mock failure runner
    worker = AgentExecutionWorker(sandbox_runner=MockFailureSandboxRunner(failure_type="transient"))
    claimed = await worker.queue.claim_execution(worker.worker_id)
    target = next(j for j in claimed if j.execution_id == execution_id)

    # Process job -> fails with transient error -> requeued
    res = await worker.process_job(target)
    assert res is True  # handled and requeued

    # Verify state machine returned execution to QUEUED for retry
    get_res = await async_client.get(
        f"/api/v1/executions/{execution_id}",
        headers={"Authorization": f"Bearer {client_token}"},
    )
    assert get_res.json()["status"] == "QUEUED"
    assert get_res.json()["attempt_count"] == 1


@pytest.mark.asyncio
async def test_worker_permanent_failure_no_retry(async_client, db_session):
    dev_token, _, _ = await create_authenticated_user(async_client, db_session, role=UserRole.DEVELOPER)
    client_token, _, _ = await create_authenticated_user(async_client, db_session, role=UserRole.CLIENT)

    manifest = RESEARCH_AGENT_MANIFEST.model_dump()
    slug = f"permanent-test-{uuid.uuid4().hex[:8]}"
    create_res = await async_client.post(
        "/api/v1/agents",
        json={"name": "Permanent Failure Agent", "slug": slug, "manifest": manifest},
        headers={"Authorization": f"Bearer {dev_token}"},
    )
    agent_id = create_res.json()["id"]
    await async_client.post(f"/api/v1/agents/{agent_id}/publish", headers={"Authorization": f"Bearer {dev_token}"})

    exec_res = await async_client.post(
        f"/api/v1/agents/{agent_id}/execute",
        json={"input": {"text": "Permanent failure test."}},
        headers={"Authorization": f"Bearer {client_token}"},
    )
    execution_id = exec_res.json()["execution_id"]

    worker = AgentExecutionWorker(sandbox_runner=MockFailureSandboxRunner(failure_type="permanent"))
    claimed = await worker.queue.claim_execution(worker.worker_id)
    target = next(j for j in claimed if j.execution_id == execution_id)

    # Process job -> permanent failure -> marks FAILED directly without retry
    res = await worker.process_job(target)
    assert res is False

    get_res = await async_client.get(
        f"/api/v1/executions/{execution_id}",
        headers={"Authorization": f"Bearer {client_token}"},
    )
    assert get_res.json()["status"] == "FAILED"
    assert get_res.json()["error_code"] == "INPUT_VALIDATION_ERROR"


@pytest.mark.asyncio
async def test_worker_timeout_enforcement(async_client, db_session):
    dev_token, _, _ = await create_authenticated_user(async_client, db_session, role=UserRole.DEVELOPER)
    client_token, _, _ = await create_authenticated_user(async_client, db_session, role=UserRole.CLIENT)

    manifest = RESEARCH_AGENT_MANIFEST.model_dump()
    slug = f"timeout-test-{uuid.uuid4().hex[:8]}"
    create_res = await async_client.post(
        "/api/v1/agents",
        json={"name": "Timeout Agent", "slug": slug, "manifest": manifest},
        headers={"Authorization": f"Bearer {dev_token}"},
    )
    agent_id = create_res.json()["id"]
    await async_client.post(f"/api/v1/agents/{agent_id}/publish", headers={"Authorization": f"Bearer {dev_token}"})

    exec_res = await async_client.post(
        f"/api/v1/agents/{agent_id}/execute",
        json={"input": {"text": "Timeout enforcement test."}},
        headers={"Authorization": f"Bearer {client_token}"},
    )
    execution_id = exec_res.json()["execution_id"]

    worker = AgentExecutionWorker(sandbox_runner=MockFailureSandboxRunner(failure_type="timeout"))
    claimed = await worker.queue.claim_execution(worker.worker_id)
    target = next(j for j in claimed if j.execution_id == execution_id)

    res = await worker.process_job(target)
    assert res is True

    get_res = await async_client.get(
        f"/api/v1/executions/{execution_id}",
        headers={"Authorization": f"Bearer {client_token}"},
    )
    assert get_res.json()["status"] == "TIMED_OUT"
    assert get_res.json()["error_code"] == "EXECUTION_TIMEOUT"


def test_retry_policy_classification_logic():
    # Transient
    assert is_transient_error("WORKER_LOST") is True
    assert is_transient_error("CONTAINER_START_FAILURE") is True
    assert is_transient_error("TEMPORARY_QUEUE_FAILURE") is True

    # Permanent
    assert is_transient_error("INPUT_VALIDATION_ERROR") is False
    assert is_transient_error("OUTPUT_VALIDATION_ERROR") is False
    assert is_transient_error("EXECUTION_TIMEOUT") is False
    assert is_transient_error("POLICY_VIOLATION") is False

    # Backoff calculation
    assert calculate_backoff_delay(0) == 1.0
    assert calculate_backoff_delay(1) == 2.0
    assert calculate_backoff_delay(2) == 4.0
    assert calculate_backoff_delay(10) == 60.0  # capped at max
