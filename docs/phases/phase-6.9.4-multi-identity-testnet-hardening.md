# Phase 6.9.4 — Multi-Identity & Testnet Operator Hardening

**Date:** 2026-10-01  
**Status:** **RELEASE GATE PASSED** ✅  
**Documentation & Evidence Status:** **CORRECTED & AUDITED** ✅  
**Target Network:** Base Sepolia Testnet (Chain ID: `84532`)  
**Base Mainnet Guard:** Strictly Blocked (`BLOCK_MAINNET_SETTLEMENT = True`, `MainnetDeploymentBlocked` enforced)  
**Operator / Client Address:** `0x473888C859F88D3De7b5A20986805b4dC3178189`  
**Developer Recipient Address:** `0x162dEB52f30f551020c23155Bc5c66419c184d82` (Fresh dedicated testnet wallet)  

---

## 1. Executive Summary

Phase 6.9.4 hardens the live Base Sepolia marketplace validation achieved in Phase 6.9.3 by eliminating test-only identity reuse, verifying native on-chain anti-self-dealing protections, and executing a brand new end-to-end marketplace lifecycle between provably distinct protocol actors.

Historical Phase 6.9.3 evidence and transactions remain immutable. Phase 6.9.4 introduces a fresh, non-default developer identity, provisions a new test agent, and executes an entirely independent order lifecycle against live Base Sepolia contracts.

Key Hardening Accomplishments:
1. **Multi-Identity Separation:** Client (`0x4738...189`) and Developer (`0x162d...d82`) are distinct, non-default Base Sepolia addresses (`client != developer`). Neither resolves to standard Anvil/Hardhat accounts, and the historical Phase 6.9.3 developer address (`0x70997970C51812dc3A010C7d01b50e0d17dc79C8`) was not reused.
2. **On-Chain Self-Payment Protection:** Non-destructive simulation of `Escrow.createEscrow` with `beneficiary == client` reverted with custom error `SelfPaymentNotAllowed(0x473888C859F88D3De7b5A20986805b4dC3178189)` (selector `0x60ef2d64`). Classified as `SIMULATED — BASE SEPOLIA STATE`.
3. **New Production Marketplace Lifecycle:** Executed a full end-to-end order with 5 live on-chain state-changing Base Sepolia transactions for Agent Registration, Escrow Funding, RFC-8785 Result Notarization, Settlement Release with 85/10/5 Revenue Distribution, and Reputation Registry registration. The USDC allowance was verified as an on-chain state read, and 32-block depth was verified as an on-chain state observation.
4. **Economic Distribution Conservation:** 100,000 atomic units (0.10 USDC) released on-chain resulted in exact balance deltas: Developer received $+85,000$, Stakers received $+10,000$, and DAO received $+5,000$ ($85,000 + 10,000 + 5,000 = 100,000$).
5. **Replay & Idempotency Protection:** Tested duplicate notarization, duplicate escrow release, and duplicate reputation registration on the completed order; all reverted cleanly with custom error selectors in simulation against deployed Base Sepolia state (`SIMULATED — BASE SEPOLIA STATE`).
6. **Full Regression Validation:** 115 Foundry tests, 138 backend pytest suites, 8 service/SDK tests, Next.js TypeScript check, and config drift scanner all passed with 0 failures.

---

## 2. Protocol Identity Table

| Role | Wallet Address | Status | Notes |
|---|---|---|---|
| **Client / Operator** | `0x473888C859F88D3De7b5A20986805b4dC3178189` | Active | Funder, Order Creator, Protocol Deployer |
| **Developer Recipient** | `0x162dEB52f30f551020c23155Bc5c66419c184d82` | Active | Fresh dedicated testnet wallet, distinct from deployer/client |
| **Historical Dev (6.9.3)** | `0x70997970C51812dc3A010C7d01b50e0d17dc79C8` | Retired / Not Reused | Preserved in Phase 6.9.3 records only |
| **Anvil Default #0** | `0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266` | Hard Blocked | Zero protocol identity permissions |
| **Stakers Recipient** | `0x51a0000000000000000000000000000000000001` | Active (Immutable) | Receives 10% protocol revenue share |
| **DAO Treasury** | `0xda00000000000000000000000000000000000002` | Active (Immutable) | Receives 5% protocol revenue share |

