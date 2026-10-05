# Phase 6.9.2 — Live Base Sepolia Deployment & On-Chain Verification

**Date:** 2026-10-01  
**Status:** **RELEASE GATE PASSED** ✅  
**Chain ID:** 84532 (Base Sepolia)  
**Mainnet Guard:** Strictly Blocked (`BLOCK_MAINNET_SETTLEMENT = True`, `MainnetDeploymentBlocked()` active)  
**Deployer Address:** `0x473888C859F88D3De7b5A20986805b4dC3178189`  
**Deployer Balance Remaining:** `0.049969 ETH` (Gas Paid: `0.000030069 ETH`)  
**Forge Version:** 1.8.3  

---

## 0. Absolute Safety Rules — Status

| Safety Rule | Status |
|---|---|
| Target network locked to Base Sepolia (Chain ID 84532) | ENFORCED |
| Chain ID confirmed live via eth_chainId = 84532 | PASS |
| Base Mainnet hard-block active (MainnetDeploymentBlocked(), Chain ID 8453) | ENFORCED |
| Private key never printed or logged | ENFORCED |
| No secrets in logs, output, or report | ENFORCED |
| No .env committed to repo | ENFORCED |
| No broadcast secrets committed (.gitignore active) | ENFORCED |

---

## 1. Executive Summary

Phase 6.9.2 was initiated to perform the **actual Base Sepolia live deployment** of all AgentChain smart contracts after the operator provisioned and bridged 0.05 ETH to deployer wallet `0x473888C859F88D3De7b5A20986805b4dC3178189`.

**Pre-flight and deployment checklist execution:**

1. **PASS** — Base Sepolia RPC connectivity confirmed — Block 47,550,830
2. **PASS** — Chain ID 84532 verified via eth_chainId RPC call
3. **PASS** — Base Mainnet hard-block confirmed active
4. **PASS** — Circle USDC `0x036CbD53842c5426634e7929541eC2318f3dCF7e` verified on-chain
5. **PASS** — Deploy.s.sol dry-run simulated with real deployer (`0x473888...`)
6. **PASS** — Live broadcast executed on Base Sepolia (`Sequence #1 on base-sepolia`)
7. **PASS** — All 5 contracts deployed with bytecode confirmed via RPC
8. **PASS** — Two-way Escrow <-> RevenueDistributor binding confirmed via RPC
9. **PASS** — All protocol roles (`DEFAULT_ADMIN_ROLE`, `ARBITRATOR_ROLE`, `PAUSER_ROLE`, `NOTARIZER_ROLE`, `REPUTATION_ORACLE_ROLE`) verified on-chain
10. **PASS** — Config drift: ZERO DRIFT (`scripts/check_config_drift.py`)
11. **PASS** — Foundry test suite: 115/115 passed (128,000 invariant calls, 0 reverts)
12. **PASS** — Services & SDK test suites: 8/8 passed
13. **PASS** — Frontend typecheck: Clean (0 errors)

---

## 2. Real Deployer Verification

- **Configured Deployer:** `0x473888C859F88D3De7b5A20986805b4dC3178189` (Non-Anvil, verified operator wallet)
- **Base Sepolia Initial Balance:** `0.050000000000000000 ETH`
- **Total Paid for Deployment:** `0.000030069228 ETH` (5,011,538 gas * avg 0.006 gwei)
- **Final Balance:** `0.049969930771999999 ETH`

---

## 3. Role Configuration

