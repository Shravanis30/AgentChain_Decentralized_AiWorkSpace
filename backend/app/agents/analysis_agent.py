from typing import Any
from agentchain_sdk import (
    Agent,
    AgentContext,
    AgentManifest,
    AgentMeta,
    PricingConfig,
    RuntimeConfig,
    VerificationConfig,
)

ANALYSIS_AGENT_MANIFEST = AgentManifest(
    protocol_version="1.0",
    agent=AgentMeta(
        id="a2222222-2222-2222-2222-222222222222",
        name="Analysis Agent",
        version="1.0.0",
        slug="analysis-agent",
    ),
    description="Deterministic local text and data analysis reference agent for AgentChain",
    capabilities=["analysis", "text_analysis"],
    input_schema={
        "type": "object",
        "properties": {
            "text": {
                "type": "string",
                "minLength": 1,
                "description": "Source text or summary to analyze",
            },
            "summary": {
                "type": "string",
                "description": "Optional upstream summary to analyze",
            },
        },
        "additionalProperties": True,
    },
    output_schema={
        "type": "object",
        "properties": {
            "key_points": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Key points extracted from the input",
            },
            "sentiment": {"type": "string", "description": "Derived sentiment"},
            "word_count": {"type": "integer", "minimum": 0},
        },
        "required": ["key_points", "sentiment", "word_count"],
        "additionalProperties": False,
    },
    tools=[],
    runtime=RuntimeConfig(
        timeout_seconds=30,
        max_retries=1,
        memory_mb=256,
    ),
    pricing=PricingConfig(
        model="per_execution",
        amount="0.50",
        currency="USDC",
    ),
    verification=VerificationConfig(
        type="deterministic",
        rules={"deterministic_output": True},
    ),
)


class AnalysisAgent(Agent):
    def __init__(self, manifest: AgentManifest | None = None):
        super().__init__(manifest=manifest or ANALYSIS_AGENT_MANIFEST)

    async def run(
        self,
        context: AgentContext,
        input_data: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Deterministic local analysis logic.
        """
        text = (input_data.get("text") or input_data.get("summary") or "").strip()
        words = text.split()
        sentences = [s.strip() for s in text.replace("\n", " ").split(".") if s.strip()]

        key_points = sentences[:3] if sentences else ["Analysis completed successfully."]
        return {
            "key_points": key_points,
            "sentiment": "neutral",
            "word_count": len(words),
        }
