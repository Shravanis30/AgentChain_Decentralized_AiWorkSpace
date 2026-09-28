from datetime import datetime, timezone
from typing import Any
import uuid
from pydantic import BaseModel, Field, field_validator


class AgentContext(BaseModel):
    execution_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    agent_id: str = Field(default="")
    agent_version: str = Field(default="1.0.0")
    request_id: str | None = Field(default=None)
    requested_by: str | None = Field(default=None)
    caller_address: str | None = Field(default=None)
    timeout_seconds: float = Field(default=300.0, gt=0)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("execution_id", "agent_id", "requested_by", mode="before")
    @classmethod
    def coerce_uuid_to_str(cls, v: Any) -> Any:
        if isinstance(v, uuid.UUID):
            return str(v)
        return v
