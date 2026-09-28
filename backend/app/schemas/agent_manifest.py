import re
from typing import Any
import jsonschema
from pydantic import BaseModel, ConfigDict, Field, field_validator

SEMVER_REGEX = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-((?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*)(?:\.(?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*))*))?(?:\+([0-9a-zA-Z-]+(?:\.[0-9a-zA-Z-]+)*))?$"
)
SLUG_REGEX = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class AgentMeta(BaseModel):
    id: str | None = Field(default=None, description="Unique agent identifier (UUID)")
    name: str = Field(..., min_length=2, max_length=128, description="Human readable agent name")
    version: str = Field(..., description="Semantic version string (e.g. 1.0.0)")
    slug: str | None = Field(default=None, max_length=128, description="URL-friendly unique slug")

    @field_validator("version")
    @classmethod
    def validate_semver(cls, v: str) -> str:
        if not SEMVER_REGEX.match(v):
            raise ValueError(f"Version '{v}' is not a valid Semantic Version (e.g. 1.0.0)")
        return v

    @field_validator("slug")
    @classmethod
    def validate_slug(cls, v: str | None) -> str | None:
        if v is not None and not SLUG_REGEX.match(v):
            raise ValueError(f"Slug '{v}' must be lowercase alphanumeric with hyphens (e.g. research-agent)")
        return v


class ToolConfig(BaseModel):
    tool_name: str = Field(..., min_length=1, max_length=64)
    configuration: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = Field(default=True)


class RuntimeConfig(BaseModel):
    timeout_seconds: int = Field(default=300, ge=1, le=3600, description="Max execution timeout in seconds")
    max_retries: int = Field(default=2, ge=0, le=10, description="Max retries upon transient failure")
    memory_mb: int = Field(default=512, ge=128, le=8192, description="Allocated memory limit in MB")


class PricingConfig(BaseModel):
    model: str = Field(default="per_execution", description="Pricing model (per_execution, per_minute)")
    amount: str = Field(default="1.00", description="Cost in payment asset units")
    currency: str = Field(default="USDC", description="Settlement asset currency")

    @field_validator("amount")
    @classmethod
    def validate_amount(cls, v: str) -> str:
        try:
            val = float(v)
            if val < 0:
                raise ValueError
        except ValueError:
            raise ValueError(f"Pricing amount '{v}' must be a non-negative numeric string")
        return v


class VerificationConfig(BaseModel):
    type: str = Field(default="deterministic", description="Verification strategy (deterministic, quorum, llm_judge)")
    rules: dict[str, Any] = Field(default_factory=dict)


class AgentManifest(BaseModel):
    protocol_version: str = Field(default="1.0", description="AgentChain agent protocol specification version")
    agent: AgentMeta
    description: str = Field(..., min_length=5, max_length=2048, description="Description of agent purpose and workflow")
    capabilities: list[str] = Field(..., min_length=1, description="List of recognized capability tags")
    input_schema: dict[str, Any] = Field(..., description="JSON Schema for required agent invocation input")
    output_schema: dict[str, Any] = Field(..., description="JSON Schema for structured deliverable output")
    tools: list[ToolConfig] = Field(default_factory=list, description="Tools available to the agent")
    runtime: RuntimeConfig = Field(default_factory=RuntimeConfig)
    pricing: PricingConfig = Field(default_factory=PricingConfig)
    verification: VerificationConfig = Field(default_factory=VerificationConfig)

    @field_validator("capabilities")
    @classmethod
    def validate_capabilities(cls, caps: list[str]) -> list[str]:
        if not caps:
            raise ValueError("At least one capability must be defined")
        cleaned = [c.strip().lower() for c in caps if c.strip()]
        if not cleaned:
            raise ValueError("Capabilities cannot be empty")
        return cleaned

    @field_validator("input_schema", "output_schema")
    @classmethod
    def validate_json_schema(cls, schema: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(schema, dict) or not schema:
            raise ValueError("Schema must be a non-empty dictionary representing a valid JSON Schema")
        try:
            # Validate that the schema itself is a valid JSON Schema meta-schema
            jsonschema.Draft202012Validator.check_schema(schema)
        except jsonschema.exceptions.SchemaError as e:
            raise ValueError(f"Invalid JSON Schema structure: {e.message}")
        if schema.get("type") != "object":
            raise ValueError("Root schema type must be 'object'")
        return schema