| Role | Environment Variable | Status | Resolved Address |
|---|---|---|---|
| admin | `ADMIN_ADDRESS` | CONFIGURED | `0x473888C859F88D3De7b5A20986805b4dC3178189` |
| arbitrator | `ARBITRATOR_ADDRESS` | CONFIGURED | `0x473888C859F88D3De7b5A20986805b4dC3178189` |
| pauser | `PAUSER_ADDRESS` | CONFIGURED | `0x473888C859F88D3De7b5A20986805b4dC3178189` |
| notarizer | `NOTARIZER_ADDRESS` | CONFIGURED | `0x473888C859F88D3De7b5A20986805b4dC3178189` |
| stakerRecipient | `STAKER_RECIPIENT` | CONFIGURED | `0x51a0000000000000000000000000000000000001` |
| daoRecipient | `DAO_RECIPIENT` | CONFIGURED | `0xda00000000000000000000000000000000000002` |
| reputationOracle | `REPUTATION_ORACLE_ADDRESS` | CONFIGURED | `0x473888C859F88D3De7b5A20986805b4dC3178189` |

---

## 4. Live Broadcast & On-Chain Addresses

Broadcast Command:
```bash
forge script script/Deploy.s.sol:DeployScript --rpc-url https://sepolia.base.org --broadcast -vvv
```

Broadcast Block: **47550830**

| Contract | On-Chain Address | Broadcast Tx Hash | Gas Paid |
|---|---|---|---|
| **AgentRegistry** | `0x96C3945e264A880EbaB5e95fDF786Fc18A4389B5` | `0x749a705fbbe68d0aff346ec08fbd1c8aa81e8079992c53ac4fa18d74731d6972` | 0.000004197 ETH |
| **RevenueDistributor** | `0x2F2d5Cc40e2C4C32cC86BC5127c2833a7784ee1c` | `0x808fd1fa73b68374180b14f74382337933da0e822140a1d5ff89689443859a8b` | 0.000005782 ETH |
| **Escrow** | `0x3e5C09D2123cA106b2E24C4f76bFad4a022640D1` | `0x3c426208be371e186ac89d00fbb9bc3fb8bbd7d7e5120c4a8a71e4e95506ea3b` | 0.000011871 ETH |
| **setEscrowContract** | Call to RevenueDistributor | `0x11a0eaeb3efe46b0ea208a039c6cfa7415a42ee435e2f123a2335040712145f5` | 0.000000286 ETH |
| **ResultNotary** | `0xed58B4E37f81dFbD08c3d2DeF613286bC8Cf8056` | `0xcdfb3d341e687c0ba522a74dd3033c8ade0a3a2e8362591c29cd7fb60a0dd506` | 0.000003332 ETH |
| **ReputationRegistry** | `0x423856529583F536d5dFaE80f7Bc142075c6Fc71` | `0xde82718547f7c18aee2ac48dcaf14fab1987e5c3440574254c4e83c8e37492c0` | 0.000004598 ETH |

---

## 5. Live State Invariants Verified

1. **`AgentRegistry.owner()`** == `0x473888C859F88D3De7b5A20986805b4dC3178189`
2. **`Escrow.paymentToken()`** == `0x036CbD53842c5426634e7929541eC2318f3dCF7e` (Circle USDC)
3. **`Escrow.distributor()`** == `0x2F2d5Cc40e2C4C32cC86BC5127c2833a7784ee1c`
4. **`RevenueDistributor.authorizedEscrow()`** == `0x3e5C09D2123cA106b2E24C4f76bFad4a022640D1`
5. **`RevenueDistributor.paymentTokenContract()`** == `0x036CbD53842c5426634e7929541eC2318f3dCF7e`
6. **`ResultNotary.hasRole(NOTARIZER_ROLE)`** == `true` for deployer
7. **`ReputationRegistry.hasRole(REPUTATION_ORACLE_ROLE)`** == `true` for deployer
8. **`ReputationRegistry.resultNotary()`** == `0xed58B4E37f81dFbD08c3d2DeF613286bC8Cf8056`

---

## 6. Release Gate Decision

### 🟢 RELEASE GATE PASSED

All prerequisites, dry runs, live broadcast transactions, and on-chain state verifications have been fully met with 100% adherence to safety and protocol requirements.

Phase 6.9.2 is complete and unblocks **Phase 6.9.3 (Live Base Sepolia Marketplace E2E Transaction Validation)**.
