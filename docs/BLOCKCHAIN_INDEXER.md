# Blockchain Indexer Architecture (Phase 6.2A)

> **CRITICAL BOUNDARY NOTICE:**
> Phase 6.2A does not authorize automatic escrow settlement or production money movement.
> The indexer provides read-only observation and derived state projection from the authoritative EVM blockchain.

---

## 1. Overview & Data Flow

The AgentChain Blockchain Indexer continuously synchronizes state from Ethereum-compatible EVM chains (Local Anvil, Base Sepolia, Base Mainnet) into PostgreSQL:

```text
Blockchain (Base / Anvil)
         ↓  JSON-RPC (eth_getLogs, eth_getBlockByNumber)
BlockchainRpcClient (fail-closed chain validation, exponential backoff)
         ↓
BlockTracker (parent_hash linking, reorg detection)
         ↓
EventIndexer (deduplication on (chain_id, tx_hash, log_index))
         ↓
EscrowStateProjector (derived EscrowChainState projection)
         ↓
PostgreSQL Storage (`indexed_blocks`, `blockchain_events`, `escrow_chain_state`)
```

---

## 2. Supported Networks & Explicit Configuration

Network identity is never inferred from arbitrary RPC responses. Each chain configuration is explicitly declared and validated via `eth_chainId` on startup:

| Network | Chain ID | RPC URL | Confirmations Required | Transaction Submission | Circle USDC Token |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Local Anvil** | `31337` | `http://127.0.0.1:8545` | 1 | **Enabled** (Testing) | `MockUSDC` (`0xe7f1...`) |
| **Base Sepolia** | `84532` | `https://sepolia.base.org` | 3 | **Enabled** (Testing) | `0x036CbD53842c5426634e7929541eC2318f3dCF7e` |
| **Base Mainnet** | `8453` | `https://mainnet.base.org` | 12 | **HARD DISABLED** | `0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913` |

### Fail-Closed Safeguards
- If `eth_chainId` does not match the configured Chain ID, `ChainMismatchError` is raised immediately.
- If contract addresses do not match the allowlist, `ContractMismatchError` is raised.
- If Base Mainnet transaction submission is attempted, `MainnetSubmissionBlockedError` is raised.

---

### 3. Block Tracking & Reorg-Compatible Persistence (Phase 6.2A.1 Hardening)

The database maintains `indexed_blocks` to verify parent-hash chains and preserve fork history during chain reorganizations:

```text
SEEN  →  CONFIRMING  →  CONFIRMED
      ↘ (Reorg)
         ORPHANED (is_canonical = false)
```

- **SEEN:** Block ingested from RPC; depth < `confirmations_required`.
- **CONFIRMING:** Block depth > 1 but < `confirmations_required`.
- **CONFIRMED:** Block depth >= `confirmations_required`.
- **ORPHANED:** Block belongs to an abandoned fork after a detected reorganization (`is_canonical = false`).

#### Historical Coexistence vs Single Canonical Invariant
PostgreSQL enforces the invariant at the database engine level via Migration `010_reorg_safe_indexer_tx`:
- **Multiple historical blocks permitted at same height:** An abandoned fork block (e.g. block 100 / hash `0xAAA`) remains persisted as `ORPHANED` with `is_canonical = false`.
- **Exactly ONE canonical block per (chain_id, block_number):** Enforced by PostgreSQL partial unique index:
  ```sql
  CREATE UNIQUE INDEX uq_indexed_blocks_canonical
  ON indexed_blocks (chain_id, block_number)
  WHERE is_canonical = true;
  ```
- Any attempt by application logic or concurrency race to insert two canonical blocks at the same height immediately raises a database constraint violation.

---

## 4. Reorg-Compatible Event Ingestion & Deduplication Identity

All emitted events from `Escrow.sol` are supported:
- `EscrowCreated(...)`
- `EscrowFunded(...)`
- `EscrowLocked(...)`
- `EscrowReleased(...)`
- `EscrowRefunded(...)`
- `DisputeOpened(...)`
- `DisputeResolved(...)`
- `Paused(...)`
- `Unpaused(...)`

### Persistent Event Identity & Deduplication
Event identity distinguishes block and fork identity to guarantee that orphaned logs never block canonical logs:
1. **Primary Log Identity:**
   ```sql
   UNIQUE (chain_id, block_hash, transaction_hash, log_index)
   ```
2. **Canonical Exclusivity:**
   ```sql
   CREATE UNIQUE INDEX uq_bc_events_canonical_tx_log
   ON blockchain_events (chain_id, transaction_hash, log_index)
   WHERE is_canonical = true;
   ```

#### Core Invariants:
- **Invariant 1 (Duplicate Polling):** Repeated indexing over the same canonical block range detects existing records by `(chain_id, block_hash, tx_hash, log_index)` and is strictly idempotent.
- **Invariant 2 (Reorg Independence):** An orphaned event on an abandoned block does not prevent the canonical fork's event from being indexed.
- **Invariant 3 (Historical Preservation):** Orphaned events are never hard-deleted; their status transitions to `ORPHANED` with `is_canonical = false`.
- **Invariant 4 (Canonical Uniqueness):** Conflicting canonical events at the same position trigger a database constraint failure.
- **Invariant 5 (Deterministic Projection):** Derived `EscrowChainState` records are projected exclusively from canonical events (`is_canonical = true`).

---

## 5. Derived Escrow State Machine

`EscrowChainState` is an operational read-model projection of on-chain state:
- `0: CREATED`
- `1: FUNDED`
- `2: LOCKED`
- `3: RELEASED`
- `4: REFUNDED`
- `5: DISPUTED`
- `6: RESOLVED`

The EVM blockchain remains the single authoritative source of truth. If a reorg occurs, derived escrow records are re-projected strictly from the canonical event sequence. If all events for an escrow are orphaned, the derived state is isolated with `is_canonical = false`.
