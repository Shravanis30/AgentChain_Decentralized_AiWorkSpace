# Phase 6.9.3 — Live Base Sepolia Marketplace E2E Transaction Validation

**Date:** 2026-10-01  
**Status:** **RELEASE GATE PASSED** ✅  
**Target Network:** Base Sepolia (Chain ID: `84532`)  
**Base Mainnet Guard:** Strictly Blocked (`BLOCK_MAINNET_SETTLEMENT = True`, `MainnetDeploymentBlocked` enforced)  
**Operator / Client Address:** `0x473888C859F88D3De7b5A20986805b4dC3178189`  
**Developer Recipient Address:** `0x70997970C51812dc3A010C7d01b50e0d17dc79C8`  

---

## 1. Executive Summary

Phase 6.9.3 represents the live end-to-end execution and validation of the entire AgentChain marketplace protocol on the **Base Sepolia testnet**.

Six live on-chain transactions were confirmed on Base Sepolia. The 32-block confirmation requirement was verified separately through live chain-head observation.

All components deployed during Phase 6.9.2 were tested under live conditions:
- **Agent Registry:** Live on-chain agent metadata registration.
- **Marketplace Order:** Deterministic selection, agent/version pinning, and budget validation (`LOCAL — EXECUTED`).
- **USDC Escrow Funding:** Live ERC-20 approval and on-chain escrow deposit of 100,000 atomic units (0.10 USDC).
- **Confirmation Depth:** 32-block confirmation policy strictly verified via live chain-head observation (confirmed at depth 121).
- **Execution & Notarization:** RFC-8785 canonical result hashing and on-chain `ResultNotary` proof registration.
- **Settlement & Distribution:** Protocol settlement through `Escrow.releaseEscrow`, routing through `RevenueDistributor` with verified 85/10/5 economic split.
- **Reputation Event:** `ReputationRegistry` canonical event registration linked to the live notarization proof.
- **State Reconciliation:** End-to-end reconciliation between PostgreSQL and Base Sepolia.

---

## 2. Live Contract & Network Configuration

| Entity | Address |
|---|---|
| **Network RPC** | `https://sepolia.base.org` |
| **Chain ID** | `84532` |
| **AgentRegistry** | `0x96C3945e264A880EbaB5e95fDF786Fc18A4389B5` |
| **RevenueDistributor** | `0x2F2d5Cc40e2C4C32cC86BC5127c2833a7784ee1c` |
| **Escrow** | `0x3e5C09D2123cA106b2E24C4f76bFad4a022640D1` |
| **ResultNotary** | `0xed58B4E37f81dFbD08c3d2DeF613286bC8Cf8056` |
| **ReputationRegistry** | `0x423856529583F536d5dFaE80f7Bc142075c6Fc71` |
| **Native USDC (Circle)** | `0x036CbD53842c5426634e7929541eC2318f3dCF7e` |

---

## 3. End-to-End Live Transaction Pipeline

```
[AgentRegistry.registerAgent]
        │  (Tx: 0x4be5c5d0... block 47553764)
        ▼
[Deterministic Selection & Marketplace Order Created]
        │  (Order fb579cd4-63db-4f93-a7c0-88f07b8b4a2c)
        ▼
[USDC.approve(Escrow, 100000)]
        │  (Tx: 0x3535d1ff... block 47553766)
        ▼
[Escrow.fundEscrow(escrowId, 100000)]
        │  (Tx: 0xc52c1cb5... block 47553913)
        ▼
[32-Block Confirmation Depth Wait]
        │  (Block 47553913 -> 47554034, 121 confirmations >= 32)
        ▼
[Task Execution & RFC-8785 Result Hashing]
        │  (Hash: 0x5ecc2c53...edd6)
        ▼
[ResultNotary.notarizeResult]
        │  (Tx: 0x8e6ee40a... block 47554036)
        ▼
[Escrow.releaseEscrow -> RevenueDistributor]
        │  (Tx: 0x5354a227... block 47554039)
        │  ├── Developer (+85,000 units)
        │  ├── Stakers   (+10,000 units)
        │  └── DAO       (+5,000 units)
        ▼
[ReputationRegistry.registerReputationEvent]
        │  (Tx: 0xef8c0dae... block 47554043)
        ▼
[DB <-> Chain Reconciliation (0 Discrepancy)]
```

---

## 4. Live Evidence Table

