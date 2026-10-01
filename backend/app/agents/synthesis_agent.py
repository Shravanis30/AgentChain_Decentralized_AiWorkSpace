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

SYNTHESIS_AGENT_MANIFEST = AgentManifest(
    protocol_version="1.0",
    agent=AgentMeta(
        id="a3333333-3333-3333-3333-333333333333",
        name="Synthesis Agent",
        version="1.0.0",
        slug="synthesis-agent",
    ),
    description="Deterministic local synthesis and report generation reference agent for AgentChain",
    capabilities=["synthesis", "text_synthesis", "reporting"],
    input_schema={
        "type": "object",
        "properties": {
            "research_summary": {
                "type": "string",
                "description": "Upstream research findings",
            },
            "analysis": {
                "type": "object",
                "description": "Upstream analysis metrics",
            },
            "text": {
                "type": "string",
                "description": "Raw or consolidated input",
            },
        },
        "additionalProperties": True,
    },
    output_schema={
        "type": "object",
        "properties": {
            "final_report": {"type": "string", "description": "Synthesized executive summary and conclusion"},
            "status": {"type": "string", "description": "Completion status"},
            "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        },
        "required": ["final_report", "status", "confidence"],
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


class SynthesisAgent(Agent):
    def __init__(self, manifest: AgentManifest | None = None):
        super().__init__(manifest=manifest or SYNTHESIS_AGENT_MANIFEST)

    async def run(
        self,
        context: AgentContext,
        input_data: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Deterministic local synthesis logic.
        """
        summary = input_data.get("research_summary") or input_data.get("text") or "Completed tasks."
        report = f"Executive Synthesis Report: {summary.strip()} | Synthesized successfully across DAG tasks."
        return {
            "final_report": report,
            "status": "COMPLETED",
            "confidence": 0.98,
        }
