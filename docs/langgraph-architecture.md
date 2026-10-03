# LangGraph Multi-Agent Architecture

## 1. Architectural Role & Version

```toml
# backend/pyproject.toml
dependencies = [
    "langgraph>=0.2.76,<0.3.0",
    ...
]
```

### Purpose
LangGraph is leveraged within AgentChain as a **durable state machine and graph orchestrator**. It determines dependency progression, schedules readiness transitions, and manages checkpointed execution state across asynchronous events.

> **"LangGraph determines WHAT should execute and WHEN.  
> The Worker determines HOW agent code executes safely."**

LangGraph **never** executes Agent SDK code directly. All agent invocations are dispatched asynchronously as `AgentExecution` records through the PostgreSQL Transactional Outbox and Redis Streams to isolated sandboxed workers.

---

## 2. Logical Task Identity vs Execution Attempt Identity

In multi-agent DAGs with automatic retry capabilities, task identity must be separated from individual execution attempt identities:

### Logical Task Identity
```text
orch-{orchestration_id}-task-{task_id}
```
- Identifies the permanent, abstract step in the DAG.
- Remains constant across task lifecycle transitions and retries.

### Execution Attempt Idempotency Key
```text
orch-{orchestration_id}-task-{task_id}-attempt-{attempt}
```
- Uniquely binds a specific dispatch attempt (e.g. `attempt = 1`, `attempt = 2`).
- Enforces strict deduplication: if the orchestrator replays a checkpoint, recovers from a crash, or handles duplicate wake-up events for the *same attempt*, the idempotency key resolves to the existing `AgentExecution`. No duplicate child execution is created.
- When a task fails with a retryable error (`attempt < max_attempts`), `task.attempt` increments (e.g. 1 $\to$ 2), yielding a new distinct idempotency key `...-attempt-2`.
- Historical execution records are preserved in `agent_executions` with `task_attempt = attempt`, maintaining full auditability without overwriting past attempts.

---

## 3. State Model

The orchestration state is strongly typed using Python type definitions and Pydantic models:

```python
class OrchestrationState(TypedDict):
    orchestration_id: str
    goal: str
    graph_version: str
    task_states: Dict[str, Dict[str, Any]]
    ready_tasks: List[str]
    running_tasks: List[str]
    completed_tasks: List[str]
    failed_tasks: List[str]
    artifacts: Dict[str, Dict[str, Any]]
    final_result: Optional[Dict[str, Any]]
    error_code: Optional[str]
    error_message: Optional[str]
    state_hash: Optional[str]
```

### Safety Principles
1. **Bounded State**: Checkpoints never store unbounded binary blobs, massive log streams, or raw container outputs. Payloads exceeding `MAX_CHECKPOINT_BYTES` (512 KB) are rejected.
2. **Cryptographic Integrity**: Every state transition computes a canonical SHA-256 `state_hash` stored in the `orchestration_checkpoints` table.
3. **Immutability of Tasks**: Completed tasks are recorded with immutable `output_hash` and cannot be re-executed upon checkpoint restore.

---

## 4. StateGraph Workflow

```mermaid
graph TD
    START([Start / Resume]) --> ScheduleTasks[schedule_tasks]
    
    ScheduleTasks -->|Tasks Dispatched or Running| END_PAUSE([Yield to END / Wait for Workers])
    ScheduleTasks -->|Tasks Ready to Collect| CollectResults[collect_results]
    ScheduleTasks -->|All Tasks Finished| Synthesize[synthesize_and_complete]
    ScheduleTasks -->|Critical Task Failure| FailNode[fail_orchestration]
    
    CollectResults -->|New Dependencies Unblocked| ScheduleTasks
    CollectResults -->|Still Waiting for Running Workers| END_PAUSE
    CollectResults -->|All Tasks Finished| Synthesize
    CollectResults -->|Task Failed (Non-retryable)| FailNode
    
    Synthesize --> END_SUCCESS([END: SUCCEEDED])
    FailNode --> END_FAIL([END: FAILED])
```

### StateGraph Nodes

1. **`schedule_tasks`**:
   - Evaluates task dependency graph.
   - Transitions unblocked tasks from `PENDING` to `READY`.
   - Enforces `MAX_CONCURRENT_TASKS_PER_ORCHESTRATION` bounded concurrency.
   - For each ready task, computes `execution_attempt_idempotency_key`. If an execution already exists for this attempt, reuses it idempotently. Otherwise, creates an `AgentExecution` row and an `execution_outbox` record within an atomic PostgreSQL transaction.
   - Transitions tasks to `RUNNING`.
   - Saves a durable checkpoint to PostgreSQL.

