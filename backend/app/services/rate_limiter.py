import logging
import time
from fastapi import HTTPException, Request, status
from redis.asyncio import Redis, from_url as redis_from_url

from app.core.config import settings

logger = logging.getLogger(__name__)

_redis_client: Redis | None = None


def get_redis_client() -> Redis:
    global _redis_client
    if _redis_client is None:
        _redis_client = redis_from_url(
            settings.REDIS_URL,
            encoding="utf-8",
            decode_responses=True,
            socket_connect_timeout=5.0,
            socket_timeout=5.0,
        )
    return _redis_client


class RateLimiter:
    @staticmethod
    async def is_rate_limited(
        key: str,
        limit: int,
        window_seconds: int = 60,
    ) -> tuple[bool, int, int]:
        """
        Check rate limit using Redis.
        Returns: (is_limited, current_count, retry_after_seconds)
        """
        try:
            client = get_redis_client()
            current_time = int(time.time())
            window_key = f"rl:{key}:{current_time // window_seconds}"

            pipe = client.pipeline()
            pipe.incr(window_key)
            pipe.expire(window_key, window_seconds + 5)
            results = await pipe.execute()

            current_count = int(results[0])
            ttl = await client.ttl(window_key)
            retry_after = ttl if ttl > 0 else window_seconds

            if current_count > limit:
                return True, current_count, retry_after

            return False, current_count, 0
        except Exception as e:
            logger.warning(f"Rate limiting check failed (Redis error): {e}. Failing open.")
            return False, 0, 0

    @classmethod
    async def enforce(
        cls,
        request: Request,
        key_prefix: str,
        limit: int,
        window_seconds: int = 60,
    ) -> None:
        """
        Enforce rate limit for an incoming request.
        Raises HTTP 429 if the limit is exceeded.
        """
        forwarded = request.headers.get("x-forwarded-for")
        client_ip = forwarded.split(",")[0].strip() if forwarded else (request.client.host if request.client else "unknown")
        key = f"{key_prefix}:{client_ip}"

        is_limited, current_count, retry_after = await cls.is_rate_limited(
            key=key,
            limit=limit,
            window_seconds=window_seconds,
        )

        if is_limited:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Rate limit exceeded. Please try again later.",
                headers={"Retry-After": str(retry_after)},
            )
