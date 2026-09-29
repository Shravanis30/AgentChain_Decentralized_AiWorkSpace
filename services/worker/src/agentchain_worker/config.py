import os
import socket
import uuid
from pydantic_settings import BaseSettings, SettingsConfigDict


class WorkerConfig(BaseSettings):
    worker_id: str = os.environ.get("WORKER_ID", f"worker-{socket.gethostname()}-{uuid.uuid4().hex[:6]}")
    version: str = "0.1.0"
    queue_name: str = "agent_executions"
    concurrency: int = 2
    poll_block_ms: int = 2000
    heartbeat_interval_seconds: int = 10
    stale_execution_threshold_seconds: int = 60
    stale_check_interval_seconds: int = 30
    sandbox_type: str = os.environ.get("SANDBOX_TYPE", "docker")
    sandbox_image: str = os.environ.get("SANDBOX_IMAGE", "python:3.12-slim")
    docker_host: str | None = os.environ.get("DOCKER_HOST")

    model_config = SettingsConfigDict(
        env_prefix="WORKER_",
        case_sensitive=False,
        extra="ignore",
    )


worker_config = WorkerConfig()
