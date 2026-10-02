import json
import uuid
import pytest
from httpx import ASGITransport, AsyncClient

from app.agents.research_agent import RESEARCH_AGENT_MANIFEST
from app.core.config import settings
from app.main import app
from app.models.user import UserRole
from app.services.rate_limiter import get_redis_client
from app.services.sse_ticket import SSETicketService
from tests.test_agents import create_authenticated_user


@pytest.mark.asyncio
async def test_sse_normal_session_authentication(async_client, db_session):
    """
    Proves normal session authentication via:
    1. Authorization Bearer header
    2. Secure session cookie (settings.SESSION_COOKIE_NAME)
    """
    dev_token, _, _ = await create_authenticated_user(async_client, db_session, role=UserRole.DEVELOPER)
    client_token, _, _ = await create_authenticated_user(async_client, db_session, role=UserRole.CLIENT)

    manifest = RESEARCH_AGENT_MANIFEST.model_dump()
    slug = f"sse-agent-{uuid.uuid4().hex[:8]}"
    create_res = await async_client.post(
        "/api/v1/agents",
        json={"name": "SSE Agent", "slug": slug, "manifest": manifest},
        headers={"Authorization": f"Bearer {dev_token}"},
    )
    agent_id = create_res.json()["id"]
    await async_client.post(
        f"/api/v1/agents/{agent_id}/publish",
        headers={"Authorization": f"Bearer {dev_token}"},
    )

    exec_res = await async_client.post(
        f"/api/v1/agents/{agent_id}/execute",
        json={"input": {"text": "SSE testing text."}},
        headers={"Authorization": f"Bearer {client_token}"},
    )
    execution_id = exec_res.json()["execution_id"]

    # Terminate so stream returns after snapshot
    await async_client.post(
        f"/api/v1/executions/{execution_id}/cancel",
        json={"reason": "Testing SSE"},
        headers={"Authorization": f"Bearer {client_token}"},
    )

    transport = ASGITransport(app=app)

    # 1. Bearer header authentication
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        async with c.stream(
            "GET",
            f"/api/v1/executions/{execution_id}/events",
            headers={"Authorization": f"Bearer {client_token}"},
        ) as response:
            assert response.status_code == 200
            assert "text/event-stream" in response.headers.get("content-type", "")

    # 2. Session cookie authentication
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        async with c.stream(
            "GET",
            f"/api/v1/executions/{execution_id}/events",
            cookies={settings.SESSION_COOKIE_NAME: client_token},
        ) as response:
            assert response.status_code == 200
            assert "text/event-stream" in response.headers.get("content-type", "")


@pytest.mark.asyncio
async def test_sse_unauthorized_execution_access(async_client, db_session):
    """
    Proves unauthorized access is rejected:
    1. Unauthenticated request -> 401
    2. Intruder user session -> 403 Forbidden
    """
    dev_token, _, _ = await create_authenticated_user(async_client, db_session, role=UserRole.DEVELOPER)
    client_token, _, _ = await create_authenticated_user(async_client, db_session, role=UserRole.CLIENT)
    intruder_token, _, _ = await create_authenticated_user(async_client, db_session, role=UserRole.CLIENT)

    manifest = RESEARCH_AGENT_MANIFEST.model_dump()
    slug = f"sse-agent-{uuid.uuid4().hex[:8]}"
    create_res = await async_client.post(
        "/api/v1/agents",
        json={"name": "SSE Agent", "slug": slug, "manifest": manifest},
        headers={"Authorization": f"Bearer {dev_token}"},
    )
    agent_id = create_res.json()["id"]
    await async_client.post(
        f"/api/v1/agents/{agent_id}/publish",
        headers={"Authorization": f"Bearer {dev_token}"},
    )

    exec_res = await async_client.post(
        f"/api/v1/agents/{agent_id}/execute",
        json={"input": {"text": "SSE testing text."}},
        headers={"Authorization": f"Bearer {client_token}"},
    )
    execution_id = exec_res.json()["execution_id"]

    # 1. Unauthenticated request -> 401
    unauth_res = await async_client.get(f"/api/v1/executions/{execution_id}/events")
    assert unauth_res.status_code == 401

    # 2. Intruder request -> 403
    intruder_res = await async_client.get(
        f"/api/v1/executions/{execution_id}/events",
        headers={"Authorization": f"Bearer {intruder_token}"},
    )
    assert intruder_res.status_code == 403


