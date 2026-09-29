import asyncio
import json
import logging
import os
import sys
import time
from typing import Any

from agentchain_worker.sandbox.base import SandboxConfig, SandboxResult, SandboxRunner

logger = logging.getLogger(__name__)


class LocalProcessSandboxRunner(SandboxRunner):
    """
    Subprocess-isolated sandbox runner with strict resource limits,
    explicit environment-variable allowlist, timeout enforcement,
    and process termination.
    """

    async def run_agent(
        self,
        agent_manifest: dict[str, Any],
        input_data: dict[str, Any],
        context: dict[str, Any],
        config: SandboxConfig,
    ) -> SandboxResult:
        start_time = time.perf_counter()

        # Build clean environment allowlist (NEVER expose host secrets/credentials)
        env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "PYTHONPATH": os.pathsep.join([
                os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")),
                os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "backend")),
                os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "packages", "agent-sdk", "src")),
            ]),
            "AGENT_EXECUTION_ID": str(context.get("execution_id", "")),
            "AGENT_ID": str(context.get("agent_id", "")),
            "AGENT_VERSION": str(context.get("agent_version", "")),
        }
        # Add explicitly configured allowlisted env vars
        env.update(config.env_allowlist)

        # Prepare invocation payload
        invocation_payload = {
            "manifest": agent_manifest,
            "input": input_data,
            "context": context,
        }
        raw_payload = json.dumps(invocation_payload).encode("utf-8")

        # Determine entrypoint path
        entrypoint_file = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "runner_entrypoint.py")
        )

        proc = None
        timed_out = False
        try:
            proc = await asyncio.create_subprocess_exec(
                sys.executable,
                entrypoint_file,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )

            stdout_data, stderr_data = await asyncio.wait_for(
                proc.communicate(input=raw_payload),
                timeout=float(config.timeout_seconds),
            )

        except asyncio.TimeoutError:
            timed_out = True
            if proc:
                try:
                    proc.kill()
                    await proc.wait()
                except Exception:
                    pass
            elapsed_ms = int((time.perf_counter() - start_time) * 1000)
            return SandboxResult(
                success=False,
                output=None,
                error_code="EXECUTION_TIMEOUT",
                error_message=f"Sandbox execution timed out after {config.timeout_seconds} seconds",
                execution_time_ms=elapsed_ms,
                timed_out=True,
            )
        except Exception as e:
            if proc:
                try:
                    proc.kill()
                except Exception:
                    pass
            elapsed_ms = int((time.perf_counter() - start_time) * 1000)
            return SandboxResult(
                success=False,
                output=None,
                error_code="SANDBOX_LAUNCH_ERROR",
                error_message=f"Failed to launch sandbox subprocess: {e!s}",
                execution_time_ms=elapsed_ms,
            )

        elapsed_ms = int((time.perf_counter() - start_time) * 1000)
        stdout_str = stdout_data.decode("utf-8", errors="replace")[: config.max_log_bytes]
        stderr_str = stderr_data.decode("utf-8", errors="replace")[: config.max_log_bytes]

        # Enforce max payload output size
        if len(stdout_data) > config.max_output_bytes:
            return SandboxResult(
                success=False,
                output=None,
                error_code="PAYLOAD_LIMIT_EXCEEDED",
                error_message=f"Sandbox output ({len(stdout_data)} bytes) exceeded limit of {config.max_output_bytes} bytes",
                execution_time_ms=elapsed_ms,
                stdout=stdout_str,
                stderr=stderr_str,
            )

        try:
            parsed = json.loads(stdout_str)
            return SandboxResult(
                success=parsed.get("success", False),
                output=parsed.get("output"),
                error_code=parsed.get("error_code"),
                error_message=parsed.get("error_message"),
                execution_time_ms=parsed.get("execution_time_ms", elapsed_ms),
                stdout=stdout_str,
                stderr=stderr_str,
            )
        except Exception as e:
            return SandboxResult(
                success=False,
                output=None,
                error_code="MALFORMED_SANDBOX_OUTPUT",
                error_message=f"Could not parse sandbox output JSON: {e!s}",
                execution_time_ms=elapsed_ms,
                stdout=stdout_str,
                stderr=stderr_str,
            )
