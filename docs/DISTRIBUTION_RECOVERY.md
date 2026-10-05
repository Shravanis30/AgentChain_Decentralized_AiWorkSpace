# Revenue Distribution Recovery & Reorg Runbook — AgentChain Phase 6.2C

## 1. Crash Recovery Semantics Across Pipeline Stages

The transaction and distribution pipeline consists of the following discrete stages:

```
[Settlement Authorized]
        │
        ▼
(A) Create Distribution DB Record
        │
        ▼
(B) Create BlockchainTransactionIntent
        │
        ▼
(C) Write to BlockchainTxOutbox
        │
        ▼
(D) Publish to Redis Stream
        │
        ▼
(E) Relayer Picks Up Intent & Assigns Nonce
        │
        ▼
(F) RPC Broadcast
        │
        ▼
(G) Transaction Mined On-Chain
        │
        ▼
(H) Indexer Detects Canonical Event
        │
        ▼
(I) Confirmation Progression (1 → 32 blocks)
        │
        ▼
(J) DistributionReconciliationService Confirms
```

### Recovery Matrix

| Failure Point | System State | Recovery Mechanism | Duplicate Risk |
|---|---|---|---|
| **Crash at (A)** (before DB insert) | Settlement authorized, no distribution | Client or task runner retries request; `create_distribution` inserts record | None |
| **Crash at (B)** (after DB insert, before intent) | Distribution in `PENDING` / `AUTHORIZED` | Distribution reconciliation or retry discovers existing record via `distribution_key` and creates/links intent | None (DB unique key enforces 1 record) |
| **Crash at (C)** (after intent, before outbox) | Intent exists, outbox pending | `TxOutboxService` periodic poll discovers un-enqueued intents and enqueues to outbox | None |
| **Crash at (D)** (outbox written, Redis down) | Outbox item marked pending | `Relayer` outbox scanner reads directly from PostgreSQL outbox table as durable fallback | None |
| **Crash at (E)** (relayer restarts) | Nonce reserved in PostgreSQL | Relayer resumes uncompleted attempts; `nonce_manager` checks on-chain nonce before re-submission | None |
| **Crash at (F)** (after broadcast, before mined) | Transaction in mempool | Relayer tracks transaction hash via `eth_getTransactionReceipt`; replacement with higher gas if stalled | None (same nonce) |
| **Crash at (G)** (mined, indexer down) | On-chain state completed | When indexer restarts, it resumes from `last_indexed_block` and catches up to chain head | None (idempotent event projection) |
| **Crash at (H)** (event indexed, reconciliation down) | Event in `blockchain_events` | `DistributionReconciliationService` runs periodic sweep over `SUBMITTED` distributions | None |

---

## 2. On-Chain Replay Protection

In `RevenueDistributor.sol`:
```solidity
bytes32 distributionId = keccak256(
    abi.encode(block.chainid, address(this), escrowId, grossAmount, DISTRIBUTION_VERSION)
);
if (_executedDistributions[distributionId]) {
    revert DistributionAlreadyProcessed(distributionId);
}
_executedDistributions[distributionId] = true;
```

Any second attempt to release the same escrow or invoke distribution with identical parameters will immediately revert on-chain.

---

## 3. Reorg Handling & 32-Block Window

### Authoritative 32-Block Window
The system maintains a mandatory 32-block finality depth for Base Sepolia and Base Mainnet.

1. **Reorganization Detection:**
   The `ReorgHandler` continuously compares the parent block hash of incoming blocks against stored canonical blocks. If a fork is detected:
   - Fork point is identified.
   - Blocks on the orphaned fork are updated to `BlockStatus.ORPHANED`.
   - Associated events are updated to `EventStatus.ORPHANED`.

2. **Distribution Status Invalidation:**
   - If a `CONFIRMED` distribution's underlying event is marked `ORPHANED`:
     - Status transitions from `CONFIRMED` $\rightarrow$ `REORGED`.
     - `DistributionHistory` logs the reorganization reason and orphaned block number.
     - `distribution_reorged_total` Prometheus counter increments.
   - The system **does not** automatically re-submit a distribution transaction while the reorg is being reconciled.

3. **Re-confirmation on Canonical Fork:**
   - If the same transaction is included in the new canonical chain, the indexer re-indexes it as `CANONICAL`.
   - Once the new canonical block reaches $\ge 32$ confirmations, status transitions from `REORGED` $\rightarrow$ `CONFIRMED`.

---

## 4. Deep Reorg Operator Runbook

A reorganization exceeding 32 blocks is classified as a **Deep Reorganization Emergency**.

### Immediate Automated Actions:
1. `ChainReorganization` record is marked `ReorgStatus.FAILED`.
2. Settlement authorizer immediately halts all new settlement authorizations for the affected chain (fail-closed).
3. `DistributionService` and `DistributionReconciliationService` halt operations on the affected chain.

### Operator Manual Recovery Steps:
1. **Identify Fork Point:** Query `chain_reorganizations` table for `fork_block_number`.
2. **Verify Node Sync:** Inspect RPC node logs to ensure node is fully synced with canonical chain.
3. **Run Projection Rebuild:** Execute `python -m app.services.blockchain.scripts.rebuild_projections --chain-id <ID> --from-block <FORK_BLOCK>`.
4. **Audit Balances:** Run `SELECT * FROM distributions WHERE status = 'REORGED'` to verify on-chain token receipts against database state.
5. **Clear Emergency Halt:** Set `status = 'RESOLVED'` in `chain_reorganizations` once verified.
