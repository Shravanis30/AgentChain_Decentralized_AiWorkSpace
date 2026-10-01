from datetime import datetime, timezone
import json
import logging
from fastapi import APIRouter, Depends, status
from redis.asyncio import Redis

from app.api.deps import require_role
from app.models.base import utc_now
from app.models.user import User, UserRole
from app.schemas.worker import WorkerInfo, WorkerListResponse
from app.services.rate_limiter import get_redis_client

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/operator", tags=["Operator / Admin Infrastructure"])


@router.get(
    "/workers",
    response_model=WorkerListResponse,
    status_code=status.HTTP_200_OK,
    summary="Get cluster worker heartbeat status and active execution count (Operator/Admin only)",
)
async def list_workers(
    user: User = Depends(require_role(UserRole.ADMIN, UserRole.AGENT_OPERATOR)),
) -> WorkerListResponse:
    """
    Returns registered worker heartbeats from Redis.
    Workers are marked alive if their last heartbeat was within 30 seconds.
    Restricted to operator / admin roles.
    """
    redis = get_redis_client()
    now = utc_now()
    workers: list[WorkerInfo] = []

    try:
        keys = await redis.keys("agentchain:workers:*")
        if keys:
            for k in keys:
                key_str = k.decode("utf-8") if isinstance(k, bytes) else str(k)
                data_str = await redis.get(key_str)
                if not data_str:
                    continue
                try:
                    payload = json.loads(data_str)
                    ts_str = payload.get("timestamp")
                    ts = datetime.fromisoformat(ts_str) if ts_str else now
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=timezone.utc)
                    age_sec = max(0.0, (now - ts).total_seconds())
                    is_alive = age_sec <= 30.0

                    workers.append(
                        WorkerInfo(
                            worker_id=payload.get("worker_id", key_str.split(":")[-1]),
                            timestamp=ts,
                            status=payload.get("status", "UNKNOWN"),
                            active_execution_count=int(payload.get("active_execution_count", 0)),
                            version=payload.get("version", "1.0.0"),
                            hostname=payload.get("hostname"),
                            is_alive=is_alive,
                            heartbeat_age_seconds=round(age_sec, 2),
                        )
                    )
                except Exception as parse_err:
                    logger.warning(f"Error parsing worker heartbeat for {key_str}: {parse_err}")
    except Exception as e:
        logger.error(f"Failed to query worker heartbeats from Redis: {e}")

    # Sort by worker_id
    workers.sort(key=lambda w: w.worker_id)
    return WorkerListResponse(workers=workers, total=len(workers))