**Identity Invariant Verification:**
- `client != developer`: `0x473888C859F88D3De7b5A20986805b4dC3178189 != 0x162dEB52f30f551020c23155Bc5c66419c184d82` (VALIDATED)
- Developer key stored strictly locally in `.dev_wallet.txt` (git-ignored, never committed, zero repo exposure).

---

## 3. On-Chain Self-Payment Protection Test

To verify protocol safeguards against client-developer self-dealing, a non-destructive transaction simulation was submitted to `Escrow.createEscrow` on Base Sepolia (`84532`):
- **Evidence Classification:** `SIMULATED — BASE SEPOLIA STATE`
- **Caller:** `0x473888C859F88D3De7b5A20986805b4dC3178189`
- **Attempted Beneficiary:** `0x473888C859F88D3De7b5A20986805b4dC3178189`
- **Result:** EVM revert with selector `0x60ef2d64`
- **Decoded Revert:** `SelfPaymentNotAllowed(0x473888C859F88D3De7b5A20986805b4dC3178189)`
- **Verification:** PASS. Self-dealing is rejected by contract bytecode without state mutation.

---

## 4. Live Base Sepolia Transaction Registry (Phase 6.9.4)

Five live on-chain transactions were executed and confirmed on Base Sepolia. The existing USDC allowance was separately verified through on-chain state observation, and the 32-block confirmation requirement was separately verified through live chain-head observation.

| Step | Operation | Tx Hash | Block | Gas | Evidence |
| ---- | --------- | ------- | ----- | --- | -------- |
| 1 | Agent Registration | `0x21d4342e89713f0e21b4a1e5334d73290c681745b193a08af8f9d0a3502f2d1b` | 47555019 | 218,910 | LIVE — EXTERNAL CHAIN TRANSACTION |
| 2 | USDC Allowance Check | N/A | 47555244 observation block | 0 | LIVE — EXTERNAL CHAIN STATE READ |
| 3 | Escrow Funding | `0xd7608fbe0f16ddeaa953cfde6f5e466f1df5953ce1abc2e1e5853914fc07d031` | 47555021 | 277,552 | LIVE — EXTERNAL CHAIN TRANSACTION |
| — | 32-Block Confirmation | N/A | 47555249 head | N/A | LIVE — EXTERNAL CHAIN STATE OBSERVATION |
| 4 | Result Notarization | `0xd916bb5f13a0c7cdf382822eea5d3ee13d9e78b5d19d979e129f32cdd1536dbd` | 47555222 | 153,835 | LIVE — EXTERNAL CHAIN TRANSACTION |
| 5 | Settlement Release | `0x1d681f66ecd9c9b2db2dd4f7f80eae38f247b79c481f23c47245b8d401850cb5` | 47555252 | 183,098 | LIVE — EXTERNAL CHAIN TRANSACTION |
| 6 | Reputation Event | `0xf701ad4ce6954889f287fb2346e660582a108f97566a920566fc22a4d9792ec1` | 47555255 | 223,509 | LIVE — EXTERNAL CHAIN TRANSACTION |

> **Important Operational & Accounting Clarifications:**  
> - **5 live on-chain transactions**: The row numbering does NOT imply six transactions. Exactly 5 live state-changing transactions were broadcast and mined on Base Sepolia.  
> - **USDC Allowance Check (`LIVE — EXTERNAL CHAIN STATE READ`)**: Existing USDC allowance observed on-chain; no approval transaction was broadcast during this phase. Existing allowance was sufficient ($2^{256} - 1$). Gas used: 0.  
> - **32-Block Confirmation (`LIVE — EXTERNAL CHAIN STATE OBSERVATION`)**: Depth confirmation is an external chain-head state observation, not a blockchain transaction.  

---

## 5. 32-Block Reorg Confirmation Evidence

The protocol strictly forbids advancing escrow state to `ESCROW_FUNDED` or enqueuing execution until the funding transaction has reached at least 32 block confirmations:
- **Evidence Classification:** `LIVE — EXTERNAL CHAIN STATE OBSERVATION`
- **Funding Transaction Block:** `47555021`
- **Required Depth:** 32 blocks (target block $\ge 47555053$)
- **Observed Chain-Head Block:** `47555249`
- **Observed Confirmation Depth:** 228 blocks
- **Result:** Depth condition ($228 \ge 32$) satisfied. Escrow state confirmed canonical.

