from datetime import datetime
from typing import Any
import uuid

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class TaskDefinitionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_key: str = Field(
        ...,
        pattern=r"^[a-zA-Z0-9_\-\.]{1,64}$",
        description="Unique identifier for the task within this orchestration",
    )
    agent_id: uuid.UUID | None = Field(
        default=None,
        description="Pinned agent ID. If omitted, agent selector resolves by capability.",
    )
    agent_version: str | None = Field(
        default=None,
        description="Pinned immutable agent version (e.g. '1.0.0'). Required if agent_id is provided.",
    )
    capability: str | None = Field(
        default=None,
        description="Required capability if agent_id is omitted (e.g. 'research', 'analysis', 'synthesis').",
    )
    input: dict[str, Any] = Field(
        default_factory=dict,
        description="Task input payload",
    )
    depends_on: list[str] = Field(
        default_factory=list,
        description="List of task_keys that this task directly depends upon",
    )
    max_attempts: int = Field(
        default=3,
        ge=1,
        le=10,
        description="Maximum execution attempts before task is marked FAILED",
    )
    max_budget_atomic: int | None = Field(
        default=None,
        ge=0,
        description="Maximum budget in token atomic units (e.g. 1000000 for 1.00 USDC)",
    )
    max_budget: str | None = Field(
        default=None,
        description="Maximum budget in human-readable display units (e.g. '1.50' for 1.50 USDC)",
    )
    budget_currency: str = Field(
        default="USDC",
        description="Currency for budget constraint (default: 'USDC')",
    )
    required_tools: list[str] = Field(
        default_factory=list,
        description="List of tool names required from the selected agent",
    )
    max_timeout_seconds: int | None = Field(
        default=None,
        ge=1,
        le=3600,
        description="Maximum execution timeout in seconds permitted for selected agent",
    )

    @field_validator("budget_currency")
    @classmethod
    def validate_currency(cls, v: str) -> str:
        upper = v.strip().upper()
        if upper != "USDC":
            raise ValueError(f"Unsupported budget currency: '{v}'. Only 'USDC' is supported.")
        return upper

    @field_validator("max_budget_atomic", mode="before")
    @classmethod
    def validate_budget_atomic(cls, v: Any) -> int | None:
        if v is None:
            return None
        from app.services.pricing import parse_atomic_units
        return parse_atomic_units(v)

    @model_validator(mode="before")
    @classmethod
    def resolve_budget_values(cls, data: Any) -> Any:
        if isinstance(data, dict):
            mb = data.get("max_budget")
            mba = data.get("max_budget_atomic")
            if mb is not None:
                from app.services.pricing import parse_usdc_display_to_atomic
                parsed = parse_usdc_display_to_atomic(mb)
                if mba is not None and mba != parsed:
                    raise ValueError(
                        f"Conflicting budget parameters: max_budget '{mb}' ({parsed} atomic) != max_budget_atomic ({mba})"
                    )
                data["max_budget_atomic"] = parsed
        return data


class DAGDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tasks: list[TaskDefinitionInput] = Field(
        ...,
        min_length=1,
        description="List of tasks forming the DAG",
    )


class OrchestrationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    goal: str = Field(
        ...,
        min_length=1,
        max_length=2000,
        description="High-level user goal to be decomposed and orchestrated",
    )
    tasks: list[TaskDefinitionInput] | None = Field(
        default=None,
        description="Optional explicit DAG tasks. If omitted, deterministic planner plans default DAG.",
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Additional orchestration metadata",
    )


class TaskResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    orchestration_id: uuid.UUID
    task_key: str
    agent_id: uuid.UUID
    agent_version: str
    price_atomic: int | None = None
    price_currency: str = "USDC"
    status: str
    dependencies: list[str]
    execution_id: uuid.UUID | None = None
    attempt: int
    max_attempts: int
    input_hash: str
    output_hash: str | None = None
    output_data: dict[str, Any] | None = None
    error_code: str | None = None
    error_message: str | None = None
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None


class ArtifactResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    orchestration_id: uuid.UUID
    task_id: uuid.UUID
    producer_agent_id: uuid.UUID
    producer_agent_version: str
    execution_id: uuid.UUID | None = None
    artifact_key: str
    content_type: str
    schema_version: str
    sha256: str
    size_bytes: int
    created_at: datetime
    content_json: dict[str, Any] | None = None


class OrchestrationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    requested_by: uuid.UUID
    goal: str
    status: str
    graph_version: str
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    failed_at: datetime | None = None
    cancelled_at: datetime | None = None
    result: dict[str, Any] | None = None
    error_code: str | None = None
    error_message: str | None = None
    tasks: list[TaskResponse] = []
    artifacts: list[ArtifactResponse] = []
    metadata: dict[str, Any] = Field(default_factory=dict)


class OrchestrationListResponse(BaseModel):
    items: list[OrchestrationResponse]
    total: int


class SSETokenResponse(BaseModel):
    token: str
    expires_in: int
    stream_url: str
