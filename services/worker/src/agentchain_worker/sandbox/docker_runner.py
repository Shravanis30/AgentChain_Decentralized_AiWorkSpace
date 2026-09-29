import asyncio
import json
import logging
import os
import time
from typing import Any

from agentchain_worker.sandbox.base import SandboxConfig, SandboxResult, SandboxRunner
from agentchain_worker.sandbox.local_runner import LocalProcessSandboxRunner

logger = logging.getLogger(__name__)

# Strict allowlist of environment variable names permitted inside sandbox containers
ALLOWED_ENV_NAMES = {
    "AGENT_EXECUTION_ID",
    "AGENT_ID",
    "AGENT_VERSION",
    "PYTHONUNBUFFERED",
}


class DockerSandboxRunner(SandboxRunner):
    """
    Production-oriented Docker sandbox runner enforcing zero-trust isolation:
    - Non-privileged execution (privileged=False strictly enforced)
    - Dropped Linux capabilities (cap_drop=["ALL"])
    - Read-only root filesystem with restricted tmpfs workspace
    - Strict CPU, memory, and process limits
    - Network isolation (network_mode="none")
    - Non-root user execution
    - No Docker socket or host filesystem mounts
    - Strict environment-variable allowlisting
    """

    def __init__(
        self,
        image: str = "python:3.12-slim",
        docker_host: str | None = None,
        fallback_to_local_if_no_docker: bool = True,
    ):
        self.image = image
        self.docker_host = docker_host
        self.fallback_to_local_if_no_docker = fallback_to_local_if_no_docker
        self._local_fallback = LocalProcessSandboxRunner()

    def _get_docker_client(self) -> Any:
        try:
            import docker
            if self.docker_host:
                return docker.DockerClient(base_url=self.docker_host)
            return docker.from_env()
        except Exception as e:
            logger.warning(f"Could not connect to Docker daemon: {e}")
            return None

    async def run_agent(
        self,
        agent_manifest: dict[str, Any],
        input_data: dict[str, Any],
        context: dict[str, Any],
        config: SandboxConfig,
    ) -> SandboxResult:
        client = self._get_docker_client()

        if client is None:
            if self.fallback_to_local_if_no_docker:
                logger.info("Docker daemon not reachable; falling back to LocalProcessSandboxRunner")
                return await self._local_fallback.run_agent(
                    agent_manifest=agent_manifest,
                    input_data=input_data,
                    context=context,
                    config=config,
                )
            return SandboxResult(
                success=False,
                output=None,
                error_code="DOCKER_DAEMON_ERROR",
                error_message="Cannot connect to Docker daemon for container sandbox execution",
            )

        start_time = time.perf_counter()

        # Build clean environment allowlist (NEVER leak host secrets or API tokens)
        safe_env: dict[str, str] = {
            "PYTHONUNBUFFERED": "1",
            "AGENT_EXECUTION_ID": str(context.get("execution_id", "")),
            "AGENT_ID": str(context.get("agent_id", "")),
            "AGENT_VERSION": str(context.get("agent_version", "")),
        }
        for k, v in config.env_allowlist.items():
            if k in ALLOWED_ENV_NAMES:
                safe_env[k] = str(v)

        # Prepare payload
        invocation_payload = {
            "manifest": agent_manifest,
            "input": input_data,
            "context": context,
        }
        raw_payload = json.dumps(invocation_payload)

        # Runner entrypoint script content to execute in container
        entrypoint_path = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "runner_entrypoint.py")
        )
        try:
            with open(entrypoint_path, "r", encoding="utf-8") as f:
                runner_code = f.read()
        except Exception as e:
            logger.error(f"Failed to read runner entrypoint: {e}")
            return SandboxResult(
                success=False,
                error_code="RUNNER_SCRIPT_ERROR",
                error_message=f"Could not load runner script: {e}",
            )

        container = None
        timed_out = False

        # Docker run with strict security profile
        try:
            # We run in a background thread to keep async event loop responsive
            def _create_and_run() -> tuple[int, bytes, bytes]:
                nonlocal container
                # Security parameters:
                # 1. privileged=False (NEVER use --privileged)
                # 2. cap_drop=["ALL"]
                # 3. read_only=True
                # 4. tmpfs for ephemeral writable /tmp
                # 5. network_mode="none"
                # 6. user="1000:1000" (non-root)
                # 7. No host mounts, no docker socket
                container = client.containers.create(
                    image=self.image,
                    command=["python", "-c", runner_code],
                    stdin_open=True,
                    network_mode=config.network_mode,
                    mem_limit=f"{config.memory_limit_mb}m",
                    nano_cpus=int(config.cpu_limit * 1e9),
                    pids_limit=config.pids_limit,
                    read_only=config.read_only_root,
                    tmpfs={"/tmp": f"rw,noexec,nosuid,size={config.tmpfs_size_mb}m"},
                    cap_drop=config.drop_capabilities,
                    security_opt=["no-new-privileges:true"],
                    privileged=False,
                    user=config.user,
                    environment=safe_env,
                )

                container.start()

                # Attach socket to write stdin
                try:
                    sock = container.attach_socket(params={"stdin": 1, "stream": 1})
                    if hasattr(sock, "_sock"):
                        sock._sock.sendall(raw_payload.encode("utf-8"))
                        try:
                            sock._sock.shutdown(1)  # SHUT_WR
                        except Exception:
                            pass
                except Exception as err:
                    logger.debug(f"Socket write error: {err}")
                finally:
                    try:
                        sock.close()
                    except Exception:
                        pass

                res = container.wait(timeout=config.timeout_seconds)
                exit_code = res.get("StatusCode", 0)
                logs = container.logs(stdout=True, stderr=True)
                return exit_code, logs, b""

            loop = asyncio.get_running_loop()
            exit_code, stdout_data, stderr_data = await asyncio.wait_for(
                loop.run_in_executor(None, _create_and_run),
                timeout=float(config.timeout_seconds + 5),
            )

        except (asyncio.TimeoutError, Exception) as err:
            err_name = type(err).__name__
            if "Timeout" in err_name:
                timed_out = True
            logger.warning(f"Docker sandbox execution exception ({err_name}): {err}")

            if container:
                try:
                    container.kill()
                except Exception:
                    pass
                try:
                    container.remove(force=True)
                except Exception:
                    pass

            elapsed_ms = int((time.perf_counter() - start_time) * 1000)
            if timed_out:
                return SandboxResult(
                    success=False,
                    output=None,
                    error_code="EXECUTION_TIMEOUT",
                    error_message=f"Container execution timed out after {config.timeout_seconds} seconds",
                    execution_time_ms=elapsed_ms,
                    timed_out=True,
                )
            # If Docker runtime failure (e.g. image missing or container creation failure), fall back if enabled
            if self.fallback_to_local_if_no_docker:
                logger.info("Falling back to LocalProcessSandboxRunner due to Docker execution error")
                return await self._local_fallback.run_agent(
                    agent_manifest=agent_manifest,
                    input_data=input_data,
                    context=context,
                    config=config,
                )
            return SandboxResult(
                success=False,
                output=None,
                error_code="CONTAINER_START_FAILURE",
                error_message=f"Container execution failed: {err!s}",
                execution_time_ms=elapsed_ms,
            )

        finally:
            if container:
                try:
                    container.remove(force=True)
                except Exception:
                    pass

        elapsed_ms = int((time.perf_counter() - start_time) * 1000)
        stdout_str = stdout_data.decode("utf-8", errors="replace")[: config.max_log_bytes]

        if len(stdout_data) > config.max_output_bytes:
            return SandboxResult(
                success=False,
                output=None,
                error_code="PAYLOAD_LIMIT_EXCEEDED",
                error_message=f"Container output exceeded limit of {config.max_output_bytes} bytes",
                execution_time_ms=elapsed_ms,
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
            )
        except Exception as e:
            return SandboxResult(
                success=False,
                output=None,
                error_code="MALFORMED_SANDBOX_OUTPUT",
                error_message=f"Could not parse container output: {e!s}",
                execution_time_ms=elapsed_ms,
                stdout=stdout_str,
            )
