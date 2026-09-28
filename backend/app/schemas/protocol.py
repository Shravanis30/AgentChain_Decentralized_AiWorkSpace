from datetime import datetime
import enum
from typing import Any
import uuid
from pydantic import BaseModel, ConfigDict, Field

from app.models.base import utc_now


class ProtocolMessageType(str, enum.Enum):
    REGISTER = "REGISTER"
    VALIDATE = "VALIDATE"
    PUBLISH = "PUBLISH"
    INVOKE = "INVOKE"
    PROGRESS = "PROGRESS"
    RESULT = "RESULT"
    ERROR = "ERROR"
    CANCEL = "CANCEL"
    HEARTBEAT = "HEARTBEAT"


class ProtocolMessage(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    protocol_version: str = Field(default="1.0", description="AgentChain protocol version")
    message_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    message_type: ProtocolMessageType
    timestamp: datetime = Field(default_factory=utc_now)
    request_id: str | None = Field(default=None)
    agent_id: str | None = Field(default=None)
    execution_id: str | None = Field(default=None)
    payload: dict[str, Any] = Field(default_factory=dict)


class InvokePayload(BaseModel):
    input_data: dict[str, Any]
    caller_address: str | None = None
    timeout_seconds: int = 300


class ProgressPayload(BaseModel):
    percent_complete: float = Field(ge=0.0, le=100.0)
    status_message: str
    step_name: str | None = None


class ResultPayload(BaseModel):
    output_data: dict[str, Any]
    execution_time_ms: int
    output_hash: str


class ErrorPayload(BaseModel):
    error_code: str
    error_message: str
    details: dict[str, Any] = Field(default_factory=dict)


class HeartbeatPayload(BaseModel):
    worker_id: str
    status: str
    active_executions: int
    memory_usage_mb: float | None = None
