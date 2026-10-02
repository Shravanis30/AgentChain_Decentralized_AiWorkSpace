import json
import uuid
import pytest

from app.models.base import utc_now
from app.models.user import UserRole
from app.services.rate_limiter import get_redis_client
from tests.test_agents import create_authenticated_user


@pytest.mark.asyncio
async def test_operator_workers_authorization_and_heartbeats(async_client, db_session):
    client_token, _, _ = await create_authenticated_user(async_client, db_session, role=UserRole.CLIENT)
    admin_token, _, _ = await create_authenticated_user(async_client, db_session, role=UserRole.ADMIN)

    # 1. Normal user (CLIENT) denied -> 403 Forbidden
    forbidden_res = await async_client.get(
        "/api/v1/operator/workers",
        headers={"Authorization": f"Bearer {client_token}"},
    )
    assert forbidden_res.status_code == 403

    # 2. Seed a mock worker heartbeat in Redis
    redis = get_redis_client()
    worker_id = f"worker-test-{uuid.uuid4().hex[:6]}"
    hb_data = {
        "worker_id": worker_id,
        "timestamp": utc_now().isoformat(),
        "status": "BUSY",
        "active_execution_count": 2,
        "version": "0.1.0",
        "hostname": "test-host",
    }
    await redis.set(f"agentchain:workers:{worker_id}", json.dumps(hb_data), ex=30)

    # 3. Admin user -> 200 OK
    admin_res = await async_client.get(
        "/api/v1/operator/workers",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert admin_res.status_code == 200
    data = admin_res.json()
    assert "workers" in data
    assert "total" in data

    matched = next((w for w in data["workers"] if w["worker_id"] == worker_id), None)
    assert matched is not None
    assert matched["status"] == "BUSY"
    assert matched["active_execution_count"] == 2
    assert matched["is_alive"] is True
    assert matched["heartbeat_age_seconds"] >= 0.0

    # Clean up
    await redis.delete(f"agentchain:workers:{worker_id}")