2. **`collect_results`**:
   - Inspects `agent_executions` table in PostgreSQL for child executions.
   - Validates child execution status (`SUCCEEDED`, `FAILED`, `CANCELLED`).
   - If `SUCCEEDED`:
     - Validates canonical `output_hash` and agent output schema.
     - Records structured output in task state.
     - Registers intermediate artifacts in `artifacts` table.
     - Transitions task to `SUCCEEDED`.
   - If `FAILED`:
     - Checks retry budget (`attempt < max_attempts`) and retryability.
     - If retryable: increments `attempt`, transitions task to `READY`, clears `execution_id`, and emits `TASK_RETRYING`.
     - If permanent: transitions task to `FAILED`.
   - Saves durable checkpoint.

3. **`synthesize_and_complete`**:
   - Aggregates outputs from terminal DAG nodes into a unified `result` JSON payload.
   - Marks orchestration status as `SUCCEEDED`.
   - Emits `ORCHESTRATION_SUCCEEDED` SSE event to Redis PubSub.

4. **`fail_orchestration`**:
   - Cancels all active child executions via `AgentExecutionService.cancel_execution`.
   - Marks remaining pending tasks as `CANCELLED`.
   - Records root `error_code` and `error_message`.
   - Marks orchestration status as `FAILED`.
   - Emits `ORCHESTRATION_FAILED` SSE event.

---

## 5. Durable Orchestration Wake-Up & Recovery Architecture (Phase 5.2)

### Two-Tier Wake-Up Pipeline: Low-Latency vs Durable Delivery

AgentChain implements a strict two-tier architecture that separates best-effort low-latency notifications from authoritative durable delivery and reconciliation:

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

### 1. Redis Pub/Sub is Best-Effort Only
Redis Pub/Sub has zero persistence: subscribers disconnected or offline when a message is published permanently miss that event. In AgentChain Phase 5.2, Redis Pub/Sub is retained solely as an optional, opportunistic low-latency signal. Correctness, progress, and safety **never** depend on Pub/Sub delivery.

### 2. PostgreSQL Transactional Outbox is the Delivery Guarantee
Delivery guarantees are anchored in the `orchestration_wakeup_outbox` table in PostgreSQL. When an `AgentExecution` transitions into a terminal state, an outbox record is inserted in the exact same transaction before `COMMIT`. There is zero failure window where an execution is committed as terminal without a durable wake-up record.

### 3. Terminal Execution & Durable Wake-Up Relationship
- **Schema & Identification**: `orchestration_wakeup_outbox` contains `id` (UUID PK), `orchestration_id`, `orchestration_task_id`, `execution_id`, `event_type`, `terminal_status`, `payload` (bounded metadata), `attempts`, `created_at`, `available_at`, `published_at`, `processed_at`, `last_error`.
- **Bounded Payloads**: Outbox records and stream messages never include large agent outputs or artifacts. PostgreSQL remains authoritative for results and hashes.
- **Idempotency**: An execution cannot generate duplicate outbox records upon repeated status evaluation.

### 4. Durable Dispatcher Behavior
- **Polling & Concurrency**: `OrchestrationWakeupDispatcher.dispatch_pending()` queries unpublished records with `SELECT ... FOR UPDATE SKIP LOCKED`, preventing race conditions across multiple dispatcher replicas.
- **Transport**: Dispatches to Redis Streams (`agentchain:orchestration:wakeup_stream`).
- **Retries & Backoff**: Publication failures increment `attempts` and calculate bounded exponential backoff (`available_at = now + min(300s, 2 ** attempts)`).
- **Post-Commit Immediate Dispatch**: Workers attempt immediate dispatch post-commit to minimize latency under normal operations, falling back automatically to the background loop on worker crash or Redis unavailability.

### 5. Recovery Sweep & State Reconciliation
The `OrchestrationReconciliationService` operates completely independently of Redis:
- **Terminal Execution Sweep**: Detects any active (`RUNNING`) orchestration that has a non-terminal task (`PENDING`, `READY`, `RUNNING`) whose linked `AgentExecution` is terminal in PostgreSQL (`SUCCEEDED`, `FAILED`, `TIMED_OUT`, `CANCELLED`).
- **Stuck Outbox Sweep**: Detects any active orchestration with unpublished or unprocessed outbox records exceeding `ORCHESTRATION_RECOVERY_STALE_THRESHOLD_SECONDS` (default: 10s).
- **Advisory Locking**: Acquires `pg_try_advisory_xact_lock(hashtext('orch_advance_' || :id))` before advancing. Competing workers exit cleanly without blocking.
- **Configurable Interval & Tradeoff**: `ORCHESTRATION_RECOVERY_INTERVAL_SECONDS` defaults to 5.0 seconds. This guarantees sub-10s automatic recovery of lost events without creating meaningful database query load.

### 6. Crash Recovery & Liveness Guarantees
- If the orchestrator is offline when a child execution completes, or restarts at any point:
  No manual API call is needed. The recovery sweep automatically detects the terminal execution state in PostgreSQL, runs `advance_orchestration()`, collects child output, and schedules downstream tasks.
- If a consumer crashes after stream delivery but before acknowledgement, the message remains pending and will be re-processed safely; `advance_orchestration()` is strictly idempotent.