@pytest.mark.asyncio
async def test_sse_session_token_rejected_in_query_param(async_client, db_session):
    """
    Proves long-lived session tokens in URL query strings (?token= or ?sse_token=) are rejected.
    """
    dev_token, _, _ = await create_authenticated_user(async_client, db_session, role=UserRole.DEVELOPER)
    client_token, _, _ = await create_authenticated_user(async_client, db_session, role=UserRole.CLIENT)

    manifest = RESEARCH_AGENT_MANIFEST.model_dump()
    slug = f"sse-agent-{uuid.uuid4().hex[:8]}"
    create_res = await async_client.post(
        "/api/v1/agents",
        json={"name": "SSE Agent", "slug": slug, "manifest": manifest},
        headers={"Authorization": f"Bearer {dev_token}"},
    )
    agent_id = create_res.json()["id"]
    await async_client.post(
        f"/api/v1/agents/{agent_id}/publish",
        headers={"Authorization": f"Bearer {dev_token}"},
    )

    exec_res = await async_client.post(
        f"/api/v1/agents/{agent_id}/execute",
        json={"input": {"text": "SSE testing text."}},
        headers={"Authorization": f"Bearer {client_token}"},
    )
    execution_id = exec_res.json()["execution_id"]

    # ?token=<session_token> -> 401 (URL query parameter token rejected)
    res_query = await async_client.get(f"/api/v1/executions/{execution_id}/events?token={client_token}")
    assert res_query.status_code == 401

    # ?sse_token=<session_token> -> 401 (normal session tokens cannot be passed as sse_token)
    res_fake_sse = await async_client.get(f"/api/v1/executions/{execution_id}/events?sse_token={client_token}")
    assert res_fake_sse.status_code == 401


@pytest.mark.asyncio
async def test_sse_execution_scoped_ticket_flow(async_client, db_session):
    """
    Proves short-lived execution-scoped SSE ticket issuance and consumption:
    1. POST /api/v1/executions/{id}/sse-token -> returns sse_token
    2. GET /api/v1/executions/{id}/events?sse_token={ticket} -> 200 stream
    """
    dev_token, _, _ = await create_authenticated_user(async_client, db_session, role=UserRole.DEVELOPER)
    client_token, _, _ = await create_authenticated_user(async_client, db_session, role=UserRole.CLIENT)

    manifest = RESEARCH_AGENT_MANIFEST.model_dump()
    slug = f"sse-agent-{uuid.uuid4().hex[:8]}"
    create_res = await async_client.post(
        "/api/v1/agents",
        json={"name": "SSE Agent", "slug": slug, "manifest": manifest},
        headers={"Authorization": f"Bearer {dev_token}"},
    )
    agent_id = create_res.json()["id"]
    await async_client.post(
        f"/api/v1/agents/{agent_id}/publish",
        headers={"Authorization": f"Bearer {dev_token}"},
    )

    exec_res = await async_client.post(
        f"/api/v1/agents/{agent_id}/execute",
        json={"input": {"text": "SSE testing text."}},
        headers={"Authorization": f"Bearer {client_token}"},
    )
    execution_id = exec_res.json()["execution_id"]

    # Terminate so stream returns
    await async_client.post(
        f"/api/v1/executions/{execution_id}/cancel",
        json={"reason": "Testing SSE"},
        headers={"Authorization": f"Bearer {client_token}"},
    )

    # Issue execution-scoped SSE ticket
    ticket_res = await async_client.post(
        f"/api/v1/executions/{execution_id}/sse-token",
        headers={"Authorization": f"Bearer {client_token}"},
    )
    assert ticket_res.status_code == 200
    ticket_data = ticket_res.json()
    assert "sse_token" in ticket_data
    assert ticket_data["sse_token"].startswith("sse_")
    sse_token = ticket_data["sse_token"]

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        async with c.stream(
            "GET",
            f"/api/v1/executions/{execution_id}/events?sse_token={sse_token}",
        ) as response:
            assert response.status_code == 200
            assert "text/event-stream" in response.headers.get("content-type", "")


@pytest.mark.asyncio
async def test_sse_expired_token(async_client, db_session):
    """
    Proves an expired SSE ticket is rejected with 401.
    """
    dev_token, _, _ = await create_authenticated_user(async_client, db_session, role=UserRole.DEVELOPER)
    client_token, _, _ = await create_authenticated_user(async_client, db_session, role=UserRole.CLIENT)

    manifest = RESEARCH_AGENT_MANIFEST.model_dump()
    slug = f"sse-agent-{uuid.uuid4().hex[:8]}"
    create_res = await async_client.post(
        "/api/v1/agents",
        json={"name": "SSE Agent", "slug": slug, "manifest": manifest},
        headers={"Authorization": f"Bearer {dev_token}"},
    )
    agent_id = create_res.json()["id"]
    await async_client.post(
        f"/api/v1/agents/{agent_id}/publish",
        headers={"Authorization": f"Bearer {dev_token}"},
    )

    exec_res = await async_client.post(
        f"/api/v1/agents/{agent_id}/execute",
        json={"input": {"text": "SSE testing text."}},
        headers={"Authorization": f"Bearer {client_token}"},
    )
    execution_id = exec_res.json()["execution_id"]

    ticket_res = await async_client.post(
        f"/api/v1/executions/{execution_id}/sse-token",
        headers={"Authorization": f"Bearer {client_token}"},
    )
    sse_token = ticket_res.json()["sse_token"]

    # Manually expire ticket in Redis
    redis = get_redis_client()
    await redis.delete(f"agentchain:sse_ticket:{sse_token}")

    # Connecting with expired ticket -> 401
    res = await async_client.get(f"/api/v1/executions/{execution_id}/events?sse_token={sse_token}")
    assert res.status_code == 401


