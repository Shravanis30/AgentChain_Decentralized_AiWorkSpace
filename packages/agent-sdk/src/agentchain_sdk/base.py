from abc import ABC, abstractmethod
import asyncio
import time
from typing import Any

from agentchain_sdk.context import AgentContext
from agentchain_sdk.manifest import AgentManifest
from agentchain_sdk.models import AgentError, AgentResult


class Agent(ABC):
    manifest: AgentManifest

    def __init__(self, manifest: AgentManifest | None = None):
        if manifest is not None:
            self.manifest = manifest

    async def execute(
        self,
        context: AgentContext,
        input: dict[str, Any],
    ) -> AgentResult:
        """
        Executes the agent with full lifecycle enforcement:
        1. Validates input data against manifest.input_schema
        2. Sets execution context and enforces timeout awareness
        3. Calls subclass run() implementation
        4. Validates output data against manifest.output_schema
        5. Returns structured AgentResult
        """
        # 1. Enforce input validation
        self.manifest.validate_input(input)

        # 2. Timeout and execution timing
        start_time = time.perf_counter()
        timeout = context.timeout_seconds or self.manifest.runtime.timeout_seconds

        try:
            raw_output = await asyncio.wait_for(
                self.run(context, input),
                timeout=float(timeout),
            )
        except asyncio.TimeoutError as e:
            raise AgentError(
                code="EXECUTION_TIMEOUT",
                message=f"Agent execution timed out after {timeout} seconds",
                details={"timeout_seconds": timeout, "execution_id": context.execution_id},
            ) from e
        except AgentError:
            raise
        except Exception as e:
            raise AgentError(
                code="INTERNAL_AGENT_ERROR",
                message=f"Agent internal execution failure: {e!s}",
                details={"execution_id": context.execution_id, "error_type": type(e).__name__},
            ) from e

        elapsed_ms = int((time.perf_counter() - start_time) * 1000)

        # 3. Enforce output validation
        self.manifest.validate_output(raw_output)

        return AgentResult(
            execution_id=context.execution_id,
            output=raw_output,
            execution_time_ms=elapsed_ms,
            metadata={"agent_id": context.agent_id, "version": context.agent_version},
        )

    @abstractmethod
    async def run(
        self,
        context: AgentContext,
        input_data: dict[str, Any],
    ) -> dict[str, Any]:
        """Subclasses implement deterministic or AI logic here returning raw output dict."""
        pass
