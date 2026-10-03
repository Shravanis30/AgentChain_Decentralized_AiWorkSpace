# AgentChain Worker Security & Threat Model

## 1. Threat Model & Trust Boundaries

The worker boundary is treated as **hostile**. Third-party developers and automated agents submit code and input parameters that may be adversarial, buggy, or resource-intensive.

```
+-----------------------------------------------------------------------------------+
| Host Environment / Orchestrator Node (Trusted)                                     |
|                                                                                   |
|  +-----------------------------------------------------------------------------+  |
|  | Worker Service Process (High Trust)                                          |  |
|  |  - DB Credentials, Redis Connections, Signing Keys                           |  |
|  +-----------------------------------------------------------------------------+  |
|                                  │ (Strict In-Memory / Stdin Isolation)           |
|                                  ▼                                                |
|  +-----------------------------------------------------------------------------+  |
|  | Sandbox Boundary (Untrusted / Hostile)                                      |  |
|  |  - Read-Only Root Filesystem                                                |  |
|  |  - Network Isolated (network_mode: none)                                    |  |
|  |  - User: 1000:1000 (Non-Root)                                               |  |
|  |  - Capabilities: ALL Dropped                                                |  |
|  |  - No New Privileges Flag Enforced                                          |  |
|  |  - Ephemeral Memory Workspace (tmpfs /tmp)                                  |  |
|  |  - Zero Host Volume Mounts                                                  |  |
|  |  - Zero Docker Socket Exposure                                              |  |
|  +-----------------------------------------------------------------------------+  |
+-----------------------------------------------------------------------------------+
```

## 2. Security Enforcement Matrix

| Threat Category | Attack Vector | Security Control | Implementation |
|---|---|---|---|
| **Privilege Escalation** | `setuid` / Linux capability abuse | Drop all capabilities, disallow new privileges | `cap_drop=["ALL"]`, `security_opt=["no-new-privileges:true"]` |
| **Privileged Mode Breakout** | Docker root escape | Explicit ban on privileged flags | `privileged=False` hardcoded; rejects any manifest with privileged requests |
| **Host System Access** | Arbitrary volume mounts | Complete ban on host filesystem mounts | No volume bindings; `tmpfs={"/tmp": "rw,noexec,nosuid,size=64m"}` only |
| **Container Daemon Control** | Docker socket hijacking | Zero Docker socket exposure | `/var/run/docker.sock` is never mounted or accessible to agent containers |
| **Data Exfiltration** | Network beaconing / C2 callbacks | Complete network isolation | `network_mode="none"` by default |
| **Resource Starvation (DoS)** | Fork bomb / CPU burn / OOM attack | Hard resource quotas per sandbox | `nano_cpus` quota, `mem_limit` quota, `pids_limit=64` |
| **Execution Lockup** | Infinite loops / deadlocks | Enforced timeout termination | `asyncio.wait_for` + SIGKILL / `container.kill()` |
| **Credential Harvesting** | Environment inspection | Strict variable allowlist | Only explicitly allowed variables (`AGENT_EXECUTION_ID`, `AGENT_ID`, `AGENT_VERSION`) passed |
| **Disk Exhaustion** | Bombarding output files | Read-only root filesystem | `read_only=True` root mount |
| **Buffer Exhaustion** | Huge payload outputs | Strict stream payload truncations | Max input: 1MB, Max output: 5MB, Max logs: 100KB |

## 3. Server-Side Identity & Authorization Guarantees

1. **Submission Authorization**: The API Controller validates user role, session authenticity, and verified wallet ownership prior to execution creation.
2. **Schema Validation**: Server-side JSON schema validation executes before entering the queue.
3. **Immutability of Executions**: Once enqueued, inputs and manifest references cannot be modified.
4. **Retrieval Authorization**: Executions are private. Only the execution requester, agent owner, platform admin, or assigned verifiers may read inputs and deliverables.
5. **Cancellation Authorization**: Only the requester, agent owner, or admin can trigger `POST /executions/{id}/cancel`.

---

## 4. Sandbox Production Boundary

The system explicitly defines the security scope of each sandbox runner tier:

```text
LocalProcessSandboxRunner
=
development/test fallback only

DockerSandboxRunner
=
local integration / controlled execution

gVisor/Kata + Kubernetes
=
planned production hostile-code isolation
```

### Critical Isolation Caveat
Standard Docker containerization alone does **NOT** provide production-grade isolation against arbitrary hostile code.
- **Shared Host Kernel:** Docker containers share the host Linux kernel. Any kernel privilege escalation or syscall zero-day compromises the entire host node.
- **Local Dev / Staging:** `DockerSandboxRunner` implements best-practice container-level controls (non-root UID, read-only rootfs, dropped capabilities, no new privileges, isolated network, memory/CPU quotas) suitable for local development and controlled testing.
- **Production Hostile Code:** Multi-tenant execution of arbitrary untrusted agent code requires hypervisor-isolated or user-space virtualized kernels (such as **gVisor** or **Kata Containers**) orchestrated via Kubernetes.
