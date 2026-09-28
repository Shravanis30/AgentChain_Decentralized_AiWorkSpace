from typing import Any
import jsonschema
from pydantic import BaseModel, Field


class AgentMeta(BaseModel):
    id: str | None = None
    name: str
    version: str
    slug: str | None = None


class ToolConfig(BaseModel):
    tool_name: str
    configuration: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True


class RuntimeConfig(BaseModel):
    timeout_seconds: int = 300
    max_retries: int = 2
    memory_mb: int = 512


class PricingConfig(BaseModel):
    model: str = "per_execution"
    amount: str = "1.00"
    currency: str = "USDC"


class VerificationConfig(BaseModel):
    type: str = "deterministic"
    rules: dict[str, Any] = Field(default_factory=dict)


class AgentManifest(BaseModel):
    protocol_version: str = "1.0"
    agent: AgentMeta
    description: str
    capabilities: list[str]
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    tools: list[ToolConfig] = Field(default_factory=list)
    runtime: RuntimeConfig = Field(default_factory=RuntimeConfig)
    pricing: PricingConfig = Field(default_factory=PricingConfig)
    verification: VerificationConfig = Field(default_factory=VerificationConfig)

    def validate_input(self, data: dict[str, Any]) -> None:
        try:
            jsonschema.validate(instance=data, schema=self.input_schema)
        except jsonschema.exceptions.ValidationError as e:
            from agentchain_sdk.models import AgentError
            raise AgentError(
                code="INPUT_VALIDATION_ERROR",
                message=f"Input validation error: {e.message}",
                details={"path": list(e.path), "schema_path": list(e.schema_path)},
            ) from e

    def validate_output(self, data: dict[str, Any]) -> None:
        try:
            jsonschema.validate(instance=data, schema=self.output_schema)
        except jsonschema.exceptions.ValidationError as e:
            from agentchain_sdk.models import AgentError
            raise AgentError(
                code="OUTPUT_VALIDATION_ERROR",
                message=f"Output validation error: {e.message}",
                details={"path": list(e.path), "schema_path": list(e.schema_path)},
            ) from e
