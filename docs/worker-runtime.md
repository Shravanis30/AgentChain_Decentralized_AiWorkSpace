# AgentChain Worker Runtime Specification

## 1. Overview & Architectural Role

In AgentChain Phase 4, agent execution is decoupled from the synchronous HTTP request-response cycle into a distributed, asynchronous worker runtime. The API process acts strictly as an execution controller (handling authentication, authorization, quota validation, canonical hashing, execution persistence, and queue submission).

```
Client ──> API Controller ──> PostgreSQL Execution Record (QUEUED)
                                 │
                                 ├──> Redis Stream (agent_executions)
                                 │       │
                                 │       ▼
                                 └──> Worker Service (claim_execution)
                                         │
                                         ▼
                                     SandboxRunner (Docker / Local Process)
                                         │
                                         ▼
                                     Agent Execution Boundary (SDK)
                                         │
                                         ├──> Canonical Output Hash (SHA-256)
                                         ├──> State Machine (SUCCEEDED / FAILED / TIMED_OUT)
                                         ├──> Audit Event Log
                                         └──> Redis SSE Stream & XACK
```

## 2. Worker Service Responsibilities

1. **Job Consumption**: Consumes queued execution jobs via Redis Streams consumer groups (`XREADGROUP`).
2. **Payload & Identity Verification**: Never trusts queue metadata alone; re-validates execution state against authoritative PostgreSQL records.
3. **Agent Resolution**: Resolves published version, manifest, and runtime configuration server-side.
4. **Context Creation**: Injects an immutable `AgentContext` with execution ID, agent version, and enforced execution timeouts.
5. **Sandbox Isolation**: Dispatches execution to a `SandboxRunner` abstraction (LocalProcess or Docker).
6. **Limit Enforcement**: Strictly enforces CPU, memory, execution duration, process count, and payload limits.
7. **Output & Hash Capture**: Computes deterministic RFC 8785 canonical SHA-256 hashes of output deliverables.
8. **State Machine Persistence**: Transitions DB record through explicit server-controlled states (`QUEUED -> RUNNING -> SUCCEEDED | FAILED | TIMED_OUT | CANCELLED`).
9. **Real-time Event Emission**: Publishes live lifecycle events to Redis channels for Server-Sent Events (SSE) clients.
10. **Queue Acknowledgement**: Acknowledges message (`XACK`) upon terminal execution or transient requeueing.

## 3. Worker Configuration Parameters

| Parameter | Env Variable | Default | Description |
|---|---|---|---|
| `worker_id` | `WORKER_ID` | `worker-{hostname}-{uuid}` | Unique cluster identifier for the worker node |
| `queue_name` | `WORKER_QUEUE_NAME` | `agent_executions` | Redis stream name for execution jobs |
| `concurrency` | `WORKER_CONCURRENCY` | `2` | Number of parallel worker threads / consumers |
| `poll_block_ms` | `WORKER_POLL_BLOCK_MS` | `2000` | Block duration for `XREADGROUP` in milliseconds |
| `heartbeat_interval_seconds` | `WORKER_HEARTBEAT_INTERVAL_SECONDS` | `10` | Frequency of worker heartbeat publication |
| `stale_execution_threshold_seconds` | `WORKER_STALE_EXECUTION_THRESHOLD_SECONDS` | `60` | Minimum idle time before claiming orphaned jobs |
| `sandbox_type` | `WORKER_SANDBOX_TYPE` | `docker` | Sandbox runner implementation: `docker` or `local` |
| `sandbox_image` | `WORKER_SANDBOX_IMAGE` | `python:3.12-slim` | Container image used for sandbox execution |

## 4. Production Migration Path: Docker to Kubernetes & gVisor

> [!WARNING]
> Standard Docker containerization alone does NOT provide true hardware-grade or kernel-grade multi-tenant isolation against arbitrary adversarial code. Container breakout vulnerabilities, shared Linux kernel vulnerabilities, and side-channel attacks exist in standard Linux containers.

For production multi-tenant agent execution, AgentChain implements a replaceable `SandboxRunner` architecture:

```
                          ┌───────────────────────────┐
                          │   SandboxRunner (ABC)     │
                          └─────────────┬─────────────┘
                                        │
           ┌────────────────────────────┼───────────────────────────┐
           ▼                            ▼                           ▼
┌──────────────────────┐    ┌──────────────────────┐    ┌──────────────────────┐
│  LocalProcessRunner  │    │  DockerSandboxRunner │    │   gVisorKubeRunner   │
│  (Unit Tests/Local)  │    │  (Local Dev / Stage) │    │   (Production Cloud) │
└──────────────────────┘    └──────────────────────┘    └──────────────────────┘
```

### Production Sandbox Architecture:
1. **gVisor (runsc) / Kata Containers**: An application kernel written in Go that provides a distinct virtualized kernel boundary per sandbox, intercepting and virtualizing all guest application syscalls.
2. **Kubernetes Ephemeral Jobs**: Each execution maps to a Kubernetes Job scheduled with `runtimeClassName: gvisor`.
3. **Network Policies**: Default Deny-All egress and ingress policies (`Cilium` or standard K8s NetworkPolicy) preventing cluster metadata API (`169.254.169.254`) and internal service network discovery.
4. **Ephemeral Volume Scratches**: Storage backed exclusively by `emptyDir: { medium: "Memory", sizeLimit: "64Mi" }`.
