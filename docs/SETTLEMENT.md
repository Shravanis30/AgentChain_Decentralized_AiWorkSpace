# AgentChain Settlement Architecture & Escrow Integration (Phase 6.2B)

> **CRITICAL PRODUCTION SAFETY NOTICE:**
> **Phase 6.2B does not enable Base Mainnet settlement.**
> All transaction submission to Base Mainnet (Chain ID 8453) is strictly blocked in configuration, API validation, authorization boundaries, and relayer runtime.

---

## 1. Executive Summary & Architecture

Phase 6.2B safely connects AgentChain's multi-agent execution pipeline with on-chain Escrow settlement:

```
LangGraph Orchestrator
        ↓
AgentExecution (Terminal Success)
        ↓
Execution Completion Verification
        ↓
Settlement Authorization Boundary
        ↓
Escrow Verification Boundary (Canonical On-Chain State)
        ↓
Settlement Record (PostgreSQL FOR UPDATE)
        ↓
Blockchain Transaction Intent
        ↓
Transactional Outbox (PostgreSQL)
        ↓
Redis Stream (`blockchain:intents:{chain_id}`)
        ↓
Blockchain Relayer & Signer Boundary
        ↓
Escrow Contract (`Escrow.sol` on Base Sepolia / Anvil)
        ↓
AgentChain Indexer (Canonical Block & Event Ingestion)
        ↓
Settlement Reconciliation Worker
        ↓
Database Settlement State (CONFIRMED)
```

Settlement is:
- **Explicit**: No automatic financial transfers without verification.
- **Deterministic**: Stable settlement keys eliminate duplicate actions.
- **Idempotent**: Concurrent or duplicate requests return the same logical record.
- **Authorization-Controlled**: RBAC enforces client and arbitrator permissions.
- **Escrow-State-Aware**: Validates against indexed canonical on-chain state before authorising.
- **Reorg-Aware**: Can undo false finality if an on-chain event becomes orphaned.
- **Confirmation-Aware**: Requires configured confirmation depths (Anvil: 1, Sepolia: 3, Mainnet: 12).
- **Fail-Closed**: Any anomaly, mismatch, or unconfirmed state blocks settlement.

---

## 2. Settlement Domain Model & State Machine

### 2.1 Settlement Status Lifecycle

```
[ PENDING_AUTHORIZATION ]
       │              │
       │              └───► [ BLOCKED ] (Policy/Escrow/Chain check failed)
       ▼
 [ AUTHORIZED ]
       │      └───► [ CANCELLED ] (User/Client cancelled before broadcast)
       │
       ▼ (Tx Broadcast)
 [ SUBMITTED ]
       │      └───► [ FAILED ] (On-chain revert or relayer exhaustion)
       ▼ (Canonical Confirmations Reached)
 [ CONFIRMED ]
       │
       └───► (If reorg orphans block) ──► [ SUBMITTED / BLOCKED ]
```

### 2.2 Statuses

| Status | Description | Terminal? |
|---|---|---|
| `PENDING_AUTHORIZATION` | Initial state upon creation; awaiting client/arbitrator authorization | No |
| `AUTHORIZED` | Authorized; transactional intent & outbox record created atomically | No |
| `SUBMITTED` | Transaction broadcast to mempool by relayer; tx hash recorded | No |
| `CONFIRMED` | Canonical event confirmed to configured depth | Yes (reorg-recoverable) |
| `BLOCKED` | Policy or validation violation halted processing | Yes |
| `FAILED` | Transaction permanently reverted or failed execution | Yes |
| `CANCELLED` | Explicitly cancelled by authorized party before submission | Yes |

### 2.3 Settlement Actions

- `RELEASE`: Client or arbitrator authorizes release of escrowed USDC to developer/beneficiary.
- `REFUND`: Client requests refund if allowed before lock or post-deadline; beneficiary forfeiture.
- `DISPUTE_RESOLVE_RELEASE`: Arbitrator resolves dispute by distributing 100% to developer.
- `DISPUTE_RESOLVE_REFUND`: Arbitrator resolves dispute by distributing 100% to client.

---

## 3. Deterministic Idempotency Model

Settlement records maintain a strict deterministic idempotency key format:

$$\text{idempotency\_key} = \text{"settlement:"} + \text{chain\_id} + \text{":"} + \text{escrow\_id} + \text{":"} + \text{action} + \text{":"} + \text{authorization\_version}$$

- **Unique Constraint**: `(chain_id, idempotency_key)` in PostgreSQL enforces that concurrent requests for the same logical settlement resolve to the identical record.
- **Row-Level Locking**: `SELECT ... FOR UPDATE` is applied during creation and authorization, preventing race conditions.
- **Separation of Concerns**: Logical settlement identity (`settlement_id`) is strictly decoupled from blockchain attempt identities (`tx_attempt_id`), allowing gas bumps and relayer retries without altering the settlement identity.

---

## 4. Escrow Verification Boundary

Before authorizing any settlement action, the `EscrowVerificationBoundary` queries the local canonical `EscrowChainState` projection and validates:

1. **Chain ID**: Must match configured supported network (Anvil 31337 or Base Sepolia 84532).
2. **Contract Address**: Must exactly match the configured `Escrow` deployment address.
3. **Escrow Exists**: Rejects nonexistent escrows.
4. **Canonical State**: The backing event must be canonical (`is_canonical = True`).
5. **Confirmation Status**: Must have reached `CONFIRMED` status.
6. **Terminal States**: Rejects escrows already in terminal states (`RELEASED`, `REFUNDED`, `RESOLVED`).
7. **Parameter Integrity**: Ensures client, beneficiary, amount, and token match recorded expectations.

---

## 5. Authorization Policy Engine

The `SettlementAuthorizationEngine` enforces:

- **Caller Role & Ownership**:
  - `RELEASE`: Only the escrow's client or an authorized platform arbitrator.
  - `REFUND`: Client before lock or after deadline; beneficiary forfeiture at any time.
  - `DISPUTE_RESOLVE_*`: Strictly platform arbitrator role (`ADMIN` or explicit `ARBITRATOR`).
- **Orchestration Verification**: If linked to an `orchestration_id`:
  - Orchestration must exist and belong to the client.
  - Orchestration status must be `COMPLETED`.
  - All linked orchestration tasks must be `COMPLETED`.
- **Contract Call Whitelist**: Translates actions to fixed internal methods (`releaseEscrow`, `refundEscrow`, `resolveDispute`). No arbitrary calldata or user-supplied targets are permitted.

---

## 6. Confirmation Policy

Confirmations are driven by chain configuration:

| Network | Chain ID | Required Confirmations |
|---|---|---|
| Anvil (Local) | 31337 | 1 |
| Base Sepolia | 84532 | 3 |
| Base Mainnet | 8453 | 12 (Hard-blocked from sending transactions) |

Settlement reconciliation only advances from `SUBMITTED` to `CONFIRMED` when the canonical event's block confirmation depth meets or exceeds this policy.

---

## 7. Reorganization Handling & Authoritative Window

The authoritative maximum reorg tracking window across the indexer, blockchain infrastructure, and settlement engine is **strictly 32 blocks**:
- **Authoritative Configuration**: `ChainConfig.max_reorg_depth = 32` and `SettlementConfig.max_reorg_depth = 32`.
- **Shallow Reorganization (depth <= 32 blocks)**:
  1. Indexer marks reorged blocks and events as `is_canonical = False`, `status = ORPHANED`.
  2. `SettlementReconciliationService.reconcile_settlements` scans previously confirmed settlements.
  3. If the confirming event is no longer canonical, the settlement status is demoted from `CONFIRMED` to `SUBMITTED` (or `BLOCKED` if re-indexing is pending), and a `SETTLEMENT_REORG_INVALIDATED` audit log is recorded.
- **Deep Reorganization (depth > 32 blocks)**:
  1. `ReorgHandler` detects that common ancestor exceeds the 32-block tracking window, records a `ChainReorganization` with status `FAILED`, and raises `DeepReorganizationError`.
  2. **Fail-Closed Execution**: Automatic state mutation ceases immediately.
  3. Settlement reconciler detects the unrecovered deep reorg and automatically transitions all in-flight and confirmed settlements on that chain to `BLOCKED`.
  4. New settlement authorizations on that chain are immediately halted with `SettlementAuthorizationError`.
  5. Manual operational intervention is strictly required before any settlement can resume.

---

## 7.1 Testnet Integration Reality Check

- **Local Anvil (31337)**: Fully executed and verified with end-to-end simulated and programmatic tests (contracts, indexing, relayer, outbox, settlement lifecycle).
- **Base Sepolia (84532)**: Validated via configuration, static analysis, ABI compatibility, and contract test suites. **No live transaction was broadcast to the Base Sepolia network** during testing because live testnet private keys and RPC endpoints were not populated in this local environment. All testnet behavior has been verified with exact network-matching mock/simulation layers.

---

## 8. Mainnet Hard-Block Guard

- **Backend Configuration**: `BLOCK_MAINNET_SETTLEMENT=true` in `SettlementConfig`.
- **Verifier Check**: `EscrowVerificationBoundary` immediately raises `SettlementMainnetBlockedError` if `chain_id == 8453`.
- **Relayer Check**: Relayer raises `MainnetSubmissionBlockedError` if `chain_id == 8453`.
- **API Guard**: Rejects creation and authorization with HTTP 400.

---

## 9. Future 85/10/5 Revenue Distribution Extension Point

> **NOTE:** Revenue distribution (85% developer, 10% staker, 5% DAO) is **NOT** implemented in Phase 6.2B.

The settlement architecture provides an extension point for future distribution:
- `Settlement.beneficiary_amount`: Currently receives 100% of released escrow funds.
- When Phase 6.2C or future phases introduce splits:
  - An off-chain or on-chain split module can intercept the authorization step.
  - The settlement record can track split allocations (`developer_share`, `staker_share`, `dao_share`) without altering the core `Escrow.sol` custody contract.
  - Future staking contracts and DAO treasuries will subscribe to `SETTLEMENT_CONFIRMED` audit and domain events.