| Step | Status | Tx Hash | Block Number | Gas Used | Evidence Tier |
|---|---|---|---|---|---|
| **Agent Registration** | **PASS** | `0x4be5c5d0ca4cbd268c58d4f0e57eb102f85ea38d9220babdd3cfe8f0d12e5db0` | 47553764 | 196,197 | `LIVE — EXTERNAL CHAIN` |
| **Marketplace Order** | **PASS** | N/A (Internal DB ID: `fb579cd4-63db-4f93-a7c0-88f07b8b4a2c`) | N/A | N/A | `LOCAL — EXECUTED` |
| **USDC Approval** | **PASS** | `0x3535d1ffdfcad06943e1f767e2023b03838e6cdcb12dc3a48ad70798634a89e4` | 47553766 | 46,835 | `LIVE — EXTERNAL CHAIN` |
| **Escrow Funding** | **PASS** | `0xc52c1cb55b0575584be54da6b86a61b92f819c591ae4cd876f348765e42a30f3` | 47553913 | 277,552 | `LIVE — EXTERNAL CHAIN` |
| **32-Block Confirmation** | **PASS** | Mined: 47553913, Verified: 47554034 (121 depth) | 47554034 | N/A | `LIVE — EXTERNAL CHAIN` |
| **Execution** | **PASS** | Execution ID: `a7285e41-cf01-488f-bd85-a6e55503ed6a` | N/A | N/A | `LOCAL — EXECUTED` |
| **Result Notarization** | **PASS** | `0x8e6ee40a065fc838c9a27959a6db786fabb2d36147dc7edf9426536c60cc33bf` | 47554036 | 160,734 | `LIVE — EXTERNAL CHAIN` |
| **Escrow Settlement** | **PASS** | `0x5354a2277fbb0bece9115fbf46fb4a20b594bec6b410dfc39dbaf3f702f3ca1f` | 47554039 | 200,198 | `LIVE — EXTERNAL CHAIN` |
| **85/10/5 Distribution** | **PASS** | Mined in settlement transaction above | 47554039 | In Settlement | `LIVE — EXTERNAL CHAIN` |
| **Reputation Event** | **PASS** | `0xef8c0dae14d5cba508a76eb58cdf665f9ed16fca4fd89f443f88e64cfe9a1dc3` | 47554043 | 223,509 | `LIVE — EXTERNAL CHAIN` |
| **Final Reconciliation** | **PASS** | DB Order & Blockchain Escrow Synchronized | 47554043 | N/A | `LIVE — EXTERNAL CHAIN` |

---

## 5. Economic Conservation Verification

The gross settlement amount of 100,000 atomic units was released and distributed by `RevenueDistributor`:
- **Developer (`0x70997970C51812dc3A010C7d01b50e0d17dc79C8`):** $+85,000$ units ($85.00\%$)
- **Stakers (`0x51a0000000000000000000000000000000000001`):** $+10,000$ units ($10.00\%$)
- **DAO Treasury (`0xda00000000000000000000000000000000000002`):** $+5,000$ units ($5.00\%$)

$$\text{Total Distributed} = 85,000 + 10,000 + 5,000 = 100,000 \quad (\text{Conserved})$$

---

## 6. Regression Testing Summary

| Test Suite | Total Tests | Passed | Failed |
|---|---|---|---|
| Foundry Invariants & Units | 115 | 115 | 0 |
| Phase 6.9 Live Validation | 16 | 16 | 0 |
| Marketplace Lifecycle | 15 | 15 | 0 |
| Settlement & Accounting | 34 | 34 | 0 |
| Reputation & Notarization | 82 | 82 | 0 |
| Production Readiness | 24 | 24 | 0 |
| Agent Selection Engine | 42 | 42 | 0 |
| Blockchain Hardening | 13 | 13 | 0 |
| Agent SDK | 6 | 6 | 0 |
| Frontend Typecheck | Clean | Clean | 0 |
| Config Drift Invariants | Clean | Clean | 0 |
| **Total** | **347** | **347** | **0** |

---

## 7. Limitations & Exclusions

- **Live Public Reorg Testing:** Not practically forceable on public Base Sepolia testnet without an active 51% mining attack. This is classified as `NOT EXECUTED — NOT PRACTICALLY FORCEABLE ON PUBLIC TESTNET`. Deep reorg handling and canonical event reconciliation were validated locally under invariant test suites.
