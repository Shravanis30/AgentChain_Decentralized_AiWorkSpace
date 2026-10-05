# Distribution Architecture — AgentChain Phase 6.2C

## 1. Executive Summary

Phase 6.2C introduces the 85/10/5 revenue distribution layer on top of the existing escrow and settlement infrastructure. This document explains the chosen architecture, alternatives evaluated, trust boundaries, custody flow, and failure modes.

---

## 2. Architecture Decision: Option B — Dedicated `RevenueDistributor` Contract

### Chosen Design

The `Escrow.sol` contract is modified so that its `releaseEscrow` function transfers the gross amount to a dedicated `RevenueDistributor` contract instead of directly to the beneficiary. The `RevenueDistributor` then atomically performs the 85/10/5 split in a single on-chain transaction.

```
Escrow.releaseEscrow(escrowId)
    ├── state: LOCKED → RELEASED (in Escrow)
    ├── paymentToken.safeTransfer(RevenueDistributor, grossAmount)
    └── RevenueDistributor.distribute(escrowId, grossAmount, developerRecipient)
            ├── developer 85% → developerRecipient (safeTransfer)
            ├── staker   10% → stakerRecipient (from immutable config, safeTransfer)
            └── dao       5% → daoRecipient    (from immutable config, safeTransfer)
                                                 (dao = remainder after floor divisions)
            └── emit DistributionExecuted(...)
```

### Option A — Escrow Directly Distributes

**Option A** would have `Escrow.sol` itself perform the 85/10/5 transfers. This was **rejected** because:

1. **Violates single responsibility**: Escrow is a custody contract, not an accounting contract.
2. **Tight coupling**: Changing distribution logic requires redeploying `Escrow.sol`, which invalidates all existing escrow storage.
3. **Redeployment risk**: Every economic change (future v2 splits) forces a migration of all funded escrows.
4. **Audit surface explosion**: Adding distribution addresses to Escrow expands the Escrow trust surface unnecessarily.

### Option B — Dedicated `RevenueDistributor` Contract (CHOSEN)

**Chosen because:**

1. **Separation of concerns**: Escrow manages custody; `RevenueDistributor` manages economic accounting.
2. **Atomic split in one transaction**: Developer, staker, and DAO all receive their funds atomically—no scenario where developer is paid but staker is not.
3. **Upgrade safety**: The distribution contract can be upgraded independently without touching Escrow storage or relying on proxies for Escrow.
4. **Audit isolation**: Security reviewers can audit distribution logic independently of escrow lifecycle logic.
5. **Conservation verifiable on-chain**: The emitted `DistributionExecuted` event carries all three amounts + gross, allowing any observer to verify `developer + staker + dao == gross`.
6. **Reentrancy resistance**: Both Escrow and Distributor use `ReentrancyGuard`. The distributor transfers are USDC-only; USDC has no reentrant callback path.
7. **Minimal trust**: Distributor does not hold funds except transiently during the atomic `distribute()` call. It never becomes a second escrow.

---

## 3. Trust Boundaries

| Component | Trust Level | Responsibility |
|---|---|---|
| `Escrow.sol` | ADMIN-controlled | Custody of USDC, lifecycle state machine |
| `RevenueDistributor.sol` | ADMIN-controlled, receives from Escrow only | Atomic 85/10/5 split |
| Backend `DistributionService` | Platform-controlled | Post-hoc reconciliation, not custody |
| Backend `SettlementService` | Platform-controlled | Authoritative settlement decision |
| Relayer/Nonce Manager | Platform-controlled | Transaction submission pipeline |
| Indexer | Platform-controlled | Event ingestion, canonical state |

**The `RevenueDistributor` MUST NOT:**
- Hold a balance between calls (it is not a vault)
- Allow arbitrary callers to trigger distributions
- Accept arbitrary token addresses
- Allow user-specified recipients for staker/DAO shares

---

## 4. Custody Flow

```
User (Client)
    │
    │  (1) createAndFundEscrow(...)
    ▼
Escrow.sol [holds USDC]
    │
    │  (2) After SettlementService authorization:
    │      backend calls releaseEscrow(escrowId) via Relayer
    ▼
Escrow.sol.releaseEscrow(escrowId)
    │  state: LOCKED → RELEASED
    │  safeTransfer(distributor, grossAmount)
    ▼
RevenueDistributor.sol [holds USDC transiently]
    │  distribute(escrowId, settlementId, grossAmount, developerRecipient, distributionVersion)
    │  ├── developerAmount = floor(gross * 85 / 100)
    │  ├── stakerAmount    = floor(gross * 10 / 100)
    │  └── daoAmount       = gross - developerAmount - stakerAmount  [exact remainder]
    │
    ├── safeTransfer(developerRecipient, developerAmount)
    ├── safeTransfer(stakerRecipient, stakerAmount)       [immutable platform config]
    └── safeTransfer(daoRecipient, daoAmount)             [immutable platform config]
    │
    └── emit DistributionExecuted(distributionId, escrowId, settlementId, gross, ...)
```

---

## 5. Contract Responsibilities

