# Blockchain Reconciliation Architecture (Phase 6.2A)

> **CORE PRINCIPLE:**
> The blockchain is the sole authoritative source of truth.
> PostgreSQL derived views are reconciled to match on-chain reality, never the reverse.

---

## 1. Overview & Operational Role

The `BlockchainReconciliationService` runs periodically in the background to detect and automatically heal divergences between PostgreSQL operational tables and the underlying blockchain:

```text
Periodic Trigger (every 30-60s)
         ↓
1. Reconcile Pending Transactions
   - Query RPC receipts for submitted transactions
   - If receipt mined: advance to CONFIRMED or FAILED
   - If pending > threshold (120s): trigger replacement gas bump (up to 4 attempts)
         ↓
2. Detect Block Tracking Gaps
   - Scan indexed_blocks for missing heights [min_num, max_num]
   - Schedule backfill of missing block headers and logs
         ↓
3. Reconcile Outbox Dispatch
   - Detect outbox records stuck past backoff window
   - Reset next_attempt_at to re-enter Redis dispatch queue
         ↓
4. Nonce Alignment
   - Query eth_getTransactionCount('latest' and 'pending')
   - Advance next_nonce if external on-chain transactions advanced count
```

---

## 2. Inconsistencies Detected & Handled

| Scenario | Detection Method | Healing Action |
| :--- | :--- | :--- |
| **Transaction confirmed on-chain** | `eth_getTransactionReceipt` returns valid receipt with `status=0x1`. | Mark transaction `CONFIRMED`, record block info and effective gas, update intent status. |
| **Transaction reverted on-chain** | `eth_getTransactionReceipt` returns receipt with `status=0x0`. | Mark transaction `FAILED`, record revert error, mark intent `FAILED`. |
| **RPC outage or network error** | `get_transaction_receipt` raises `TransientRpcError` or network drop. | Classify `UNKNOWN`; **DEFER** replacement. Never replace due to RPC failure! |
| **Transaction mined but receipt lagging** | Receipt is `None`, but `eth_getTransactionByHash` returns `blockNumber != None`. | Classify mined; **DEFER** replacement and wait for receipt. |
| **Nonce consumed externally** | `eth_getTransactionCount('latest') > tx.nonce`. | Mark transaction and attempts `DROPPED`; never create blind replacements. |
| **Genuinely stuck transaction (> 120s)** | Receipt `null`, `blockNumber is None`, `latest_nonce == tx.nonce`. | If attempts < 4, submit safe replacement using **SAME NONCE** with 12.5% fee bump. |
| **Missing block in indexed_blocks** | Height gaps identified in range `[min, max]`. | Ingest missing block headers and query logs for that specific range. |
| **Outbox message lost due to Redis restart** | Outbox record remains in `PENDING` with elapsed backoff. | Reset `next_attempt_at = now()` to retry stream publication. |

---

## 3. Safe Transaction Replacement Decision Tree (Phase 6.2A.1 Hardening)

Never create a replacement transaction solely because `elapsed_time > threshold`. The system executes a rigorous safety decision tree:

```text
Transaction pending beyond threshold
            ↓
Query receipt across all attempts
            ↓
Receipt exists?
       ┌────┴────┐
      YES        NO
       ↓          ↓
verify receipt    query transaction object (eth_getTransactionByHash)
       ↓          ↓
canonical?       exists & mined? (blockNumber != None)
       ↓          ┌─────┴─────┐
    finalize     YES          NO
                  ↓           ↓
             wait for receipt  inspect on-chain nonces (latest & pending)
                               ↓
                      latest_nonce > tx.nonce?
                         ┌─────┴─────┐
                        YES          NO
                         ↓           ↓
                     reconcile    latest_nonce == tx.nonce?
                    as DROPPED       ┌─────┴─────┐
                                    YES          NO (gap)
                                     ↓           ↓
                             attempts < 4?     defer
                               ┌─────┴─────┐
                              YES          NO
                               ↓           ↓
                         replace with    halt at limit
                          SAME NONCE
```

### Safety Invariants:
1. **Original transaction is not mined:** Verified via receipt lookup and transaction object `blockNumber`.
2. **RPC Uncertainty Handled:** Explicitly distinguishes `NOT_FOUND` from `RPC_ERROR` and `NETWORK_UNAVAILABLE`. RPC errors defer immediately.
3. **Nonce unconsumed:** On-chain nonce count confirms `latest_nonce == tx.nonce`.
4. **Same Nonce:** Replacement strictly preserves `transaction.nonce`.
5. **Fee Escalation:** Gas fees bumped by 12.5% (EIP-1559 compliant) and capped by ceiling.
6. **Bounded Attempts:** Halts at maximum 4 replacement attempts.
7. **Concurrency Race Protection:** Uses row-level locking (`with_for_update(skip_locked=True)`) and re-verifies fresh attempt counts before broadcast to ensure only one worker creates a replacement.
