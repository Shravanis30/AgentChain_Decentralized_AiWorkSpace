from datetime import datetime
from pydantic import BaseModel, Field


class WorkerHeartbeat(BaseModel):
    worker_id: str = Field(..., description="Unique worker node identifier")
    timestamp: datetime = Field(..., description="Heartbeat generation timestamp")
    status: str = Field(..., description="Worker status: IDLE, BUSY, DRAINING, STOPPED")
    active_execution_count: int = Field(default=0, ge=0, description="Number of currently running sandboxes")
    version: str = Field(default="1.0.0", description="Worker service build version")
    hostname: str | None = Field(default=None, description="Host / container node name")


class WorkerInfo(BaseModel):
    worker_id: str
    timestamp: datetime
    status: str
    active_execution_count: int
    version: str
    hostname: str | None = None
    is_alive: bool
    heartbeat_age_seconds: float


class WorkerListResponse(BaseModel):
    workers: list[WorkerInfo]
    total: int
