import asyncio
import uuid
import pytest
from httpx import ASGITransport, AsyncClient

from app.agents.research_agent import RESEARCH_AGENT_MANIFEST
from app.main import app
from app.models.user import UserRole
from tests.test_agents import create_authenticated_user


@pytest.mark.asyncio
async def test_execution_idempotency_duplicate_request(async_client, db_session):
    dev_token, _, _ = await create_authenticated_user(async_client, db_session, role=UserRole.DEVELOPER)
    client_token, _, _ = await create_authenticated_user(async_client, db_session, role=UserRole.CLIENT)

    # 1. Create and publish agent
    manifest = RESEARCH_AGENT_MANIFEST.model_dump()
    slug = f"idempotent-agent-{uuid.uuid4().hex[:8]}"
    create_res = await async_client.post(
        "/api/v1/agents",
        json={"name": "Idempotent Agent", "slug": slug, "manifest": manifest},
        headers={"Authorization": f"Bearer {dev_token}"},
    )
    agent_id = create_res.json()["id"]
    await async_client.post(
        f"/api/v1/agents/{agent_id}/publish",
        headers={"Authorization": f"Bearer {dev_token}"},
    )

    # 2. First request with Idempotency-Key
    idempotency_key = f"key-{uuid.uuid4().hex}"
    payload = {"input": {"text": "AgentChain deterministic idempotency test."}}

    res1 = await async_client.post(
        f"/api/v1/agents/{agent_id}/execute",
        json=payload,
        headers={
            "Authorization": f"Bearer {client_token}",
            "Idempotency-Key": idempotency_key,
        },
    )
    assert res1.status_code == 200
    data1 = res1.json()
    exec_id_1 = data1["execution_id"]

    # 3. Duplicate request with same Idempotency-Key
    res2 = await async_client.post(
        f"/api/v1/agents/{agent_id}/execute",
        json=payload,
        headers={
            "Authorization": f"Bearer {client_token}",
            "Idempotency-Key": idempotency_key,
        },
    )
    assert res2.status_code == 200
    data2 = res2.json()
    exec_id_2 = data2["execution_id"]

    # Exactly the same execution record returned
    assert exec_id_1 == exec_id_2
    assert data1["input_hash"] == data2["input_hash"]


@pytest.mark.asyncio
async def test_execution_idempotency_concurrent_duplicate_requests(async_client, db_session):
    dev_token, _, _ = await create_authenticated_user(async_client, db_session, role=UserRole.DEVELOPER)
    client_token, _, _ = await create_authenticated_user(async_client, db_session, role=UserRole.CLIENT)

    manifest = RESEARCH_AGENT_MANIFEST.model_dump()
    slug = f"concurrent-agent-{uuid.uuid4().hex[:8]}"
    create_res = await async_client.post(
        "/api/v1/agents",
        json={"name": "Concurrent Agent", "slug": slug, "manifest": manifest},
        headers={"Authorization": f"Bearer {dev_token}"},
    )
    agent_id = create_res.json()["id"]
    await async_client.post(
        f"/api/v1/agents/{agent_id}/publish",
        headers={"Authorization": f"Bearer {dev_token}"},
    )

    idempotency_key = f"concurrent-key-{uuid.uuid4().hex}"
    payload = {"input": {"text": "Concurrent test text for idempotency."}}

    # Send 5 parallel requests concurrently with the same idempotency key
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        tasks = [
            c.post(
                f"/api/v1/agents/{agent_id}/execute",
                json=payload,
                headers={
                    "Authorization": f"Bearer {client_token}",
                    "Idempotency-Key": idempotency_key,
                },
            )
            for _ in range(5)
        ]
        responses = await asyncio.gather(*tasks)

    # All requests must succeed with HTTP 200
    for r in responses:
        assert r.status_code == 200

    # All must yield the exact same execution_id
    execution_ids = {r.json()["execution_id"] for r in responses}
    assert len(execution_ids) == 1


@pytest.mark.asyncio
async def test_execution_idempotency_different_users_same_key(async_client, db_session):
    dev_token, _, _ = await create_authenticated_user(async_client, db_session, role=UserRole.DEVELOPER)
    user1_token, _, _ = await create_authenticated_user(async_client, db_session, role=UserRole.CLIENT)
    user2_token, _, _ = await create_authenticated_user(async_client, db_session, role=UserRole.CLIENT)

    manifest = RESEARCH_AGENT_MANIFEST.model_dump()
    slug = f"multiuser-agent-{uuid.uuid4().hex[:8]}"
    create_res = await async_client.post(
        "/api/v1/agents",
        json={"name": "Multiuser Agent", "slug": slug, "manifest": manifest},
        headers={"Authorization": f"Bearer {dev_token}"},
    )
    agent_id = create_res.json()["id"]
    await async_client.post(
        f"/api/v1/agents/{agent_id}/publish",
        headers={"Authorization": f"Bearer {dev_token}"},
    )

    shared_key = f"shared-key-{uuid.uuid4().hex}"
    payload = {"input": {"text": "Multi-user test text."}}

    # User 1 submits with key
    res1 = await async_client.post(
        f"/api/v1/agents/{agent_id}/execute",
        json=payload,
        headers={
            "Authorization": f"Bearer {user1_token}",
            "Idempotency-Key": shared_key,
        },
    )
    assert res1.status_code == 200
    id1 = res1.json()["execution_id"]

    # User 2 submits with the same key -> Must create a separate execution
    res2 = await async_client.post(
        f"/api/v1/agents/{agent_id}/execute",
        json=payload,
        headers={
            "Authorization": f"Bearer {user2_token}",
            "Idempotency-Key": shared_key,
        },
    )
    assert res2.status_code == 200
    id2 = res2.json()["execution_id"]

    assert id1 != id2
