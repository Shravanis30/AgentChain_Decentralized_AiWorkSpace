# Chain Reorganization Detection & Rollback Recovery (Phase 6.2A)

> **CRITICAL RULE:**
> Never permanently finalize an event merely because it was observed once.
> The indexer must detect chain forks, isolate orphaned records, and restore canonical state.

---

## 1. Reorganization Detection Mechanism

During continuous block ingestion, the `BlockTracker` strictly validates the parent-hash relationship:

$$\text{current\_block.parent\_hash} == \text{previous\_canonical\_block.block\_hash}$$

A reorganization is detected when either:
1. The incoming block at height $N$ has a `parent_hash` that differs from the stored `block_hash` of height $N-1$.
2. An incoming block at height $N$ has a different `block_hash` than an already indexed non-orphaned block at height $N$.

---

## 2. Common Ancestor Discovery

When a reorganization is detected, `ReorgHandler.find_common_ancestor()` walks backward from the divergent height:

```text
Fork Detected at Block 11
  Check Block 10: DB hash == RPC hash?  -> YES (Common Ancestor found!)
  Depth = 11 - 10 = 1
```

If the divergence extends deeper, the walkback continues block by block until the canonical fork point is identified.

---

## 3. Data Isolation & Rollback

Once the common ancestor height $A$ is identified:

### 1. Orphan Block Headers (Database Partial Index Compliance)
All blocks with `block_number > A` are updated:
```sql
UPDATE indexed_blocks 
SET is_canonical = FALSE, status = 'ORPHANED', updated_at = NOW() 
WHERE chain_id = :chain_id AND block_number > :ancestor_number AND is_canonical = TRUE;
```
Setting `is_canonical = FALSE` immediately satisfies PostgreSQL partial unique index `uq_indexed_blocks_canonical`, freeing the height for the incoming canonical block while permanently preserving the orphaned block in the database for auditability.

### 2. Invalidate Events
All events with `block_number > A` are marked non-canonical:
```sql
UPDATE blockchain_events 
SET is_canonical = FALSE, status = 'ORPHANED', updated_at = NOW() 
WHERE chain_id = :chain_id AND block_number > :ancestor_number AND is_canonical = TRUE;
```
Setting `is_canonical = FALSE` frees the canonical event constraint `uq_bc_events_canonical_tx_log`, ensuring the new canonical fork events can be indexed without blocking.

### 3. Derived State Rollback & Replay
For any `escrow_id` affected by orphaned events:
1. Re-query all **canonical** events (`is_canonical = TRUE`) for that `escrow_id` in ascending block and log order.
2. Replay the state transitions to calculate the correct on-chain state at ancestor height $A$.
3. If no canonical events remain for that escrow, mark the `EscrowChainState` as `is_canonical = FALSE`.

### 4. Audit Logging & Observability
A permanent audit record is created in `chain_reorganizations`:
- `chain_id`
- `detection_block_number`
- `old_block_hash`
- `new_block_hash`
- `common_ancestor_number`
- `common_ancestor_hash`
- `depth`
- `affected_blocks_count`
- `affected_events_count`
- `status` (`RESOLVED`)
- `detected_at`, `resolved_at`

Metrics emitted:
- `blockchain_reorgs_total`
- `blockchain_reorg_depth`
- `blockchain_block_reorg_conflicts_total`
- `blockchain_canonical_block_replacements_total`
- `blockchain_orphaned_events_total`

---

## 4. Concurrency Safety During Reorganizations

To prevent race conditions when multiple indexer workers or reconciliation tasks encounter the same reorganization simultaneously:
1. A transaction-scoped PostgreSQL advisory lock is acquired on the chain:
   ```sql
   SELECT pg_advisory_xact_lock(hashtext('chain_reorg_' || :chain_id));
   ```
2. The second worker waits for the lock, observes the resolved reorg state, and safely avoids duplicate rollbacks or duplicate inserts.

---

## 5. Re-Indexing Canonical Chain

Following isolation and state rollback:
1. The indexer resumes fetching canonical block headers starting from $A + 1$.
2. Logs on the new canonical fork are ingested and decoded normally.
3. Confirmations and projections advance along the new canonical head.
