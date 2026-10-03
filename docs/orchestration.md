# AgentChain: Multi-Agent Orchestration Architecture (Phase 5)

## 1. Architectural Philosophy & Separation of Concerns

> **LangGraph determines WHAT should execute and WHEN.**  
> **The Worker determines HOW agent code executes safely.**

LangGraph is strictly an **orchestration layer** and state graph coordinator. It is **NOT** an execution runtime.

The orchestrator must NEVER directly execute an Agent SDK implementation or agent code in-process.

### The Canonical Execution Pipeline

```text
User Goal
   ↓
Orchestrator API (POST /api/v1/orchestrations)
   ↓
LangGraph StateGraph (Deterministic DAG Scheduling)
   ↓
Task / DAG Planning & Dependency Resolution
   ↓
Create AgentExecution records (status: QUEUED)
   ↓
Transactional Outbox (PostgreSQL execution_outbox)
   ↓
Redis Streams (xadd execution_stream)
   ↓
Sandboxed Worker Runtime
   ↓
Isolated Execution Sandbox (Docker / LocalProcess)
   ↓
Agent SDK (runner_entrypoint)
   ↓
Canonicalized Structured Output & SHA-256 Hashing
   ↓
PostgreSQL Finalization (Idempotent Execution Store)
   ↓
Orchestrator Engine Observes Result
   ↓
Advance LangGraph Checkpoint & Schedule Next DAG Nodes
```

---

## 2. Orchestration & Task Models

### Orchestration Model (`orchestrations` table)

| Field | Type | Description |
| :--- | :--- | :--- |
| `id` | `VARCHAR(64)` (PK) | Unique orchestration identifier (`orch-...`) |
| `requested_by` | `VARCHAR(66)` | Ethereum wallet address of requester (SIWE authenticated) |
| `goal` | `TEXT` | High-level user goal / task objective |
| `status` | `VARCHAR(32)` | State: `PLANNING`, `READY`, `RUNNING`, `SUCCEEDED`, `FAILED`, `CANCELLED` |
| `graph_version` | `VARCHAR(32)` | Pinned graph definition version (e.g., `1.0.0`) |
| `result` | `JSONB` | Synthesized final result payload |
| `error_code` | `VARCHAR(64)` | Error code on failure |
| `error_message` | `TEXT` | Descriptive error message |
| `metadata_json` | `JSONB` | Arbitrary bounded orchestration metadata |
| `created_at` / `started_at` / `completed_at` | `TIMESTAMPTZ` | Lifecycle timestamps |

### Orchestration Task Model (`orchestration_tasks` table)

| Field | Type | Description |
| :--- | :--- | :--- |
| `id` | `VARCHAR(64)` (PK) | Unique task identifier (`otask-...`) |
| `orchestration_id` | `VARCHAR(64)` (FK) | References parent orchestration |
| `task_key` | `VARCHAR(64)` | Unique task identifier within DAG (e.g., `research`, `analysis`) |
| `agent_id` | `VARCHAR(64)` | Pinned immutable agent identifier |
| `agent_version` | `VARCHAR(32)` | Pinned immutable semantic version (e.g. `1.0.0`) |
| `input_data` | `JSONB` | Input payload passed to task |
| `input_hash` | `VARCHAR(66)` | Canonical SHA-256 hash of input payload |
| `output_data` | `JSONB` | Validated output payload from worker |
| `output_hash` | `VARCHAR(66)` | Canonical SHA-256 hash of output payload |
| `status` | `VARCHAR(32)` | State: `PENDING`, `READY`, `RUNNING`, `SUCCEEDED`, `FAILED`, `CANCELLED`, `SKIPPED` |
| `dependencies` | `JSONB` | Array of parent `task_key`s that must succeed before this task runs |
| `execution_id` | `VARCHAR(64)` (FK) | Reference to child `agent_executions` record |
| `attempt` / `max_attempts` | `INTEGER` | Current retry attempt and maximum allowed attempts |

---

## 3. DAG Validation & Safety Boundaries

Before an orchestration can be persisted or executed, the incoming task graph is validated by `DAGValidator`:

1. **Cycle Detection**: Kahn's algorithm (topological sorting) detects and rejects cyclic dependencies.
2. **Limit Enforcement**:
   - `MAX_GRAPH_TASKS`: Maximum 20 tasks per orchestration.
   - `MAX_GRAPH_DEPTH`: Maximum 10 dependency levels.
   - `MAX_DEPENDENCIES_PER_TASK`: Maximum 5 parent tasks per node.
   - `MAX_CONCURRENT_TASKS_PER_ORCHESTRATION`: Bounded concurrent task execution (default: 3).
   - `MAX_TASK_INPUT_BYTES`: Maximum 64 KB per input payload.
   - `MAX_TASK_OUTPUT_BYTES`: Maximum 256 KB per output payload.
   - `MAX_ARTIFACT_BYTES`: Maximum 1 MB per artifact payload.
   - `MAX_CHECKPOINT_BYTES`: Maximum 512 KB per LangGraph checkpoint.
3. **Immutable Agent Resolution**:
   - Every task must resolve to a published agent with a valid pinned semantic version.
   - Tasks cannot reference mutable drafts or unpinned versions during execution.

---

## 4. Agent Selection Protocol

The orchestrator utilizes a decoupled agent selection interface:

```python
class AgentSelector(Protocol):
    async def select_agent(
        self,
        task: TaskDefinition,
    ) -> AgentReference:
        ...
```

The default implementation is `DeterministicAgentSelector`:
- Checks `AgentRegistry` for published agents.
- Matches declared `capability` (e.g. `research`, `analysis`, `synthesis`) or explicit `agent_id`.
- Resolves the latest published semantic version and binds it immutably.
- Future LLM-based agent selectors can implement `AgentSelector` without altering graph orchestration infrastructure.

---

## 5. Bounded Concurrency & Parallel Execution

Independent DAG branches execute concurrently without exceeding system capacity:
```text
           ┌──> Task A (Research) ──┐
User Goal ─┤                        ├──> Task C (Synthesis)
           └──> Task B (Market)   ──┘
```

- When both Task A and Task B have empty dependencies, they transition to `READY`.
- The engine dispatches up to `MAX_CONCURRENT_TASKS_PER_ORCHESTRATION` child executions simultaneously.
- If more tasks become ready than allowed by the concurrency bound, excess tasks remain in `READY` status until running tasks succeed and free up worker slots.
- LangGraph pauses cleanly by yielding to `END` while tasks run on external workers, resuming when results are collected.

---

## 6. Failure Semantics & Orchestration Policies

When an individual task fails:
1. **Retryable Classification**:
   - `WORKER_TIMEOUT`, `CONTAINER_START_FAILURE`, `TRANSIENT_INFRA_ERROR` are retryable.
   - `SCHEMA_VALIDATION_ERROR`, `INVALID_INPUT`, `UNAUTHORIZED`, `PERMISSION_DENIED` are non-retryable.
2. **Retry Execution**:
   - If `attempt < max_attempts` and failure is retryable, task resets to `READY` with `attempt += 1` and a new child `agent_execution` is dispatched.
3. **Critical Task Failure**:
   - Once a task exhausts `max_attempts` or suffers a non-retryable error, it transitions to `FAILED`.
   - The default orchestration failure policy immediately fails the entire orchestration, cancels all pending and running sibling tasks, and logs the root error code.

---

## 7. Logical Task Identity vs Execution Attempt Identity (Phase 5.1)

In multi-agent DAGs with automatic retry capabilities, task identity is strictly decoupled from individual execution attempt identities:

### Logical Task Identity
```text
orch-{orchestration_id}-task-{task_id}
```
- Represents the logical node in the orchestration DAG.
- Remains constant throughout all retries and lifecycle transitions.

### Execution Attempt Idempotency Key
```text
orch-{orchestration_id}-task-{task_id}-attempt-{attempt}
```
- Binds a specific execution attempt (e.g. `attempt = 1`, `attempt = 2`).
- Re-dispatching or replaying the same attempt resolves to the existing `AgentExecution` (no duplicate executions).
- Legitimate retries increment `attempt` (e.g. 1 $\to$ 2), dispatching a new distinct execution attempt with its own idempotency key.
- Historical execution records are preserved in `agent_executions` linked to `task_attempt = attempt` for complete auditability.

---

## 8. Durable Orchestration Wake-Up & Recovery Architecture (Phase 5.2)

### Two-Tier Wake-Up Pipeline: Low-Latency vs Durable Delivery

Phase 5.2 closes the delivery-guarantee gap by eliminating reliance on Redis Pub/Sub for correctness:

```text
Worker finalizes AgentExecution
                ↓
    PostgreSQL ATOMIC TRANSACTION
    ├── Terminal execution state (SUCCEEDED / FAILED / TIMED_OUT / CANCELLED)
    └── Durable wake-up outbox record (orchestration_wakeup_outbox)
                │
                ├─────────────────────────────────────────┐
                ▼                                         ▼
   [LOW-LATENCY NOTIFICATION PATH]           [DURABLE DELIVERY & RECOVERY PATH]
         (Best-effort Only)                         (Authoritative Guarantee)
                │                                         │
        Redis Pub/Sub channel                     Durable Outbox Dispatcher
  (orchestration:{id}:wakeup)                             │ (XADD with retries)
                │                                         ▼
                │                               Redis Streams Transport
                │                          (orchestration:wakeup_stream)
                │                                         │
                │                               Stream Consumer Group
                │                            (orchestration:wakeups_group)
                │                                         │
                ├─────────────────────────────────────────┤
                ▼                                         ▼
      handle_execution_completed_wakeup()    Recovery Sweep / Reconciliation
                │                              (Independent of Redis)
                └────────────────────┬────────────────────┘
                                     ▼
                        advance_orchestration(id)
                                     │
                 PostgreSQL Advisory Lock (pg_try_advisory_xact_lock)
                                     │
                 Authoritative DB reload & DAG advancement
                                     │
                        LangGraph Checkpoint Persisted
```

### Core Invariant & Reliability Guarantees

> **Whenever an AgentExecution reaches a terminal state and belongs to an orchestration, the parent orchestration must eventually be advanced even if Redis Pub/Sub delivery is lost, the orchestrator is temporarily offline, or a process crashes at any point after execution finalization.**

1. **Redis Pub/Sub is Best-Effort Only**:
   Pub/Sub is unbuffered and non-durable. Disconnected subscribers miss messages permanently. It is retained solely for immediate, low-latency opportunistic wake-up.
2. **PostgreSQL Outbox is the Delivery Guarantee**:
   The `orchestration_wakeup_outbox` table guarantees that terminal execution state and the wake-up intent commit atomically in PostgreSQL.
3. **Durable Dispatcher**:
   `OrchestrationWakeupDispatcher.dispatch_pending()` reads records using `SELECT ... FOR UPDATE SKIP LOCKED` and publishes to Redis Streams with bounded exponential backoff (`available_at = now + min(300s, 2 ** attempts)`).
4. **Automatic Recovery Sweep / Reconciliation**:
   `OrchestrationReconciliationService` queries PostgreSQL directly to detect active orchestrations whose child executions are terminal but whose DAG task state is lagging, as well as stuck outbox records older than `ORCHESTRATION_RECOVERY_STALE_THRESHOLD_SECONDS` (10s).
5. **Configurable Liveness vs Load Tradeoff**:
   `ORCHESTRATION_RECOVERY_INTERVAL_SECONDS` is set to 5.0 seconds by default. This ensures sub-10 second recovery of any lost notification without generating excessive database polling load.
6. **Concurrency Safety**:
   Every advancement step is protected by a PostgreSQL transaction-scoped advisory lock (`pg_try_advisory_xact_lock(hashtext('orch_advance_' || :id))`). Competing wake-up events or recovery sweeps fail fast without blocking (`acquired == False`), incrementing `orchestration_concurrent_advance_conflict_total`.
7. **Idempotent Advancement**:
   Delivering the same wake-up event multiple times never creates duplicate executions or corrupts DAG checkpoints.

---

## 9. Observability & Telemetry

### Phase 5.2 Metrics

| Metric | Type | Description |
| :--- | :--- | :--- |
| `orchestration_wakeup_outbox_created_total` | Counter | Total durable wake-up outbox records created in PostgreSQL |
| `orchestration_wakeup_dispatch_total` | Counter | Total wake-up records successfully dispatched to Redis Streams |
| `orchestration_wakeup_dispatch_failure_total` | Counter | Total failed dispatch attempts (transient transport errors) |
| `orchestration_wakeup_dispatch_retry_total` | Counter | Total dispatch retry schedules computed |
| `orchestration_wakeup_recovery_total` | Counter | Total orchestrations evaluated by the recovery sweep |
| `orchestration_wakeup_recovery_stale_total` | Counter | Total stale outbox records recovered by the sweep |
| `orchestration_wakeup_reconciliation_total` | Counter | Total reconciliations performed for stuck orchestrations |
| `orchestration_wakeup_total` | Counter | Total wake-up events received by orchestrator |
| `orchestration_wakeup_duplicate_total` | Counter | Duplicate wake-up events received for terminal orchestrations |
| `orchestration_wakeup_failure_total` | Counter | Errors encountered during wake-up processing |
| `orchestration_concurrent_advance_conflict_total` | Counter | Concurrent advancement lock conflicts avoided |


