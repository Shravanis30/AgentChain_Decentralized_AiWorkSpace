from datetime import datetime
from typing import Any
import uuid
from pydantic import BaseModel, ConfigDict, Field

from app.schemas.agent_manifest import AgentManifest


class AgentCreateRequest(BaseModel):
    name: str = Field(..., min_length=2, max_length=128)
    slug: str = Field(..., min_length=2, max_length=128)
    description: str | None = Field(default=None, max_length=2048)
    manifest: AgentManifest


class AgentUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=128)
    description: str | None = Field(default=None, max_length=2048)
    manifest: AgentManifest | None = Field(default=None)


class AgentCapabilityResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    capability: str
    description: str | None = None


class AgentToolResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tool_name: str
    configuration: dict[str, Any]
    enabled: bool


class AgentVersionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    version: str
    manifest: dict[str, Any]
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    runtime_config: dict[str, Any]
    pricing_config: dict[str, Any]
    created_at: datetime
    published_at: datetime | None = None


class AgentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    owner_user_id: uuid.UUID
    name: str
    slug: str
    description: str | None
    status: str
    current_version_id: uuid.UUID | None = None
    current_version: AgentVersionResponse | None = None
    capabilities: list[str] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime
    published_at: datetime | None = None


class AgentListResponse(BaseModel):
    items: list[AgentResponse]
    total: int
    page: int
    page_size: int
    total_pages: int


class AgentExecutionRequest(BaseModel):
    input: dict[str, Any] = Field(..., description="Invocation parameters conforming to agent input_schema")


class AgentExecutionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    agent_id: uuid.UUID
    agent_version_id: uuid.UUID
    requested_by: uuid.UUID
    status: str
    input_hash: str
    output_hash: str | None
    input_data: dict[str, Any]
    output_data: dict[str, Any] | None
    queued_at: datetime | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    timeout_at: datetime | None = None
    cancelled_at: datetime | None = None
    attempt_count: int = 0
    worker_id: str | None = None
    cancellation_requested: bool = False
    error_code: str | None = None
    error_message: str | None = None
    metadata_json: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime


class ExecutionSubmitResponse(BaseModel):
    execution_id: uuid.UUID
    status: str
    input_hash: str
    agent_id: uuid.UUID
    agent_version: str
