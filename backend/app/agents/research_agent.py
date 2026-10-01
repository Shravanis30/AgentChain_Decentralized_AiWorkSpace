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

RESEARCH_AGENT_MANIFEST = AgentManifest(
    protocol_version="1.0",
    agent=AgentMeta(
        id="a1111111-1111-1111-1111-111111111111",
        name="Research Agent",
        version="1.0.0",
        slug="research-agent",
    ),
    description="Deterministic local research and text summarization reference agent for AgentChain",
    capabilities=["text_summarization"],
    input_schema={
        "type": "object",
        "properties": {
            "text": {
                "type": "string",
                "minLength": 1,
                "maxLength": 100000,
                "description": "Source text to summarize",
            }
        },
        "required": ["text"],
        "additionalProperties": False,
    },
    output_schema={
        "type": "object",
        "properties": {
            "summary": {"type": "string", "description": "Extracted summary of source text"},
            "word_count": {"type": "integer", "minimum": 0},
            "sentence_count": {"type": "integer", "minimum": 0},
            "character_count": {"type": "integer", "minimum": 0},
        },
        "required": ["summary", "word_count", "sentence_count", "character_count"],
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


class ResearchAgent(Agent):
    def __init__(self, manifest: AgentManifest | None = None):
        super().__init__(manifest=manifest or RESEARCH_AGENT_MANIFEST)

    async def run(
        self,
        context: AgentContext,
        input_data: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Deterministic local summarization logic.
        No external LLM API dependency.
        """
        text = input_data["text"].strip()
        words = text.split()
        raw_sentences = [s.strip() for s in text.replace("\n", " ").split(".") if s.strip()]

        # Deterministic extraction of up to 3 most representative sentences
        if len(raw_sentences) <= 3:
            summary = ". ".join(raw_sentences) + ("." if raw_sentences else "")
        else:
            # Pick first sentence, middle sentence, and last sentence
            selected = [
                raw_sentences[0],
                raw_sentences[len(raw_sentences) // 2],
                raw_sentences[-1],
            ]
            summary = ". ".join(selected) + "."

        return {
            "summary": summary,
            "word_count": len(words),
            "sentence_count": len(raw_sentences),
            "character_count": len(text),
        }
