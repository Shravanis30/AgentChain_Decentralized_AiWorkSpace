from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class SandboxConfig:
    cpu_limit: float = 1.0
    memory_limit_mb: int = 512
    timeout_seconds: int = 300
    pids_limit: int = 64
    read_only_root: bool = True
    tmpfs_size_mb: int = 64
    drop_capabilities: list[str] = field(default_factory=lambda: ["ALL"])
    network_mode: str = "none"
    user: str = "1000:1000"
    env_allowlist: dict[str, str] = field(default_factory=dict)
    max_output_bytes: int = 5 * 1024 * 1024  # 5MB
    max_log_bytes: int = 100 * 1024  # 100KB


@dataclass
class SandboxResult:
    success: bool
    output: dict[str, Any] | None = None
    error_code: str | None = None
    error_message: str | None = None
    execution_time_ms: int = 0
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False
    cancelled: bool = False


class SandboxRunner(ABC):
    @abstractmethod
    async def run_agent(
        self,
        agent_manifest: dict[str, Any],
        input_data: dict[str, Any],
        context: dict[str, Any],
        config: SandboxConfig,
    ) -> SandboxResult:
        """
        Execute an agent inside an isolated sandbox boundary according to config limits.
        """
        pass