---

## 6. Economic Distribution & Conservation (85/10/5)

Gross escrow amount: 100,000 atomic units ($0.10$ USDC).  
Settlement transaction `0x1d681f66ecd9c9b2db2dd4f7f80eae38f247b79c481f23c47245b8d401850cb5` triggered atomic distribution via `RevenueDistributor`:

| Recipient | Target Address | Share % | Expected Delta | Observed Delta | Balance Delta Confirmed |
|---|---|---|---|---|---|
| **Developer** | `0x162dEB52f30f551020c23155Bc5c66419c184d82` | 85.00% | +85,000 | +85,000 | `0 -> 85,000` |
| **Stakers** | `0x51a0000000000000000000000000000000000001` | 10.00% | +10,000 | +10,000 | `10,000 -> 20,000` |
| **DAO Treasury** | `0xda00000000000000000000000000000000000002` | 5.00% | +5,000 | +5,000 | `5,000 -> 10,000` |
| **Total** | — | **100.00%** | **100,000** | **100,000** | **Exact Conservation** |

$$\text{Conservation Verification}: 85,000 + 10,000 + 5,000 = 100,000 \quad (\Delta_{\text{sum}} == \text{Gross Released})$$

---

## 7. Database ↔ Blockchain State Reconciliation

Reconciliation between the PostgreSQL persistence layer and Base Sepolia contracts for Order `d89c27e8-be90-48b0-82c4-5ae1a0eab3d9`:

| Field | Database Record | On-Chain Contract State | Status |
|---|---|---|---|
| **Order ID** | `d89c27e8-be90-48b0-82c4-5ae1a0eab3d9` | Referenced via `referenceId` hash | **MATCH** |
| **Agent ID** | `fcacd91d-1205-4d78-b817-8d37bed9c4b4` | Registered in `AgentRegistry` | **MATCH** |
| **Agent Version** | `1.0.0` (ID: `b48e5d33-fa72-4863-a0f2-52139898aacd`) | Pinned in Order & Notarization payload | **MATCH** |
| **Developer Wallet** | `0x162deb52f30f551020c23155bc5c66419c184d82` | Beneficiary of Escrow `0x6bf3...` | **MATCH** |
| **Order Price** | 100,000 atomic units ($0.10$ USDC) | Escrow funded with 100,000 units | **MATCH** |
| **Escrow ID** | `0x6bf30c8e0fa93e612faccf688c3fc65f7f9a7ac2add13a3bf89fc7cdd59c7d28` | State: `RELEASED` (3) | **MATCH** |
| **Execution ID** | `ffa7fc60-63da-4b1f-a831-60cee84e74a7` | Registered in `ResultNotary` proof | **MATCH** |
| **Result Hash** | `4821a2395b880cf251c43acca103959455ee5be02a80a6749b21a0dbb3b3037f` | Verified via `notary.verifyProof()` | **MATCH** |
| **Settlement Action** | `RELEASE` (`CONFIRMED`) | Tx `0x1d681f66...` mined in block 47555252 | **MATCH** |
| **Reputation Event** | `CONFIRMED` (`VERIFIED_SUCCESS`) | Tx `0xf701ad4c...` mined in block 47555255 | **MATCH** |
| **Order Status** | `SETTLED` | Terminal state reached | **MATCH** |

Cross-system discrepancies: **0**.

---

## 8. Financial Reconciliation & Balances

### Client / Operator Wallet (`0x473888C859F88D3De7b5A20986805b4dC3178189`)
- **Starting ETH:** `0.044920932273401962` ETH
- **ETH Received:** `0.0` ETH
- **ETH Spent on Gas:** `0.000006115208203786` ETH
- **ETH Sent Elsewhere:** `0.0` ETH
- **Ending ETH:** `0.044914817065198176` ETH
- **Equation:** $0.044920932273401962 - 0.000006115208203786 = 0.044914817065198176$ (Exact)

