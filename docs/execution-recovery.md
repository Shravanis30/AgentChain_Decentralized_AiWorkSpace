# AgentChain Execution Failure Recovery & Resilience

## 1. Failure Scenarios & Recovery Matrix

| Failure Mode | Detection Mechanism | Recovery Action | Target State |
|---|---|---|---|
| **Worker Crash Before Execution** | Job remains unacknowledged in consumer group | Other active workers claim job via consumer group | `QUEUED` -> `RUNNING` on new worker |
| **Worker Crash During Execution** | Stale sweeper scans `XPENDING` where `idle >= 60s` | Surviving worker reclaims job via `XCLAIM` | Handled & retried up to `max_attempts` |
| **Sandbox Execution Timeout** | `asyncio.wait_for` / Docker container timeout | Container process killed (`SIGKILL`), output truncated | `TIMED_OUT` (Non-retryable) |
| **Malformed Sandbox Output** | JSON decoding / schema check failure | Caught by worker, error recorded in DB & audit | `FAILED` with `MALFORMED_SANDBOX_OUTPUT` |
| **Transient Container / Host Error** | Docker daemon glitch / temporary connection drop | Requeued with exponential backoff delay | `QUEUED` -> Retried up to 3 times |
| **Redis Restart** | Redis recovers from AOF log (`appendonly yes`) | Stream restored, unacked jobs preserved | Work continues automatically |
| **API Server Restart** | Persistent PostgreSQL state | Executions remain queryable, workers continue | Zero data loss |

## 2. Stale Worker Lease & Heartbeat Sweeper

Workers publish a heartbeat every 10 seconds to `agentchain:workers:{worker_id}` with a 30-second TTL.

When a worker crashes:
1. Its heartbeat key expires in Redis within 30 seconds.
2. The periodic stale recovery loop scans the Pending Entries List via `XPENDING_RANGE`.
3. If an in-flight message has been pending longer than `stale_execution_threshold_seconds` (default 60s), a healthy worker invokes `XCLAIM` to take ownership of the task.
4. The execution is processed to completion or retried.

## 3. Retry Classification & Dead-Letter Routing

Failures are partitioned strictly into **Transient** (retried) vs. **Permanent** (fatal):

### Transient Errors (Retried with Exponential Backoff):
* `WORKER_LOST`
* `INFRASTRUCTURE_ERROR`
* `CONTAINER_START_FAILURE`
* `DOCKER_DAEMON_ERROR`
* `TEMPORARY_QUEUE_FAILURE`
* `DATABASE_CONNECTION_ERROR`
* `REDIS_CONNECTION_ERROR`
* `HOST_RESOURCE_EXHAUSTED`

### Permanent Errors (Terminal — NEVER retried):
* `INPUT_VALIDATION_ERROR`
* `OUTPUT_VALIDATION_ERROR`
* `AGENT_LOGIC_ERROR`
* `AGENT_EXECUTION_ERROR`
* `EXECUTION_TIMEOUT`
* `EXECUTION_CANCELLED`
* `AUTHORIZATION_FAILURE`
* `POLICY_VIOLATION`
* `MALFORMED_MANIFEST`

When a transient failure exceeds `max_attempts` (default 3), the worker invokes `dead_letter_execution`, moving the message to `agentchain:dead_letter:agent_executions` and setting the execution status to `FAILED`.
