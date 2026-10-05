import httpx
from redis.asyncio import from_url as redis_from_url
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.schemas.health import DependencyHealth, HealthResponse


class HealthService:
    @staticmethod
    async def check_database(db: AsyncSession) -> str:
        try:
            result = await db.execute(text("SELECT 1"))
            val = result.scalar()
            if val == 1:
                return "connected"
            return "unexpected response"
        except Exception as e:  # noqa: BLE001
            return f"error: {e!s}"

    @staticmethod
    async def check_redis() -> str:
        try:
            client = redis_from_url(
                settings.REDIS_URL,
                socket_connect_timeout=2.0,
                socket_timeout=2.0,
            )
            async with client:
                pong = await client.ping()
                if pong:
                    return "connected"
                return "ping failed"
        except Exception as e:  # noqa: BLE001
            return f"error: {e!s}"

    @staticmethod
    async def check_qdrant() -> str:
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                url = f"{settings.QDRANT_URL.rstrip('/')}/readyz"
                headers = {}
                if settings.QDRANT_API_KEY:
                    headers["api-key"] = settings.QDRANT_API_KEY
                response = await client.get(url, headers=headers)
                if response.status_code == 200:
                    return "connected"
                # Fallback to root if readyz isn't present
                root_res = await client.get(settings.QDRANT_URL, headers=headers)
                if root_res.status_code == 200:
                    return "connected"
                return f"http status {response.status_code}"
        except Exception as e:  # noqa: BLE001
            return f"error: {e!s}"

    @staticmethod
    async def check_object_storage() -> str:
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                # MinIO health endpoint
                url = f"{settings.S3_ENDPOINT_URL.rstrip('/')}/minio/health/live"
                response = await client.get(url)
                if response.status_code in (200, 204):
                    return "connected"
                # Generic S3 endpoint probe
                res_root = await client.get(settings.S3_ENDPOINT_URL)
                if res_root.status_code in (200, 403, 400):
                    return "connected"
                return f"http status {response.status_code}"
        except Exception as e:  # noqa: BLE001
            return f"error: {e!s}"

    @classmethod
    async def get_health_status(cls, db: AsyncSession) -> HealthResponse:
        db_status = await cls.check_database(db)
        redis_status = await cls.check_redis()
        qdrant_status = await cls.check_qdrant()
        s3_status = await cls.check_object_storage()

        deps = DependencyHealth(
            database=db_status,
            redis=redis_status,
            qdrant=qdrant_status,
            object_storage=s3_status,
        )

        all_ok = all(
            val == "connected"
            for val in [db_status, redis_status, qdrant_status, s3_status]
        )

        return HealthResponse(
            status="ok" if all_ok else "degraded",
            version=settings.APP_VERSION,
            dependencies=deps,
        )
