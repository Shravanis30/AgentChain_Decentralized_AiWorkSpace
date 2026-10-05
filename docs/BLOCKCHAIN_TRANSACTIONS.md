# Blockchain Transaction Lifecycle & Infrastructure (Phase 6.2A)

> **CRITICAL BOUNDARY NOTICE:**
> Phase 6.2A does not authorize automatic escrow settlement or production money movement.
> Transaction submission is strictly disabled on Base Mainnet.

---

## 1. Overview & Transaction Pipeline

The transaction management pipeline provides durable, idempotent submission and tracking of EVM transactions:

```text
Business DB Transaction
    +
BlockchainTransactionIntent (unique on chain_id, idempotency_key)
    +
BlockchainTxOutbox (PENDING)
    ↓  PostgreSQL COMMIT (Atomic)
BlockchainTxOutboxDispatcher
    ↓  Redis Stream (`agentchain:blockchain:tx_stream`)
BlockchainRelayer Worker
    ↓  Nonce Reservation (SELECT ... FOR UPDATE on relayer_nonces)
    ↓  EIP-1559 Gas Estimation (GasEstimator)
    ↓  Signing Boundary (LocalAccountSigner in-memory / HSM)
RPC Broadcast (eth_sendRawTransaction)
    ↓
BlockchainTransaction (SUBMITTED) & BlockchainTransactionAttempt #1
    ↓
Receipt Polling & Reconciliation
    ↓
CONFIRMED (status=1) | FAILED (status=0) | REPLACED (gas bump)
```

---

## 2. Idempotent Transaction Intents

Every on-chain action must be initiated as a logical intent:
- **Logical Identity:** `(chain_id, idempotency_key)` is UNIQUE in the database.
- **Deduplication:** A second submission with the same idempotency key returns the existing intent immediately, guaranteeing that duplicate network requests or retries never trigger multiple on-chain transactions.
- **Operation Allowlist:** Only approved Escrow contract functions (`createEscrow`, `createAndFundEscrow`, `fundEscrow`, `lockEscrow`, `releaseEscrow`, `refundEscrow`, `disputeEscrow`, `resolveDispute`, `pause`, `unpause`) are accepted. Arbitrary calldata is strictly blocked.

---

## 3. Transactional Outbox & Redis Streams

To prevent transaction loss during network partitions or Redis downtime:
1. **Atomic Enqueue:** `BlockchainTxOutbox` is inserted within the exact same database transaction as the `BlockchainTransactionIntent`.
2. **At-Least-Once Delivery:** The `BlockchainTxOutboxDispatcher` reads pending records (`status = PENDING`) using `SELECT ... FOR UPDATE SKIP LOCKED` and publishes them to Redis Stream `agentchain:blockchain:tx_stream`.
3. **Exponential Backoff:** If Redis is temporarily unreachable, entries are retried with exponential backoff (`delay = 2 * 2^(attempts-1)` seconds).
4. **Dead-Letter Handling:** After 5 failed attempts, the entry is transitioned to `FAILED` and an operational alert is emitted.

---

## 4. Nonce Management

To prevent nonce collisions across concurrent processes:
- Nonces are persisted in `relayer_nonces` table per `(chain_id, account_address)`.
- Nonce allocation uses row-level locking (`SELECT ... FOR UPDATE`).
- Nonces are strictly monotonic without gaps.
- Nonce state reconciles periodically against on-chain `eth_getTransactionCount('latest' and 'pending')`.

---

## 5. EIP-1559 Fee Management & Safe Gas Bumping (Phase 6.2A.1 Hardening)

- **Fee Estimation:** Derived from `eth_feeHistory` and base fee from block headers:
  $$\text{max\_fee} = (2 \times \text{base\_fee}) + \text{priority\_fee}$$
- **Safe Gas Bumping:** A transaction is never replaced solely because `elapsed_time > threshold`. The reconciliation service enforces a multi-step verification pipeline:
  1. **Receipt Polling across all attempts:** If any attempt has a valid receipt with `status=0x1`, the transaction is finalized as `CONFIRMED`.
  2. **RPC Uncertainty (UNKNOWN State):** If receipt lookup fails due to network outage or RPC error (`TransientRpcError`), the transaction is classified `UNKNOWN` and replacement is deferred. RPC failures never trigger replacement.
  3. **Mined Transaction Detection:** If receipt is `None`, the system queries `eth_getTransactionByHash`. If `blockNumber` is populated, the transaction is already mined (receipt index lagging); replacement is deferred.
  4. **On-Chain Nonce Verification:** Queries `eth_getTransactionCount` on-chain:
     - If `latest_nonce > tx.nonce`: Nonce was already consumed on-chain! The transaction is reconciled as `DROPPED` without creating a blind replacement.
     - If `latest_nonce < tx.nonce`: Nonce gap detected; replacement is deferred.
     - If `latest_nonce == tx.nonce`: Transaction is genuinely unmined and eligible for replacement.
  5. **Replacement Invariants:**
     - Uses the **exact same nonce** (`tx.nonce`).
     - Bumps fee by **12.5%** (EIP-1559 requires >= 10%).
     - Halts at maximum **4 replacement attempts** to prevent unbounded fee loops.
     - Resets `transaction.submitted_at` to the new attempt timestamp.
     - Links previous attempts as `REPLACED`.
  6. **Concurrency Protection:** Uses PostgreSQL row-level locks (`with_for_update(skip_locked=True)`) and verifies fresh attempt counts before submission, ensuring only one worker replaces a stuck transaction.

---

## 6. Receipt Polling & Confirmation State Machine

```text
[CREATED]
    ↓  (Outbox Dispatch)
[QUEUED]
    ↓  (Broadcast)
[SUBMITTED]
    ↓  (Receipt Polling)
[PENDING]
    ├── (Receipt status = 1) ───────────→ [CONFIRMED]
    ├── (Receipt status = 0) ───────────→ [FAILED]
    ├── (External nonce consumed) ──────→ [DROPPED]
    └── (Safe verification & Bumping) ──→ [REPLACED] (new attempt submitted)
```

A transaction is never marked confirmed merely because the RPC accepted the raw payload. Confirmation requires receipt verification from the chain with status `0x1`.
