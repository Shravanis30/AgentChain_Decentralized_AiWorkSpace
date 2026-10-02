import pytest
from httpx import AsyncClient

from app.services.health import HealthService


@pytest.mark.asyncio
async def test_fastapi_startup_and_health_structure(async_client: AsyncClient):
    """
    Validates FastAPI app startup and the GET /api/v1/health endpoint response structure.
    Checks that dependencies dictionary has database, redis, qdrant, and object_storage keys.
    """
    response = await async_client.get("/api/v1/health")
    assert response.status_code == 200

    data = response.json()
    assert "status" in data
    assert data["status"] in ("ok", "degraded")
    assert "version" in data
    assert data["version"] == "0.1.0"

    deps = data.get("dependencies", {})
    assert "database" in deps
    assert "redis" in deps
    assert "qdrant" in deps
    assert "object_storage" in deps


@pytest.mark.asyncio
async def test_health_service_real_probes():
    """
    Executes real probe methods on HealthService to verify error handling and connectivity behavior.
    """
    # Redis probe
    redis_res = await HealthService.check_redis()
    assert isinstance(redis_res, str)
    assert redis_res in ("connected", "ping failed") or redis_res.startswith("error:")

    # Qdrant probe
    qdrant_res = await HealthService.check_qdrant()
    assert isinstance(qdrant_res, str)
    assert qdrant_res == "connected" or qdrant_res.startswith("error:") or "http status" in qdrant_res

    # Object storage probe
    s3_res = await HealthService.check_object_storage()
    assert isinstance(s3_res, str)
    assert s3_res == "connected" or s3_res.startswith("error:") or "http status" in s3_res
