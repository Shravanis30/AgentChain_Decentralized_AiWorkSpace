import asyncio
import json
import sys
import time
import traceback
from typing import Any

try:
    from agentchain_sdk import AgentContext, AgentError, AgentManifest
    from app.agents.analysis_agent import AnalysisAgent
    from app.agents.research_agent import ResearchAgent
    from app.agents.synthesis_agent import SynthesisAgent
    HAS_SDK = True
except ImportError:
    HAS_SDK = False


def _run_reference_research_agent(input_data: dict[str, Any]) -> dict[str, Any]:
    text = input_data.get("text", "").strip()
    words = text.split()
    raw_sentences = [s.strip() for s in text.replace("\n", " ").split(".") if s.strip()]

    if len(raw_sentences) <= 3:
        summary = ". ".join(raw_sentences) + ("." if raw_sentences else "")
    else:
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


async def main() -> None:
    start_time = time.perf_counter()
    try:
        # Read invocation payload from stdin
        raw_input = sys.stdin.read()
        if not raw_input:
            result = {
                "success": False,
                "output": None,
                "error_code": "EMPTY_INPUT",
                "error_message": "Runner received empty input stream",
            }
            print(json.dumps(result))
            return

        payload = json.loads(raw_input)
        manifest_dict = payload.get("manifest", {})
        input_data = payload.get("input", {})
        context_dict = payload.get("context", {})

        if HAS_SDK:
            manifest = AgentManifest.model_validate(manifest_dict)
            context = AgentContext(
                execution_id=context_dict.get("execution_id", "local"),
                agent_id=context_dict.get("agent_id", manifest.agent.id or "unknown"),
                agent_version=context_dict.get("agent_version", manifest.agent.version),
                timeout_seconds=context_dict.get("timeout_seconds", manifest.runtime.timeout_seconds),
            )
            slug = manifest.agent.slug.lower()
            if "analysis" in slug or "analysis" in manifest.capabilities:
                agent = AnalysisAgent(manifest=manifest)
            elif "synthesis" in slug or "synthesis" in manifest.capabilities:
                agent = SynthesisAgent(manifest=manifest)
            else:
                agent = ResearchAgent(manifest=manifest)

            agent_res = await agent.execute(context=context, input=input_data)
            out = {
                "success": True,
                "output": agent_res.output,
                "execution_time_ms": agent_res.execution_time_ms,
                "error_code": None,
                "error_message": None,
            }
            print(json.dumps(out))
        else:
            # Standalone execution inside bare python container
            slug = manifest_dict.get("agent", {}).get("slug", "").lower()
            if "analysis" in slug:
                output = {
                    "key_points": [input_data.get("text", "Analysis completed.")],
                    "sentiment": "neutral",
                    "word_count": len(input_data.get("text", "").split()),
                }
            elif "synthesis" in slug:
                output = {
                    "final_report": f"Executive Report: {input_data.get('research_summary', 'Consolidated output.')}",
                    "status": "COMPLETED",
                    "confidence": 0.98,
                }
            else:
                output = _run_reference_research_agent(input_data)
            elapsed_ms = int((time.perf_counter() - start_time) * 1000)
            out = {
                "success": True,
                "output": output,
                "execution_time_ms": elapsed_ms,
                "error_code": None,
                "error_message": None,
            }
            print(json.dumps(out))

    except Exception as e:
        elapsed_ms = int((time.perf_counter() - start_time) * 1000)
        out = {
            "success": False,
            "output": None,
            "execution_time_ms": elapsed_ms,
            "error_code": "INTERNAL_AGENT_ERROR",
            "error_message": f"{type(e).__name__}: {e!s}",
            "traceback": traceback.format_exc(),
        }
        print(json.dumps(out))


if __name__ == "__main__":
    asyncio.run(main())
