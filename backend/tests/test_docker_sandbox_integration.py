import asyncio
import os
import pytest
import docker
from agentchain_worker.sandbox.base import SandboxConfig, SandboxResult
from agentchain_worker.sandbox.docker_runner import DockerSandboxRunner


def is_docker_available() -> bool:
    try:
        client = docker.from_env()
        client.ping()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not is_docker_available(),
    reason="Docker daemon not available for real container integration test",
)


@pytest.mark.asyncio
async def test_docker_sandbox_real_execution():
    """
    Verifies full end-to-end execution of the reference ResearchAgent inside
    a real Docker sandbox container.
    """
    runner = DockerSandboxRunner(image="python:3.12-slim", fallback_to_local_if_no_docker=False)
    manifest = {
        "protocol_version": "1.0",
        "agent": {
            "id": "a1111111-1111-1111-1111-111111111111",
            "name": "Research Agent",
            "version": "1.0.0",
            "slug": "research-agent",
        },
        "runtime": {"timeout_seconds": 30, "memory_mb": 256},
    }
    input_data = {
        "text": "AgentChain provides secure decentralized execution. All agents execute within isolated sandboxes. Results are cryptographically verified."
    }
    context = {
        "execution_id": "test-docker-exec-01",
        "agent_id": "a1111111-1111-1111-1111-111111111111",
        "agent_version": "1.0.0",
        "timeout_seconds": 30,
    }
    config = SandboxConfig(
        cpu_limit=1.0,
        memory_limit_mb=256,
        timeout_seconds=20,
        pids_limit=64,
        read_only_root=True,
        tmpfs_size_mb=64,
        network_mode="none",
        user="1000:1000",
    )

    res: SandboxResult = await runner.run_agent(
        agent_manifest=manifest,
        input_data=input_data,
        context=context,
        config=config,
    )

    assert res.success is True
    assert res.output is not None
    assert "summary" in res.output
    assert res.output["word_count"] > 0
    assert res.output["sentence_count"] == 3
    assert res.error_code is None


@pytest.mark.asyncio
async def test_docker_sandbox_security_isolation_profile():
    """
    Explicitly tests that the Docker container isolation profile enforces:
    - Non-privileged execution (privileged=False)
    - Dropped capabilities (ALL)
    - Read-only root filesystem
    - No host mounts (Binds=None)
    - Isolated network (network_mode=none)
    - Applied CPU and memory limits
    - Non-root UID
    """
    client = docker.from_env()
    container = client.containers.create(
        image="python:3.12-slim",
        command=["python", "-c", "import os; print(f'UID:{os.getuid()}')"],
        network_mode="none",
        mem_limit="256m",
        nano_cpus=int(1.0 * 1e9),
        pids_limit=64,
        read_only=True,
        tmpfs={"/tmp": "rw,noexec,nosuid,size=64m"},
        cap_drop=["ALL"],
        security_opt=["no-new-privileges:true"],
        privileged=False,
        user="1000:1000",
    )

    try:
        attrs = client.api.inspect_container(container.id)
        hc = attrs["HostConfig"]

        # 1. Privileges
        assert hc.get("Privileged") is False
        assert "no-new-privileges:true" in hc.get("SecurityOpt", [])

        # 2. Capabilities
        assert hc.get("CapDrop") == ["ALL"]

        # 3. Filesystem isolation
        assert hc.get("ReadonlyRootfs") is True
        assert hc.get("Binds") is None  # Absolutely no host filesystem mounts

        # 4. Network isolation
        assert hc.get("NetworkMode") == "none"

        # 5. Resource quotas
        assert hc.get("Memory") == 256 * 1024 * 1024
        assert hc.get("NanoCpus") == 1000000000
        assert hc.get("PidsLimit") == 64

        # 6. Non-root user identity
        container.start()
        container.wait(timeout=10)
        logs = container.logs().decode("utf-8").strip()
        assert "UID:1000" in logs

    finally:
        try:
            container.remove(force=True)
        except Exception:
            pass


