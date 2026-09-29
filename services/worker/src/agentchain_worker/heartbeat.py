import asyncio
import json
import logging
import socket
from redis.asyncio import Redis

from app.models.base import utc_now
from app.services.rate_limiter import get_redis_client

logger = logging.getLogger(__name__)


class WorkerHeartbeatService:
    def __init__(
        self,
        worker_id: str,
        version: str = "0.1.0",
        interval_seconds: int = 10,
        redis: Redis | None = None,
    ):
        self.worker_id = worker_id
        self.version = version
        self.interval_seconds = interval_seconds
        self._redis = redis
        self._status = "IDLE"
        self._active_executions = 0
        self._task: asyncio.Task[None] | None = None
        self._stopping = False
        self._hostname = socket.gethostname()

    @property
    def redis(self) -> Redis:
        if self._redis is None:
            self._redis = get_redis_client()
        return self._redis

    def set_status(self, status: str, active_count: int | None = None) -> None:
        self._status = status
        if active_count is not None:
            self._active_executions = active_count

    def increment_active(self) -> None:
        self._active_executions += 1
        if self._active_executions > 0 and self._status == "IDLE":
            self._status = "BUSY"

    def decrement_active(self) -> None:
        self._active_executions = max(0, self._active_executions - 1)
        if self._active_executions == 0 and self._status == "BUSY":
            self._status = "IDLE"

    async def publish_heartbeat(self) -> None:
        now = utc_now()
        payload = {
            "worker_id": self.worker_id,
            "timestamp": now.isoformat(),
            "status": self._status,
            "active_execution_count": self._active_executions,
            "version": self.version,
            "hostname": self._hostname,
        }
        key = f"agentchain:workers:{self.worker_id}"
        # Set with TTL = 30 seconds (3x interval)
        ttl = max(30, self.interval_seconds * 3)
        await self.redis.set(key, json.dumps(payload), ex=ttl)

    async def start(self) -> None:
        self._stopping = False
        self._task = asyncio.create_task(self._heartbeat_loop())
        logger.info(f"Worker heartbeat service started for {self.worker_id}")

    async def stop(self) -> None:
        self._stopping = True
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        # Final stopped status
        try:
            self._status = "STOPPED"
            self._active_executions = 0
            await self.publish_heartbeat()
        except Exception:
            pass
        logger.info(f"Worker heartbeat service stopped for {self.worker_id}")

    async def _heartbeat_loop(self) -> None:
        while not self._stopping:
            try:
                await self.publish_heartbeat()
            except Exception as e:
                logger.warning(f"Failed to publish worker heartbeat: {e}")
            try:
                await asyncio.sleep(self.interval_seconds)
            except asyncio.CancelledError:
                break
