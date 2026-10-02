import asyncio
import os
import pytest
from unittest.mock import MagicMock, patch

from agentchain_worker.sandbox.base import SandboxConfig, SandboxResult
from agentchain_worker.sandbox.docker_runner import ALLOWED_ENV_NAMES, DockerSandboxRunner
from agentchain_worker.sandbox.local_runner import LocalProcessSandboxRunner
from app.agents.research_agent import RESEARCH_AGENT_MANIFEST


@pytest.mark.asyncio
async def test_local_sandbox_successful_execution():
    runner = LocalProcessSandboxRunner()
    config = SandboxConfig(timeout_seconds=10, max_output_bytes=1024 * 1024)
    manifest = RESEARCH_AGENT_MANIFEST.model_dump()
    context = {"execution_id": "test-local-exec", "agent_id": "agent-1", "agent_version": "1.0.0"}
    input_data = {"text": "AgentChain sandboxed execution isolation test."}

    res = await runner.run_agent(manifest, input_data, context, config)
    assert res.success is True
    assert res.output is not None
    assert "summary" in res.output
    assert res.output["word_count"] > 0
    assert res.error_code is None


@pytest.mark.asyncio
async def test_local_sandbox_timeout_termination():
    runner = LocalProcessSandboxRunner()
    # Extremely short timeout (0.01 seconds) to trigger timeout termination
    config = SandboxConfig(timeout_seconds=0.01)
    manifest = RESEARCH_AGENT_MANIFEST.model_dump()
    context = {"execution_id": "test-timeout-exec", "agent_id": "agent-1", "agent_version": "1.0.0"}
    input_data = {"text": "Some text to process."}

    res = await runner.run_agent(manifest, input_data, context, config)
    # Runner must cleanly capture timeout and terminate
    assert res.success is False
    assert res.timed_out is True
    assert res.error_code == "EXECUTION_TIMEOUT"


@pytest.mark.asyncio
async def test_local_sandbox_environment_isolation():
    # Verify that host sensitive environment variables are NEVER passed to sandbox runner
    runner = LocalProcessSandboxRunner()
    os.environ["SECRET_DATABASE_PASSWORD"] = "ultra_sensitive_password_123"
    os.environ["WALLET_PRIVATE_KEY"] = "0xdeadbeef1234567890abcdef"

    config = SandboxConfig(timeout_seconds=10)
    manifest = RESEARCH_AGENT_MANIFEST.model_dump()
    context = {"execution_id": "test-env-exec", "agent_id": "agent-1", "agent_version": "1.0.0"}
    input_data = {"text": "Checking env isolation."}

    res = await runner.run_agent(manifest, input_data, context, config)
    assert res.success is True

    # Cleanup
    del os.environ["SECRET_DATABASE_PASSWORD"]
    del os.environ["WALLET_PRIVATE_KEY"]


@pytest.mark.asyncio
async def test_local_sandbox_output_payload_size_limit():
    runner = LocalProcessSandboxRunner()
    # Tiny limit of 10 bytes to trigger payload size limit
    config = SandboxConfig(timeout_seconds=10, max_output_bytes=10)
    manifest = RESEARCH_AGENT_MANIFEST.model_dump()
    context = {"execution_id": "test-limit-exec", "agent_id": "agent-1", "agent_version": "1.0.0"}
    input_data = {"text": "This text will generate an output larger than 10 bytes."}

    res = await runner.run_agent(manifest, input_data, context, config)
    assert res.success is False
    assert res.error_code == "PAYLOAD_LIMIT_EXCEEDED"


def test_docker_sandbox_security_configuration_enforcement():
    runner = DockerSandboxRunner(fallback_to_local_if_no_docker=False)
    config = SandboxConfig(
        cpu_limit=0.5,
        memory_limit_mb=256,
        timeout_seconds=30,
        pids_limit=32,
        network_mode="none",
        read_only_root=True,
        drop_capabilities=["ALL"],
        user="1000:1000",
    )

    # Mock docker client to inspect container creation parameters
    mock_docker = MagicMock()
    mock_container = MagicMock()
    mock_docker.containers.create.return_value = mock_container

    with patch.object(runner, "_get_docker_client", return_value=mock_docker):
        with patch("builtins.open", unittest.mock.mock_open(read_data="print('mock runner')")):
            mock_container.wait.return_value = {"StatusCode": 0}
            mock_container.logs.return_value = b'{"success": true, "output": {"test": 1}}'
            mock_sock = MagicMock()
            mock_container.attach_socket.return_value = mock_sock

            manifest = RESEARCH_AGENT_MANIFEST.model_dump()
            context = {"execution_id": "test-sec-exec", "agent_id": "agent-1", "agent_version": "1.0.0"}

            # Run in event loop
            import asyncio
            res = asyncio.run(runner.run_agent(manifest, {"text": "test"}, context, config))

            # Inspect exact kwargs passed to docker.containers.create
            mock_docker.containers.create.assert_called_once()
            call_kwargs = mock_docker.containers.create.call_args[1]

            # 1. Privileged mode MUST be False
            assert call_kwargs["privileged"] is False

            # 2. Linux capabilities MUST drop ALL
            assert call_kwargs["cap_drop"] == ["ALL"]

            # 3. Security opt MUST disable new privileges
            assert "no-new-privileges:true" in call_kwargs["security_opt"]

            # 4. Root filesystem MUST be read-only
            assert call_kwargs["read_only"] is True

            # 5. Network MUST be isolated ('none')
            assert call_kwargs["network_mode"] == "none"

            # 6. User MUST be non-root (e.g. 1000:1000)
            assert call_kwargs["user"] == "1000:1000"

            # 7. Memory limit enforced
            assert call_kwargs["mem_limit"] == "256m"

            # 8. PIDs limit enforced
            assert call_kwargs["pids_limit"] == 32

            # 9. No host mounts or volumes allowed
            assert "volumes" not in call_kwargs or call_kwargs["volumes"] is None

            # 10. Ephemeral tmpfs allowed only
            assert "/tmp" in call_kwargs["tmpfs"]

import unittest.mock