### `Escrow.sol` (modified)
- All existing behavior preserved.
- `releaseEscrow` now transfers gross amount to `RevenueDistributor` and calls `distribute()`.
- `refundEscrow` and `resolveDispute` remain unchanged (no distribution on refund/dispute).
- New immutable: `address public immutable distributor`.

### `RevenueDistributor.sol` (new)
- Accepts USDC from `Escrow` via `distribute()`.
- Performs atomic 85/10/5 split.
- Immutable: `stakerRecipient`, `daoRecipient`, `paymentToken`.
- `developerRecipient` passed at call time from Escrow (from escrow record's beneficiary address, which is the developer).
- Enforces: no zero addresses, no arbitrary tokens, conservation invariant.
- Emits: `DistributionExecuted`.
- Access-controlled: only Escrow (or `DISTRIBUTOR_CALLER_ROLE`) may call `distribute()`.

---

## 6. Backend Responsibilities

### `DistributionService` (new)
- Consumes the authoritative settlement decision from `SettlementService` — does NOT make independent authorization decisions.
- Resolves developer recipient from `EscrowChainState.developer` (the beneficiary on-chain address).
- Reads staker/DAO recipients from `DistributionConfig` (platform configuration, not user input).
- Calculates deterministic 85/10/5 amounts using integer arithmetic.
- Creates `Distribution` record with idempotency key.
- Creates `BlockchainTransactionIntent` (via existing `TransactionIntentService`).
- Does NOT bypass the outbox, relayer, or nonce manager.

### `DistributionReconciliationService` (new)
- Reconciles `Distribution` records against indexed `DistributionExecuted` events.
- Handles reorg invalidation.
- Handles retry for retryable failures.
- Does NOT issue a second on-chain transaction unless the first is provably lost.

---

## 7. Indexer Responsibilities

- Extended to index `DistributionExecuted` events from `RevenueDistributor.sol`.
- Same reorg handling, canonical event model, and confirmation depth rules apply.
- Distribution events are stored in `blockchain_events` table with `event_name = "DistributionExecuted"`.
- No second indexer is created.

---

## 8. Failure Modes

| Failure | Behavior |
|---|---|
| Escrow `safeTransfer` to distributor fails | `releaseEscrow` reverts; escrow remains LOCKED; backend retries |
| Distributor `safeTransfer` to developer fails | Entire `distribute()` call reverts; escrow transfer also reverts; state remains LOCKED |
| Distributor `safeTransfer` to staker fails | Same — full revert |
| Distributor `safeTransfer` to DAO fails | Same — full revert |
| Distributor called with wrong token | Reverts |
| Distributor called by non-Escrow | Reverts (access control) |
| Conservation violation (≠ 85+10+5 of gross) | Impossible by construction (remainder allocation to DAO) |
| Backend `DistributionService` crashes | Recovery from `Distribution` record via reconciliation |
| Backend sees partial indexer state | Waits for canonical confirmation (32-block window) |
| Deep reorg | Distribution authorization/submission halted per global reorg safety policy |

---

## 9. Upgrade Considerations

- `Escrow.sol` stores `distributor` as `immutable`. Upgrading the distributor address requires redeploying `Escrow.sol`. This is intentional — distribution address must be audited alongside escrow.
- `RevenueDistributor.sol` stores `DISTRIBUTION_VERSION = 1`. Future economic changes use a new version and a new distributor contract.
- Historical distributions always reflect the version-1 formula regardless of future changes.

---

## 10. Why the Existing Escrow/Settlement Architecture Remains Authoritative

- The `SettlementService` continues to perform all authorization decisions.
- The `Escrow.sol` continues to be the canonical source of truth for escrow state.
- The `BlockchainTransactionIntent` / outbox / relayer pipeline is preserved.
- The indexer's canonical event model with 32-block reorg window applies to `DistributionExecuted` events.
- `DistributionService` consumes `Settlement` records — it does not modify or re-evaluate them.
- No double nonce management. No second relayer. No second outbox.

---

## 11. Token Trust

- Distribution operates exclusively on USDC.
- The `paymentToken` in `RevenueDistributor` is set at construction time from the same chain-validated USDC address used by `Escrow.sol`.
- Arbitrary tokens are rejected.
- Fee-on-transfer tokens: if any transfer reduces the amount, the conservation check will detect the deficit. Distribution uses `safeTransfer` only.
- Base Mainnet: blocked by existing settlement safety guards. Distribution cannot be triggered on mainnet unless settlement is unblocked.

---

## 12. USDC Decimal Semantics

All amounts are in USDC base units (6 decimals). `$1.00 USDC = 1,000,000 base units`.

**No floating-point arithmetic is used anywhere in distribution logic.**

Integer arithmetic only:
```
developer_amount = floor(gross * 85 / 100)     # integer division
staker_amount    = floor(gross * 10 / 100)     # integer division
dao_amount       = gross - developer - staker   # exact remainder
```

This guarantees: `developer + staker + dao == gross` for every possible `uint256` value.

---

*Document version: Phase 6.2C — not yet enabled on Base Mainnet*