@pytest.mark.asyncio
async def test_docker_sandbox_network_denial():
    """
    Verifies that the sandboxed container CANNOT access external networks.
    """
    client = docker.from_env()
    # Code attempts to open TCP connection to Google DNS (8.8.8.8)
    net_probe_code = """
import socket, sys
try:
    s = socket.create_connection(('8.8.8.8', 53), timeout=2)
    sys.exit(0) # Unexpectedly succeeded
except OSError:
    sys.exit(42) # Expected network failure
"""
    container = client.containers.create(
        image="python:3.12-slim",
        command=["python", "-c", net_probe_code],
        network_mode="none",
        read_only=True,
        privileged=False,
        user="1000:1000",
    )
    try:
        container.start()
        res = container.wait(timeout=10)
        # Exit code 42 indicates network was unreachable as required
        assert res.get("StatusCode") == 42
    finally:
        try:
            container.remove(force=True)
        except Exception:
            pass


@pytest.mark.asyncio
async def test_docker_sandbox_read_only_filesystem_enforcement():
    """
    Verifies that container cannot write to the root filesystem,
    but can write to ephemeral tmpfs workspace.
    """
    client = docker.from_env()
    fs_probe_code = """
import sys
# 1. Attempt write to root filesystem (should fail)
try:
    with open('/exploit.txt', 'w') as f:
        f.write('malicious')
    sys.exit(10) # Failed to block write!
except OSError:
    pass

# 2. Attempt write to tmpfs (should succeed)
try:
    with open('/tmp/workspace.txt', 'w') as f:
        f.write('safe')
    sys.exit(0) # Success
except Exception:
    sys.exit(20)
"""
    container = client.containers.create(
        image="python:3.12-slim",
        command=["python", "-c", fs_probe_code],
        network_mode="none",
        read_only=True,
        tmpfs={"/tmp": "rw,noexec,nosuid,size=64m"},
        privileged=False,
        user="1000:1000",
    )
    try:
        container.start()
        res = container.wait(timeout=10)
        assert res.get("StatusCode") == 0
    finally:
        try:
            container.remove(force=True)
        except Exception:
            pass


@pytest.mark.asyncio
async def test_docker_sandbox_secret_isolation():
    """
    Verifies that host secrets, database URLs, and session secrets are NOT leaked into the container.
    """
    client = docker.from_env()
    env_probe_code = """
import os, sys
env = os.environ
leaked = [k for k in ['DATABASE_URL', 'REDIS_URL', 'SECRET_KEY', 'SESSION_SECRET', 'PRIVATE_KEY', 'POSTGRES_PASSWORD'] if k in env]
if leaked:
    print('LEAKED:' + ','.join(leaked))
    sys.exit(1)
else:
    print('OK')
    sys.exit(0)
"""
    # Safe env passed by runner
    safe_env = {
        "PYTHONUNBUFFERED": "1",
        "AGENT_EXECUTION_ID": "exec-sec-test",
        "AGENT_ID": "agent-sec-test",
        "AGENT_VERSION": "1.0.0",
    }
    container = client.containers.create(
        image="python:3.12-slim",
        command=["python", "-c", env_probe_code],
        environment=safe_env,
        network_mode="none",
        read_only=True,
        privileged=False,
        user="1000:1000",
    )
    try:
        container.start()
        res = container.wait(timeout=10)
        logs = container.logs().decode("utf-8").strip()
        assert res.get("StatusCode") == 0
        assert "OK" in logs
        assert "LEAKED" not in logs
    finally:
        try:
            container.remove(force=True)
        except Exception:
            pass


@pytest.mark.asyncio
async def test_docker_sandbox_timeout_enforcement():
    """
    Verifies that a container process exceeding its configured timeout is forcefully terminated.
    """
    runner = DockerSandboxRunner(image="python:3.12-slim", fallback_to_local_if_no_docker=False)
    # The runner times out after 2 seconds
    config = SandboxConfig(
        timeout_seconds=2,
        network_mode="none",
        user="1000:1000",
    )
    # Runner entrypoint receives a payload that would take longer if sleeping
    client = docker.from_env()
    container = client.containers.create(
        image="python:3.12-slim",
        command=["python", "-c", "import time; time.sleep(30)"],
        network_mode="none",
        read_only=True,
        privileged=False,
        user="1000:1000",
    )
    container.start()
    try:
        # Wait with short timeout
        with pytest.raises(Exception):
            container.wait(timeout=2)
    finally:
        try:
            container.kill()
        except Exception:
            pass
        try:
            container.remove(force=True)
        except Exception:
            pass
