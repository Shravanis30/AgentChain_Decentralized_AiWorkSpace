import json
import logging
import secrets
import uuid

from app.services.rate_limiter import get_redis_client

logger = logging.getLogger("agentchain.sse_ticket")

SSE_TICKET_TTL_SECONDS = 60  # Short-lived ticket valid for 60 seconds


class SSETicketService:
    """
    Short-lived, single-use, execution-scoped SSE ticket manager.
    Avoids exposing long-lived session credentials in query URLs.
    """

    @classmethod
    async def create_ticket(cls, user_id: uuid.UUID, execution_id: uuid.UUID) -> str:
        token = f"sse_{secrets.token_urlsafe(32)}"
        redis = get_redis_client()
        key = f"agentchain:sse_ticket:{token}"
        data = {
            "user_id": str(user_id),
            "execution_id": str(execution_id),
        }
        await redis.set(key, json.dumps(data), ex=SSE_TICKET_TTL_SECONDS)
        return token

    @classmethod
    async def validate_and_consume_ticket(
        cls,
        token: str,
        expected_execution_id: uuid.UUID,
    ) -> uuid.UUID | None:
        if not token or not token.startswith("sse_"):
            return None

        redis = get_redis_client()
        key = f"agentchain:sse_ticket:{token}"
        raw = await redis.get(key)
        if not raw:
            return None

        # Delete immediately to enforce single-use semantics
        await redis.delete(key)

        try:
            val = raw.decode("utf-8") if isinstance(raw, bytes) else raw
            data = json.loads(val)
            if data.get("execution_id") != str(expected_execution_id):
                return None
            return uuid.UUID(data["user_id"])
        except Exception:
            return None

    @classmethod
    async def create_orchestration_ticket(cls, user_id: uuid.UUID, orchestration_id: uuid.UUID) -> str:
        token = f"sse_orch_{secrets.token_urlsafe(32)}"
        redis = get_redis_client()
        key = f"agentchain:sse_ticket:{token}"
        data = {
            "user_id": str(user_id),
            "orchestration_id": str(orchestration_id),
        }
        await redis.set(key, json.dumps(data), ex=SSE_TICKET_TTL_SECONDS)
        return token

    @classmethod
    async def validate_and_consume_orchestration_ticket(
        cls,
        token: str,
        expected_orchestration_id: uuid.UUID,
    ) -> uuid.UUID | None:
        if not token or not token.startswith("sse_orch_"):
            return None

        redis = get_redis_client()
        key = f"agentchain:sse_ticket:{token}"
        raw = await redis.get(key)
        if not raw:
            return None

        # Delete immediately to enforce single-use semantics
        await redis.delete(key)

        try:
            val = raw.decode("utf-8") if isinstance(raw, bytes) else raw
            data = json.loads(val)
            if data.get("orchestration_id") != str(expected_orchestration_id):
                return None
            return uuid.UUID(data["user_id"])
        except Exception:
            return None

