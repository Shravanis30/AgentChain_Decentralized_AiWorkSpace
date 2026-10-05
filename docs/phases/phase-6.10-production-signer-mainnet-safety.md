# Phase 6.10: Production Signer & Mainnet Safety Readiness

## 1. Executive Summary

Phase 6.10 establishes the operational security, signer custody abstraction, fail-closed network safety gates, and deployment guards necessary to prepare AgentChain for future Mainnet readiness without deploying to or broadcasting any transaction on Base Mainnet (`Chain ID 8453`).

Base Mainnet remains strictly **HARD BLOCKED**. Zero state-changing transactions were broadcast on Base Mainnet or Base Sepolia during this phase. All live chain evidence is strictly read-only (`eth_call`, `eth_getCode`, `eth_chainId`, `eth_blockNumber`).

## 2. Scope & Core Invariants

1. **Base Mainnet (8453)**: Hard blocked across contracts, relayer, signers, configs, and CI.
2. **Base Sepolia (84532)**: Canonical deployments preserved with zero address changes and zero state modification.
3. **Production Signer Abstraction**: Clean boundary separating `LocalDevelopmentSigner`, `TestnetAccountSigner`, and `ProductionSigner`. Raw private keys are strictly forbidden in production.
4. **Key Custody Status**: KMS, HSM, and MPC adapters are implemented as fail-closed boundary interfaces. Because live KMS/HSM cloud providers are not provisioned in this repository, production signing is recorded as **BLOCKED / NOT IMPLEMENTED**, and the overall release gate remains **PARTIAL**.
5. **Role Separation**: Explicit roles required (`relayer_role_address`, `admin_role_address`, etc.). Anvil default accounts (`0xf39Fd6...`) and retired accounts are blocked on live networks.
6. **Zero Leaks**: Verified via automated CI scanner that git-tracked files contain no secrets or private keys.

## 3. Architecture & Components

### 3.1 Signer Hierarchy (`backend/app/services/blockchain/signer.py`)
- `TransactionSigner (ABC)`: Base class enforcing `verify_network_compatibility`, `get_address`, `sign_transaction`, and `health_check`.
- `LocalDevelopmentSigner`: Strictly restricted to chain `31337` (Anvil).
- `TestnetAccountSigner`: Restricted to chain `84532` (Base Sepolia). Blocks Anvil default deterministic keys.
- `ProductionSigner (ABC)`: Strictly forbids raw private keys in memory or configuration.
- `KmsSignerAdapter`, `HsmSignerAdapter`, `MpcSignerAdapter`: Fail-closed adapters that raise `ProductionSignerUnavailableError`.
- `SignerFactory`: Enforces environment isolation (`DEVELOPMENT`, `TESTNET`, `PRODUCTION`).

### 3.2 Deployment Guard (`contracts/script/Deploy.s.sol`)
- Enforces `deployWithConfig(DeploymentConfig)` and `loadConfigFromEnv()`.
- Reverts `MainnetDeploymentBlocked` on chain `8453`.
- Reverts `AnvilAccountBlockedOnLiveNetwork` if Anvil default key is used on Base Sepolia.
- Requires explicit non-zero addresses for all roles on Base Sepolia.
- Enforces official Circle Native USDC token address binding.

### 3.3 Relayer Hardening (`backend/app/services/blockchain/relayer.py`)
- `verify_startup()`: Fail-closed verification of RPC chain ID, Mainnet block, signer network compatibility, signer health check, and default test account blocking.
- `submit_intent()`: Enforces intent chain binding (`intent.chain_id == self.chain_id`), signer network compatibility, operation allowlist, and target contract allowlist.

### 3.4 Production Configuration Model (`backend/app/core/production_config.py` & `.env.production.template`)
- Pydantic v2 validation model enforcing environment isolation.
- Blocks debug mode, raw private keys, Anvil accounts, and Base Mainnet in production.
- Validates canonical contract address bindings for Base Sepolia.

### 3.5 Automated Security Gate (`scripts/verify_production_safety_gates.py`)
- Automated script scanning tracked git files, `.gitignore`, `.dockerignore`, contract guards, relayer guards, and signer boundaries.
- Integrated directly into `.github/workflows/backend.yml`.

## 4. Base Sepolia Live Read-Only Verification