@pytest.mark.asyncio
async def test_sse_execution_scoped_token_cannot_access_another_execution(async_client, db_session):
    """
    Proves an SSE ticket scoped to Execution A CANNOT access Execution B.
    """
    dev_token, _, _ = await create_authenticated_user(async_client, db_session, role=UserRole.DEVELOPER)
    client_token, _, _ = await create_authenticated_user(async_client, db_session, role=UserRole.CLIENT)

    manifest = RESEARCH_AGENT_MANIFEST.model_dump()
    slug = f"sse-agent-{uuid.uuid4().hex[:8]}"
    create_res = await async_client.post(
        "/api/v1/agents",
        json={"name": "SSE Agent", "slug": slug, "manifest": manifest},
        headers={"Authorization": f"Bearer {dev_token}"},
    )
    agent_id = create_res.json()["id"]
    await async_client.post(
        f"/api/v1/agents/{agent_id}/publish",
        headers={"Authorization": f"Bearer {dev_token}"},
    )

    # Create Execution A
    res_a = await async_client.post(
        f"/api/v1/agents/{agent_id}/execute",
        json={"input": {"text": "Execution A text."}},
        headers={"Authorization": f"Bearer {client_token}"},
    )
    exec_a_id = res_a.json()["execution_id"]

    # Create Execution B
    res_b = await async_client.post(
        f"/api/v1/agents/{agent_id}/execute",
        json={"input": {"text": "Execution B text."}},
        headers={"Authorization": f"Bearer {client_token}"},
    )
    exec_b_id = res_b.json()["execution_id"]

    # Issue ticket for Execution A
    ticket_res = await async_client.post(
        f"/api/v1/executions/{exec_a_id}/sse-token",
        headers={"Authorization": f"Bearer {client_token}"},
    )
    ticket_a = ticket_res.json()["sse_token"]

    # Attempt to use ticket_a to subscribe to Execution B -> 401 / 403
    res_cross = await async_client.get(f"/api/v1/executions/{exec_b_id}/events?sse_token={ticket_a}")
    assert res_cross.status_code in {401, 403}


@pytest.mark.asyncio
async def test_sse_token_cannot_authenticate_normal_api_endpoints(async_client, db_session):
    """
    Proves an SSE ticket cannot be used to authenticate standard REST API endpoints.
    """
    dev_token, _, _ = await create_authenticated_user(async_client, db_session, role=UserRole.DEVELOPER)
    client_token, _, _ = await create_authenticated_user(async_client, db_session, role=UserRole.CLIENT)

    manifest = RESEARCH_AGENT_MANIFEST.model_dump()
    slug = f"sse-agent-{uuid.uuid4().hex[:8]}"
    create_res = await async_client.post(
        "/api/v1/agents",
        json={"name": "SSE Agent", "slug": slug, "manifest": manifest},
        headers={"Authorization": f"Bearer {dev_token}"},
    )
    agent_id = create_res.json()["id"]
    await async_client.post(
        f"/api/v1/agents/{agent_id}/publish",
        headers={"Authorization": f"Bearer {dev_token}"},
    )

    exec_res = await async_client.post(
        f"/api/v1/agents/{agent_id}/execute",
        json={"input": {"text": "SSE testing text."}},
        headers={"Authorization": f"Bearer {client_token}"},
    )
    execution_id = exec_res.json()["execution_id"]

    ticket_res = await async_client.post(
        f"/api/v1/executions/{execution_id}/sse-token",
        headers={"Authorization": f"Bearer {client_token}"},
    )
    sse_token = ticket_res.json()["sse_token"]

    # 1. Attempt to call GET /api/v1/auth/me with SSE ticket -> 401
    me_res = await async_client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {sse_token}"},
    )
    assert me_res.status_code == 401

    # 2. Attempt to call GET /api/v1/executions/{id} with SSE ticket -> 401
    exec_detail = await async_client.get(
        f"/api/v1/executions/{execution_id}",
        headers={"Authorization": f"Bearer {sse_token}"},
    )
    assert exec_detail.status_code == 401
