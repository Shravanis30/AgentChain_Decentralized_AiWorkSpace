# AgentChain Settlement Failure & Recovery Matrix (Phase 6.2B)

> **PRODUCTION NOTICE:**
> **Phase 6.2B does not enable Base Mainnet settlement.**
> This matrix details failure scenarios, authoritative sources, automated recovery, and operational procedures across all settlement components.

---

## 1. Step 29 Failure / Recovery Matrix

| # | Scenario | Authoritative Source | Persisted State | Retry Behavior | Reconciliation Behavior | Alert / Metric | Manual Intervention? |
|---|---|---|---|---|---|---|---|
| **1** | **API crashes before settlement creation** | PostgreSQL | None | Client receives 5xx/timeout and retries | Idempotency key ensures clean first creation upon retry | `api_request_errors_total` | No |
| **2** | **API crashes after settlement creation** | PostgreSQL | `Settlement: PENDING_AUTHORIZATION` | Client retries creation; existing record returned | N/A (record waiting for authorization) | `settlement_duplicate_total` | No |
| **3** | **API crashes after transaction intent creation** | PostgreSQL | `Settlement: AUTHORIZED`, `Intent: CREATED`, `Outbox: PENDING` | Client retries authorization; existing authorized settlement returned | Outbox dispatcher picks up pending outbox item independently | `settlement_authorized_total` | No |
| **4** | **Outbox dispatcher crashes** | PostgreSQL | `BlockchainTxOutbox: PENDING` | Outbox item remains due; next worker pass picks it up after backoff | Outbox dispatcher sweeps pending items with `next_attempt_at <= now` | `outbox_dispatch_lag_seconds` | No |
| **5** | **Redis unavailable** | PostgreSQL | `BlockchainTxOutbox: PENDING`, `attempts` incremented | Exponential backoff retry on outbox; DB state preserved | Once Redis reconnects, dispatch automatically succeeds | `redis_connection_errors_total` | No |
| **6** | **Relayer crashes** | PostgreSQL & EVM RPC | `Intent: QUEUED` or `SUBMITTED`, `Tx: SUBMITTED` | Relayer restarts, reads durable stream / unconfirmed transactions from DB | Verifies mined nonces against RPC before re-signing | `relayer_restarts_total` | No |
| **7** | **RPC unavailable** | Local DB & Gas Estimator | `Tx: SUBMITTED`, `Attempt: PENDING` | Transient error classifier backs off without failing intent | Transaction remains in-flight until RPC restores | `rpc_errors_transient_total` | No |
| **8** | **Tx broadcast succeeds but response lost** | EVM RPC Mempool / Mined Blocks | `Attempt: SUBMITTED` | Relayer checks `eth_getTransactionReceipt` and `eth_getTransactionByHash` before retry | Relayer discovers transaction hash on-chain; avoids duplicate broadcast | `tx_receipt_recovered_total` | No |
| **9** | **Transaction is replaced** | EVM Mempool & Mined Blocks | `Attempt #1: REPLACED`, `Attempt #2: SUBMITTED` | Replacement inherits same nonce with >=12.5% fee bump | Settlement remains linked to parent `intent_id`; advances when replacement confirms | `tx_replaced_total` | No |
| **10** | **Transaction is mined** | Canonical EVM Block Header | `Tx: CONFIRMED`, `Settlement: SUBMITTED` | N/A | Reconciliation advances `Settlement -> CONFIRMED` once confirmation depth reached | `settlement_confirmed_total` | No |
| **11** | **Transaction is reverted** | Transaction Receipt (`status=0`) | `Tx: FAILED`, `Settlement: FAILED` | Permanent error; no automatic transaction resubmission | Reconciler records revert reason and marks settlement `FAILED` | `settlement_failed_total` | Yes (investigate revert reason) |
| **12** | **Indexer crashes** | `indexed_blocks` table | Last canonical block stored in DB | Indexer restarts, fetches latest block from DB, resumes scanning from `last_block + 1` | Backfills missing blocks and events cleanly | `indexer_restarts_total` | No |
| **13** | **Indexer sees duplicate event** | PostgreSQL Unique Constraints | `BlockchainEvent: (chain, block_hash, tx_hash, log_index)` | DB unique constraint ignores or updates existing event | Settlement reconciler executes idempotently; confirms exactly once | `indexer_duplicate_events_total` | No |
| **14** | **Chain reorg occurs** | Canonical EVM Chain Headers | `IndexedBlock: is_canonical=False`, `BlockchainEvent: ORPHANED` | Indexer marks orphaned blocks and backtracks to common ancestor | Settlement reconciliation detects orphaned event; demotes settlement from `CONFIRMED` | `settlement_reorg_invalidated_total` | Yes, if deep reorg exceeds tracking window |
| **15** | **Settlement reconciler crashes** | PostgreSQL | `Settlement: SUBMITTED` | Reconciler restarts and re-scans active submitted settlements | Fetches latest canonical events and advances state | `reconciliation_restarts_total` | No |
| **16** | **PostgreSQL unavailable** | In-flight services | Connection pooling retry | Services back off and wait for DB recovery; transactions fail closed | All operations resume from last committed ACID boundary | `db_connection_errors_total` | If DB cluster failure requires failover |
| **17** | **Mainnet settlement requested accidentally** | Config / App Code | `Settlement: BLOCKED` (or rejected at API boundary) | Request rejected immediately with HTTP 400 | Relayer and Verifier raise `MainnetBlockedError`; no RPC call made | `settlement_mainnet_blocked_total` | No |
| **18** | **Nonce ambiguity (Step 10)** | EVM `eth_getTransactionCount` (latest vs pending) | `BlockchainTransaction: SUBMITTED` | Higher pending nonce is ignored; only latest mined nonce advances state | Prevents premature failure or duplicate nonce consumption | `nonce_discrepancy_total` | No |
| **19** | **Stale escrow state** | Canonical `EscrowChainState` | DB reflects older block | Verifier re-queries indexed canonical state or waits for indexer catch-up | Verifier rejects settlement until confirmation depth is satisfied | `settlement_stale_state_total` | No |
| **20** | **Escrow already terminal** | On-chain contract & `EscrowChainState` | `EscrowChainState: RELEASED / REFUNDED / RESOLVED` | Immediate failure closed | Authorization engine rejects request with `SettlementEscrowVerificationError` | `settlement_terminal_conflict_total` | No |
