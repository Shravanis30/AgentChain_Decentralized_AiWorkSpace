# Multi-Agent Orchestration: Crash Recovery & Resilience Matrix

This document details the recovery mechanisms and verified behaviors across all failure modes defined in the Phase 5 and Phase 5.1 specifications.

---

## 1. Retry Identity vs Idempotency Identity (Phase 5.1)

In multi-agent DAGs with automatic retry capabilities, task identity is strictly decoupled from individual execution attempt identities:

### Logical Task Identity
```text
orch-{orchestration_id}-task-{task_id}
```
- Identifies the logical DAG node.
- Remains constant across retries and task transitions.

### Execution Attempt Idempotency Key
```text
orch-{orchestration_id}-task-{task_id}-attempt-{attempt}
```
- Identifies the specific execution dispatch attempt.
- Replaying or re-scheduling the same attempt reuses the existing `AgentExecution`.
- Legitimate retries increment `attempt` (e.g. 1 $\to$ 2), creating a new distinct `AgentExecution` without overwriting historical attempts.

---

## 2. Phase 5.1 Retry Resilience Matrix (Tests A through F)

All 6 retry identity scenarios are implemented and automated in `backend/tests/test_orchestration_hardening.py`:

| Test | Scenario | Verified Invariant | Status |
| :--- | :--- | :--- | :---: |
| **Test A** | Same task + same attempt scheduled twice | Exactly one `AgentExecution` is created; second schedule reuses it. | **PASS** |
| **Test B** | Task retry after retryable failure | Attempt 1 (`...-attempt-1`) fails; attempt 2 (`...-attempt-2`) creates a new distinct `AgentExecution`. | **PASS** |
| **Test C** | Retry attempt 2 scheduled twice | Exactly one attempt-2 execution exists; duplicate attempt 2 schedule reuses it. | **PASS** |
| **Test D** | Historical attempt queryability | Attempt 1 and attempt 2 remain separately queryable in PostgreSQL (`task_attempt` column). | **PASS** |
| **Test E** | Checkpoint replay attempt safety | Replaying checkpoint while task is RUNNING does not increment attempt or create extra executions. | **PASS** |
| **Test F** | Concurrent scheduler calls | Simultaneous scheduler calls (`asyncio.gather`) produce exactly one execution attempt without race conditions. | **PASS** |

---

## 3. Phase 5.1 Orchestration Wake-Up & Crash Scenarios (Cases 1 through 5)

All 5 crash and wake-up scenarios are implemented and automated in `backend/tests/test_orchestration_hardening.py`:

| Case | Scenario | Verified Invariant | Status |
| :--- | :--- | :--- | :---: |
| **Case 1** | Crash after exec creation before checkpoint | Orchestrator restarts, detects existing execution via idempotency key, binds it, and creates no duplicate. | **PASS** |
| **Case 2** | Child execution completes while offline | Completion status is durably committed in PostgreSQL. On restart, orchestrator discovers completion and advances DAG. | **PASS** |
| **Case 3** | Completion event lost (Redis drop) | Durable PostgreSQL state allows recovery sweeps / subsequent calls to advance the workflow without losing progress. | **PASS** |
| **Case 4** | Duplicate completion events | Repeated delivery of identical completion events safely advances idempotently without duplicating tasks or checkpoints. | **PASS** |
| **Case 5** | Simultaneous wake-up events | Two orchestrator processes receive event simultaneously. PostgreSQL advisory lock (`pg_try_advisory_xact_lock`) ensures exactly one authoritative advancement; competitor exits safely. | **PASS** |

---

## 4. Phase 5 Core Recovery Scenarios (Cases A through O)

Automated in `backend/tests/test_orchestration_recovery.py`:

- **Case A**: Crash before child execution creation $\to$ recovered cleanly from checkpoint (**PASS**)
- **Case B**: Crash after child execution creation before checkpoint $\to$ idempotent reuse (**PASS**)
- **Case C**: Orchestrator restart after task completion $\to$ advances to next task (**PASS**)
- **Case D**: Duplicate scheduling of same DAG task $\to$ idempotent (**PASS**)
- **Case E**: Worker duplicate delivery $\to$ idempotent PostgreSQL finalization (**PASS**)
- **Case F & G**: Task retry and attempt exhaustion $\to$ transitions to FAILED (**PASS**)
- **Case H**: Branch failure $\to$ fails orchestration and cancels siblings (**PASS**)
- **Case I**: Bounded concurrent execution of independent branches (**PASS**)
- **Case J & K**: Cancellation propagation to pending and running executions (**PASS**)
- **Case L**: Checkpoint restoration integrity with SHA-256 verification (**PASS**)
- **Case M**: Graph cycle rejection via Kahn's algorithm (**PASS**)
- **Case N**: Graph size limit rejection prior to execution (**PASS**)
- **Case O**: Cross-user orchestration access rejection (**PASS**)

---

## 5. Phase 5.2 Durable Wake-Up & Recovery Matrix (Tests A through R)

Automated in `backend/tests/test_durable_wakeups.py`:

| Category | Test | Scenario | Verified Invariant | Status |
| :--- | :--- | :--- | :--- | :---: |
| **Durable Delivery** | **Test A** | Terminal execution creates durable wake-up atomically | `AgentExecution` terminal status and `OrchestrationWakeupOutbox` row commit in the exact same DB transaction. | **PASS** |
| | **Test B** | Dispatcher restart does not lose pending wake-up | Pending outbox records survive process termination and are reliably published on restart. | **PASS** |
| | **Test C** | Publication failure causes retry | Transient transport failure increments attempts, calculates exponential backoff, and keeps entry available for retry. | **PASS** |
| | **Test D** | Successful publication marks wake-up appropriately | Publication sets `published_at`, clears `last_error`, and increments dispatch metrics. | **PASS** |
| | **Test E** | Duplicate dispatcher delivery is idempotent | Repeated publish calls on an already-published record safely no-op. | **PASS** |
| **Lost Notification** | **Test F** | Redis Pub/Sub notification completely lost | Suppressing or dropping Pub/Sub notifications leaves durable PostgreSQL state intact. | **PASS** |
| | **Test G** | Progresses automatically through recovery | Background reconciliation sweep detects completed child execution and advances DAG without manual API calls. | **PASS** |
| | **Test H** | Orchestrator offline when execution completes | Terminal state and outbox record commit durably in PostgreSQL while orchestrator is absent. | **PASS** |
| | **Test I** | Orchestrator restarts after execution completion | Fresh orchestrator instance starts up and reconciliation automatically recovers and progresses DAG. | **PASS** |
| **DB Crash Boundaries** | **Test J** | Transactional atomicity on rollback | Rolling back execution finalization ensures neither terminal status nor outbox record persists. | **PASS** |
| | **Test K** | Crash during wake-up dispatch | Mid-dispatch crash releases lock; restarted dispatcher re-claims via `FOR UPDATE SKIP LOCKED`. | **PASS** |
| | **Test L** | Crash after publication before ack | Stream consumer redelivery is handled safely and idempotently by `advance_orchestration()`. | **PASS** |
| **Concurrency** | **Test M** | Recovery sweep and wake-up event race | Simultaneous wake-up and recovery race on `pg_try_advisory_xact_lock`; exactly one advances, other yields cleanly. | **PASS** |
| | **Test N** | Two recovery workers race | Concurrent recovery sweeps for the same orchestration execute safely without duplicate steps. | **PASS** |
| | **Test O** | Recovery worker vs API-triggered advancement | Concurrent API call and recovery worker advance safely under advisory locking. | **PASS** |
| **End-to-End** | **Test P** | Research → Analysis → Synthesis completes | Full 3-task pipeline finishes with durable outbox and stream dispatch active. | **PASS** |
| | **Test Q** | Retry attempt 1 → attempt 2 identity | Attempt 1 failure creates attempt-1 outbox; attempt 2 schedules with distinct attempt key and succeeds. | **PASS** |
| | **Test R** | Duplicate completion events remain harmless | 5 consecutive wake-up events for the same task execute idempotently without duplicating child executions. | **PASS** |

