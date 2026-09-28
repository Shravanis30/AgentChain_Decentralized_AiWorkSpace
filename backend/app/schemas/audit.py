import uuid
from datetime import datetime
from typing import Any
from pydantic import BaseModel, ConfigDict, Field


class AuditLogResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID | None
    wallet_address: str | None
    event_type: str
    timestamp: datetime
    request_id: str | None
    ip_address: str | None
    user_agent: str | None
    metadata_json: dict[str, Any] = Field(default_factory=dict)
