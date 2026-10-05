# Result Notarization Recovery & Reorganization Architecture (Phase 6.3)

## 1. Lifecycle Failure Boundaries & Recovery

The notarization pipeline is designed to survive crashes at any phase boundary without losing durable operations or introducing false confirmed proofs:

```
[Agent Execution Succeeded]
        │
      (A) Failure before DB notarization insert
        ▼
[ResultNotarization & Intent Inserted Atomically]
        │
      (B) Failure after DB insert
        ▼
[Outbox Entry Created]
        │
      (C/D) Failure after transaction intent / outbox
        ▼
[Redis Stream Dispatched]
        │
      (E) Failure after Redis publish
        ▼
[Relayer Signs & Broadcasts]
        │
      (F) Failure after submission
        ▼
[Mined On-Chain & Receipt Emitted]
        │
      (G/H) Failure after mining / event indexing
        ▼
[Canonical Projection Updated]
        │
      (I) Failure before confirmation (depth < 32)
        ▼
[32-Block Confirmation Reached]
        │
      (J) Failure after confirmation
        ▼
[Reconciliation Service Reconciles]
        │
      (K/L) Failure during reconciliation / duplicate delivery
        ▼
[Durable CONFIRMED Proof]
```

### Boundary Recovery Matrix

| Boundary | Crash Point | Automatic Recovery Behavior | Guarantee |
| :--- | :--- | :--- | :--- |
| **A** | Before DB Insert | Client/system retries `create_notarization()`; no partial state in DB | Clean Retry |
| **B** | After DB Insert | Transaction rollback ensures intent, outbox, and notarization commit together | Atomicity |
| **C/D** | Outbox Created | `BlockchainTxOutboxDispatcher` picks up `PENDING` outbox rows on restart | Durable Dispatch |
| **E** | Dispatched to Redis | Redis stream persistence + consumer groups re-read pending messages | At-least-once Delivery |
| **F** | After Broadcast | `BlockchainRelayer` polls receipts using tracked `tx_hash` | No Duplicate Send |
| **G/H** | After Event Emission | `EventIndexer` starts scanning from last indexed block checkpoint | Complete Log Ingestion |
| **I** | Confirming (< 32 blocks) | `advance_event_confirmations()` continuously increments depth as blocks mine | Monotonic Progress |
| **J** | Confirmed (>= 32 blocks) | Reaches terminal `CONFIRMED`; proof locked | Durable Confirmation |
| **K** | During Reconciliation | Idempotent queries resume from current DB state | Idempotency |
| **L** | Duplicate Delivery | Unique constraints on `(chain_id, execution_id)` and contract `AlreadyNotarized` reject duplicates | Replay Protection |

---

## 2. Reorganization Handling Architecture

### 32-Block Confirmation Rule
- Authoritative confirmation threshold: **32 blocks** on all EVM chains.
- Notarizations remain in `CONFIRMING` until 32 blocks have passed since the event block number.
- At 32+ blocks, the proof transitions to `CONFIRMED`.

### Reorg Invalidation Protocol (< 32 Blocks)
If a fork reorganization replaces blocks:
1. `BlockTracker` detects parent hash mismatch.
2. `ReorgHandler` detects depth and updates affected `IndexedBlock` records to `is_canonical = false`.
3. Affected `BlockchainEvent` records are updated to `is_canonical = false` and `status = ORPHANED`.
4. `NotarizationProjector.reproject_notarization()` transitions the `ResultNotarization` record to `status = REORGED`, `is_canonical = false`, and records `reorged_at`.
5. Read-only verification API immediately reports `REORGED` rather than `VERIFIED`.

### Execution Immutability During Reorg
- **Crucial Invariant**: Even if a blockchain transaction is orphaned by a reorganization, the `AgentExecution.output_hash` in PostgreSQL remains **strictly immutable**.
- The execution output is never automatically altered or wiped.
- A new notarization attempt may be submitted for the canonical chain if required.

### Deep Reorganizations (> 32 Blocks)
- In the event of an anomalous deep reorg exceeding 32 blocks, the system **fails closed**.
- Automated indexing halts on the affected chain, alerting on-call operators for manual inspection and cryptographic consensus verification.
