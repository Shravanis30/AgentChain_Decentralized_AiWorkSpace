# Transactional Execution Dispatch Architecture

## 1. Problem Statement: The Dual-Write Gap

In asynchronous task execution architectures, dispatching an event to a queue (such as Redis Streams) immediately after committing a database transaction creates an inherent dual-write consistency gap:

```
Step 1: PostgreSQL COMMIT (execution = QUEUED)
Step 2: Network / Redis XADD failure OR Process crash before Redis call
Result: The execution record is committed as QUEUED in PostgreSQL,
        but never reaches a queue. No worker will ever execute the job.
```

Conversely, attempting queue submission *before* database commit creates the opposite failure: Redis receives a message for an uncommitted transaction; if the database transaction subsequently rolls back or times out, the worker attempts to process an execution that does not exist in PostgreSQL.

## 2. Solution: PostgreSQL Transactional Outbox Pattern

To eliminate the dual-write failure window and guarantee that no queued execution is ever lost, AgentChain implements the **Transactional Outbox Pattern**.

### Architecture Overview

```
Client
  ↓ POST /api/v1/agents/{id}/execute
Execution Controller
  │
  ├─► [BEGIN DB TRANSACTION]
  │     1. Validate input schema & compute canonical RFC 8785 SHA-256 hash
  │     2. Check idempotency key
  │     3. INSERT INTO agent_executions (status = 'QUEUED', ...)
  │     4. INSERT INTO execution_outbox (event_type = 'EXECUTION_ENQUEUED', published_at = NULL, ...)
  │     5. INSERT INTO audit_logs (event_type = 'AGENT_EXECUTION_QUEUED', ...)
  │   [COMMIT DB TRANSACTION]
  │
  ├─► (Post-Commit Dispatcher)
  │     Attempts immediate delivery: OutboxDispatcher.process_outbox_entry_by_id(...)
  │     - Enqueues job to Redis Stream ('agent_executions')
  │     - On success: UPDATE execution_outbox SET published_at = now()
  │
  └─► Return ExecutionResponse (status = QUEUED)
        │
        ▼ (If Redis down or process crashed)
  Background Outbox Dispatcher Sweeper (Worker & Background loop)
        - Periodically polls: WHERE published_at IS NULL AND available_at <= now()
        - Retries publishing with exponential backoff
        - Guarantees at-least-once delivery to Redis Streams
```

---

## 3. Database Schema: `execution_outbox`

Created via Alembic migration `005_outbox_and_leases.py`:

```sql
CREATE TABLE execution_outbox (
    id UUID PRIMARY KEY,
    execution_id UUID NOT NULL REFERENCES agent_executions(id) ON DELETE CASCADE,
    event_type VARCHAR(64) NOT NULL,
    payload JSONB NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    published_at TIMESTAMP WITH TIME ZONE NULL,
    attempts INTEGER DEFAULT 0 NOT NULL,
    last_error TEXT NULL,
    available_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL
);

CREATE INDEX idx_outbox_execution_id ON execution_outbox(execution_id);
CREATE INDEX idx_outbox_pending_dispatch ON execution_outbox(published_at, available_at);
```

### Operational Metadata Retention
Outbox records are **not deleted** immediately upon publishing. `published_at` is set to the completion timestamp, and the queue message ID is recorded on `agent_executions.queue_message_id`. This retains valuable operational and audit metadata for performance analysis, deduplication tracing, and debugging.

---

## 4. Delivery Semantics & Failure Recovery

### At-Least-Once Delivery
Redis Streams and the Transactional Outbox guarantee **At-Least-Once Delivery**:
- A temporary Redis outage causes the dispatcher to retry with exponential backoff:
  $$\text{backoff\_seconds} = \min(300, 2^{\min(\text{attempts}, 8)})$$
- If the dispatcher successfully publishes to Redis but crashes before updating `published_at`, the record may be redelivered.
- The worker runtime guarantees **Idempotent Finalization**: duplicate deliveries of an execution that is already terminal (`SUCCEEDED`, `FAILED`, `TIMED_OUT`, `CANCELLED`) are safely acknowledged and skipped without re-running or overwriting results.

### Lease Management
To prevent split-brain execution between workers:
- Workers acquire a lease on `agent_executions`: `lease_owner`, `lease_acquired_at`, `lease_expires_at`.
- Stale workers that wake up after their lease was reclaimed cannot overwrite the active worker's terminal result.

---

## 5. Observability & Metrics

The outbox dispatcher exports Prometheus-compatible metrics through `/metrics`:

| Metric Name | Type | Description |
| :--- | :--- | :--- |
| `outbox_pending` | Gauge | Count of outbox events awaiting queue delivery (`published_at IS NULL`) |
| `outbox_publish_success_total` | Counter | Total outbox events successfully published to Redis Streams |
| `outbox_publish_failure_total` | Counter | Total publication attempt failures requiring retry |
| `outbox_publish_latency_seconds` | Histogram | Latency distribution of queue publishing operations |
| `execution_duplicate_delivery_total` | Counter | Redelivered jobs safely ignored by worker runtime |
| `execution_stale_lease_total` | Counter | Attempts by stale workers to mutate an execution rejected |
| `execution_finalization_conflict_total`| Counter | Terminal status overwrite conflicts prevented |