All 6 canonical contracts and official Circle USDC were verified via live RPC queries to `https://sepolia.base.org`:
- Chain ID: `84532` (LIVE — EXTERNAL CHAIN STATE OBSERVATION)
- Block Number: `47582293` (LIVE — EXTERNAL CHAIN STATE OBSERVATION)
- Bytecode confirmed present for:
  - AgentRegistry (`0x96C3945e264A880EbaB5e95fDF786Fc18A4389B5`): 2846 bytes
  - RevenueDistributor (`0x2F2d5Cc40e2C4C32cC86BC5127c2833a7784ee1c`): 3793 bytes
  - Escrow (`0x3e5C09D2123cA106b2E24C4f76bFad4a022640D1`): 8314 bytes
  - ResultNotary (`0xed58B4E37f81dFbD08c3d2DeF613286bC8Cf8056`): 2061 bytes
  - ReputationRegistry (`0x423856529583F536d5dFaE80f7Bc142075c6Fc71`): 3032 bytes
  - USDC (`0x036CbD53842c5426634e7929541eC2318f3dCF7e`): 1798 bytes
- On-chain configurations confirmed:
  - `Escrow.paymentToken` == `0x036CbD53842c5426634e7929541eC2318f3dCF7e`
  - `Escrow.distributor` == `0x2F2d5Cc40e2C4C32cC86BC5127c2833a7784ee1c`
  - `RevenueDistributor.paymentTokenContract` == `0x036CbD53842c5426634e7929541eC2318f3dCF7e`
  - `RevenueDistributor.authorizedEscrow` == `0x3e5C09D2123cA106b2E24C4f76bFad4a022640D1`
  - `ResultNotary`: Operator has `DEFAULT_ADMIN_ROLE` and `NOTARIZER_ROLE`.
  - `ReputationRegistry`: Bound to canonical `ResultNotary`; Operator has `REPUTATION_ORACLE_ROLE` and `DEFAULT_ADMIN_ROLE`.
  - `USDC`: Decimals = `6`, Symbol = `"USDC"`.

## 5. Evidence Taxonomy

| Taxonomy Tier | Count | Description |
| :--- | :---: | :--- |
| **LIVE — EXTERNAL CHAIN TRANSACTION** | **0** | Zero on-chain state-changing transactions broadcast |
| **LIVE — EXTERNAL CHAIN STATE READ** | **21** | 6 bytecode checks + 15 on-chain state reads via `eth_call` / `eth_getCode` |
| **LIVE — EXTERNAL CHAIN STATE OBSERVATION** | **2** | `eth_chainId` (84532) and `eth_blockNumber` (47582293) |
| **LOCAL — EXECUTED** | **143** | 122 Foundry tests (128,000 invariant checks) + 21 targeted pytest tests |
| **SIMULATED — BASE SEPOLIA STATE** | **1** | `Deploy.s.sol` dry-run simulation |
| **STATIC — INSPECTED** | **8** | 8 automated security checks in `verify_production_safety_gates.py` |
| **NOT EXECUTED** | **3** | AWS KMS live signing, HSM hardware signing, Base Mainnet deployment |

## 6. Release Gate Classification

| Area | Status | Evaluation Notes |
| :--- | :---: | :--- |
| **Production Signer** | **BLOCKED** | Interface & fail-closed adapter only; live KMS/HSM provider not provisioned |
| **Secure Key Custody** | **BLOCKED** | No cloud KMS or hardware HSM provisioned in local environment |
| **Network Isolation** | **PASS** | Base Mainnet (8453) strictly hard blocked across all layers |
| **Role Separation** | **PASS** | Anvil accounts and role fallbacks rejected in production |
| **CI Secret Protection** | **PASS** | Secret scanner gate passes; `.dockerignore` and `.gitignore` hardened |
| **Deployment Safety** | **PASS** | `Deploy.s.sol` enforces explicit keys, roles, and token checks |
| **Relayer Safety** | **PASS** | Intent chain binding, signer/network mismatch checks verified |
| **Mainnet Protection** | **PASS** | Multiple independent fail-closed safety barriers active |
| **Base Sepolia Verification** | **PASS** | 100% read-only verification of bytecode and state contracts |

**Overall Gate Status:**
`PARTIAL — SECURITY HARDENING COMPLETE, PRODUCTION SIGNER INTEGRATION PENDING`