### USDC Balances Across All Protocol Participants
- **Client Starting USDC:** `622,988` units
- **Client Escrow Funding Deposit:** $-100,000$ units
- **Client Ending USDC:** `522,988` units
- **Developer Starting USDC:** `0` units
- **Developer Ending USDC:** `85,000` units ($+85,000$)
- **Staker Starting USDC:** `10,000` units
- **Staker Ending USDC:** `20,000` units ($+10,000$)
- **DAO Starting USDC:** `5,000` units
- **DAO Ending USDC:** `10,000` units ($+5,000$)
- **Gross USDC Inflow to Escrow:** $100,000$ units
- **Gross USDC Outflow from Escrow:** $85,000 + 10,000 + 5,000 = 100,000$ units (Conserved)

---

## 9. Replay Protection Verification

- **Evidence Classification:** `SIMULATED — BASE SEPOLIA STATE`  
- Replay-protection checks were simulated against the deployed Base Sepolia contract state; no duplicate state-mutating transaction was intentionally mined.

Following order completion, adversarial replay transactions were simulated directly against Base Sepolia:
1. **Duplicate Notarization:** Reverted with `AlreadyNotarized(bytes32)` selector `0x37f5e398`.
2. **Duplicate Reputation Registration:** Reverted with `AlreadyRegistered(bytes32)` selector `0x51c7263c`.
3. **Duplicate Escrow Release:** Reverted with `InvalidEscrowState(bytes32,uint8)` selector `0x33e00e36`.

Zero duplicate payouts or double-registrations occurred.

---

## 10. Regression Testing Suite

| Test Suite | Total Tests | Passed | Failed | Skipped | Status |
|---|---|---|---|---|---|
| **Foundry Invariant & Unit Tests** | 115 | 115 | 0 | 0 | **PASS** |
| **Backend Marketplace & Settlement Tests** | 138 | 138 | 0 | 0 | **PASS** |
| **Services Indexer & Agent SDK Tests** | 8 | 8 | 0 | 0 | **PASS** |
| **Web Frontend Typecheck (`tsc --noEmit`)** | Clean | Clean | 0 | 0 | **PASS** |
| **Configuration Drift Invariant Scanner** | Clean | Clean | 0 | 0 | **PASS** |
| **Total** | **261** | **261** | **0** | **0** | **PASS** |

---

## 11. Evidence Taxonomy

The evidence taxonomy across Phase 6.9.4 adheres strictly to the following standards:

- **LIVE — EXTERNAL CHAIN TRANSACTION:**
  - Broadcast, mined transactions confirmed on Base Sepolia with valid hash and receipt.
  - Exactly 5 live state-changing transactions: Agent registration, Escrow funding, Result notarization, Escrow settlement/release, Reputation event registration.
- **LIVE — EXTERNAL CHAIN STATE READ:**
  - Non-mutating `eth_call`, balance checks, allowance reads, contract storage reads, and bytecode reads.
  - USDC allowance read confirming existing sufficient allowance ($2^{256} - 1$); no approval transaction was broadcast.
- **LIVE — EXTERNAL CHAIN STATE OBSERVATION:**
  - Chain-head observation, confirmation depth verification, and canonicality checks.
  - 32-block depth observation: funding block `47555021`, head `47555249`, observed depth `228 >= 32`.
- **LOCAL — EXECUTED:**
  - Marketplace order creation and indexing in PostgreSQL.
  - Canonical RFC-8785 result hashing.
  - 261 passing unit, invariant, integration, and typecheck tests.
- **SIMULATED — BASE SEPOLIA STATE:**
  - Non-destructive `eth_call` simulations against deployed contract state.
  - Self-payment protection verification (`SelfPaymentNotAllowed`, selector `0x60ef2d64`).
  - Replay protection verification (duplicate notarization, duplicate reputation registration, duplicate escrow release).
- **STATIC — INSPECTED:**
  - Contract deployment addresses and ABI interfaces.
  - Git repository `.gitignore` ensuring zero private key exposure.
- **NOT EXECUTED:**
  - Public testnet reorg manipulation (reorg handling verified in local/integration test suites).

---

## 12. Release Gate Decision

### **RELEASE GATE: PASSED ✅**

All Phase 6.9.4 hardening requirements have been fulfilled with independently verifiable on-chain evidence on Base Sepolia (`84532`). Exactly 5 live state-changing transactions were executed and confirmed, USDC allowance was verified via on-chain state read, 32-block depth was verified via live chain-head observation, and self-payment and replay protections were verified via EVM simulation against live contract state. Base Mainnet remains strictly blocked.
