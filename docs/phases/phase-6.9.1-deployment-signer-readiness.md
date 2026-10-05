# Phase 6.9.1 — Base Sepolia Deployment & Signer Readiness Report

**Date:** 2026-09-30  
**Phase:** 6.9.1  
**Target Network:** Base Sepolia Testnet (Chain ID: 84532)  
**Safety Gate:** Base Mainnet (Chain ID: 8453) Strictly Blocked (`BLOCK_MAINNET_SETTLEMENT = True`, `allow_transactions = False`)  
**Status:** **DEPLOYER FUNDING BLOCKER** (Deployment script simulated and verified against live Base Sepolia RPC; live broadcast blocked due to unset deployer/relayer credentials)

---

## 1. Executive Summary

Phase 6.9.1 is a dedicated infrastructure enablement and validation phase designed to prepare AgentChain for live on-chain execution on Base Sepolia.

Phase 6.9 previously verified the Base Sepolia RPC endpoint (`https://sepolia.base.org`) and the official Circle USDC token contract (`0x036CbD53842c5426634e7929541eC2318f3dCF7e`), but concluded with `RELEASE GATE BLOCKED — INFRASTRUCTURE` because:
1. AgentChain smart contracts were not yet deployed on Base Sepolia.
2. No funded deployer, relayer, or user wallet keys were configured in the execution environment.

In Phase 6.9.1, we performed an exhaustive audit and dry-run validation of the existing deployment system without introducing second mechanisms or altering contract architecture:
1. **Audit of `contracts/script/Deploy.s.sol`**: Fully verified contract creation order, dependency resolution, role assignments, immutable variables, and strict fail-closed Mainnet protection.
2. **Live Base Sepolia Simulation**: Executed `forge script script/Deploy.s.sol:DeployScript --rpc-url https://sepolia.base.org` against the live Base Sepolia network. The dry-run simulation completed with **zero errors and zero reverts**, establishing an exact gas estimate of **6,518,871 gas** (~0.000072 ETH at current network base fee).
3. **Live Base Sepolia RPC & USDC Verification**: Verified RPC connectivity (block height `47,500,464+`), chain ID `84532`, and verified on-chain that Circle USDC has `decimals = 6`, symbol `USDC`, name `USDC`.
4. **Signer Safety & Credential Audit**: Checked environment variables using zero-leakage introspection. All private key variables (`DEPLOYER_PRIVATE_KEY`, `RELAYER_PRIVATE_KEY`, `WALLET_PRIVATE_KEY`) are confirmed as `NOT SET`.
5. **Funding Blocker Classification**: In strict compliance with the Phase 6.9.1 specification, because no funded deployer key is present in the environment, the phase stops cleanly with **DEPLOYER FUNDING BLOCKER**. No live transaction broadcast was faked or attempted with empty credentials.
6. **Full Regression Health**: All existing regression suites remain pristine:
   - Backend unit and adversarial tests: **483 / 483 passed**.
   - Services (indexer, SDK, worker): **8 / 8 passed**.
   - Foundry smart contract suite: **115 / 115 passed** (including **128,000 invariant checks** with 0 reverts).
   - Production readiness & config drift: **24 / 24 passed**, 0 config drift.
   - Frontend web application (`apps/web`): Clean typecheck (`tsc --noEmit`).

---

## 2. Existing Deployment System Analysis (`Deploy.s.sol`)

The canonical deployment script is located at [contracts/script/Deploy.s.sol](file:///Users/shravani/Desktop/AgentChain_AIWorkSpace/contracts/script/Deploy.s.sol).

### 2.1 Contracts Deployed
1. **`AgentRegistry`**:
   - Constructor: `constructor(address initialOwner) Ownable(initialOwner)`
   - Deploys registry for developer agent profiles and version metadata.
2. **`RevenueDistributor`**:
   - Constructor: `constructor(address admin_, address escrow_, address usdc_, address stakerRecipient_, address daoRecipient_)`
   - Handles the canonical 85% developer / 10% stakers / 5% DAO revenue split.
3. **`Escrow`**:
   - Constructor: `constructor(address admin_, address arbitrator_, address pauser_, address usdc_, address revenueDistributor_)`
   - Holds client funds in USDC, controls dispute, release, and refund lifecycles.
4. **`ResultNotary`**:
   - Constructor: `constructor(address admin_, address notarizer_)`
   - Stores RFC-8785 execution result hashes and cryptographic proofs on-chain.
5. **`ReputationRegistry`**:
   - Constructor: `constructor(address admin_, address notaryContract_, address reputationOracle_)`
   - Records canonical reputation events linked to verified notarizations.

### 2.2 Deployment Order & Dependency Resolution
The deployment script executes the following sequential steps:
```
1. Chain ID Verification: Ensure block.chainid == 84532 (Base Sepolia) or 31337 (Local Anvil).
   Strictly revert with MainnetDeploymentBlocked() if block.chainid == 8453.
2. Circle USDC Validation: Confirm configured USDC == 0x036CbD53842c5426634e7929541eC2318f3dCF7e.
3. Deploy AgentRegistry(roles.admin)
4. Deploy RevenueDistributor(roles.admin, address(0), usdc, roles.stakerRecipient, roles.daoRecipient)
5. Deploy Escrow(roles.admin, roles.arbitrator, roles.pauser, usdc, address(distributor))
6. RevenueDistributor.setEscrowContract(address(escrow))  <-- Resolves circular dependency
7. Deploy ResultNotary(roles.admin, roles.notarizer)
8. Deploy ReputationRegistry(roles.admin, address(notary), roles.reputationOracle)
```

### 2.3 Dependency & Immutability Analysis
- **Circular Dependency Handling**:
  - `Escrow` requires `RevenueDistributor` at construction time to set its `immutable REVENUE_DISTRIBUTOR` address.
  - `RevenueDistributor` requires `Escrow` to authorize calls to `distributeFee`.
  - `Deploy.s.sol` cleanly resolves this by passing `address(0)` to `RevenueDistributor`'s constructor, deploying `Escrow` with the distributor address, and then executing `distributor.setEscrowContract(address(escrow))`. `setEscrowContract` is a protected one-time call restricted to `DEFAULT_ADMIN_ROLE`.
- **Immutable State Variables**:
  - `RevenueDistributor.USDC`, `RevenueDistributor.STAKER_RECIPIENT`, `RevenueDistributor.DAO_RECIPIENT`
  - `Escrow.USDC`, `Escrow.REVENUE_DISTRIBUTOR`
  - `ReputationRegistry.RESULT_NOTARY`
- **Role Assignments**:
  - `admin`: Granted `DEFAULT_ADMIN_ROLE` across all contracts (`AgentRegistry` owner).
  - `arbitrator`: Granted `ARBITRATOR_ROLE` in `Escrow`.
  - `pauser`: Granted `PAUSER_ROLE` in `Escrow`.
  - `notarizer`: Granted `NOTARIZER_ROLE` in `ResultNotary`.
  - `stakerRecipient`: Canonical recipient for the 10% staker share (`0x51a0000000000000000000000000000000000001` or env).
  - `daoRecipient`: Canonical recipient for the 5% DAO share (`0xda00000000000000000000000000000000000002` or env).
  - `reputationOracle`: Granted `REPUTATION_ORACLE_ROLE` in `ReputationRegistry`.
- **Deterministic vs Nonce-Dependent**:
  - The script uses standard EVM `CREATE` opcodes (`new Contract(...)`), meaning addresses are sequential and nonce-dependent based on the deployer account nonce (`keccak256(rlp([deployer, nonce]))`).

---

## 3. Base Sepolia Pre-Flight & Live Simulation Evidence

### 3.1 Live RPC Pre-Flight Verification
| Parameter | Required Value | Live Queried Value | Status |
|---|---|---|---|
| RPC URL | `https://sepolia.base.org` | `https://sepolia.base.org` | Reachable |
| Chain ID | `84532` | `84532` | MATCH |
| Latest Block | Available (>45,000,000) | `47,500,464+` | ACTIVE |
| Circle USDC Address | `0x036CbD53842c5426634e7929541eC2318f3dCF7e` | `0x036CbD53842c5426634e7929541eC2318f3dCF7e` | MATCH |
| USDC Symbol | `USDC` | `USDC` | MATCH |
| USDC Name | `USDC` | `USDC` | MATCH |
| USDC Decimals | `6` | `6` | MATCH |
| Mainnet Block | `True` | `BLOCK_MAINNET_SETTLEMENT = True` | ACTIVE |

### 3.2 Live Forge Script Dry-Run Simulation
We executed `Deploy.s.sol:DeployScript` targeting `https://sepolia.base.org` without broadcasting:
```bash
~/.foundry/bin/forge script script/Deploy.s.sol:DeployScript --rpc-url https://sepolia.base.org
```

**Simulation Output:**
```
Compiler run successful!
Script ran successfully.

== Logs ==
  Executing deployment on Chain ID: 84532
  Deployer: 0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266
  Admin: 0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266
  Arbitrator: 0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266
  Pauser: 0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266
  Staker Recipient: 0x51a0000000000000000000000000000000000001
  DAO Recipient: 0xda00000000000000000000000000000000000002
  Reputation Oracle: 0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266
  Using USDC at: 0x036CbD53842c5426634e7929541eC2318f3dCF7e
  AgentRegistry deployed at: 0x22D7A1bA8BCc6006B80ceAdeb805d9cCe3BA6F8c
  RevenueDistributor deployed at: 0x810cA0CcEc42D29f72b68BF0815CEE0A5F226678
  Escrow deployed at: 0x20eb288D6Be9cf68669c3B77E2B48dcAD7d7ed1C
  Bound Escrow to RevenueDistributor
  ResultNotary deployed at: 0x19B99a3A334d19676D7d596D020f6Cc9B4c9D754
  ReputationRegistry deployed at: 0x885D69Fd207299472935c41aDd252B0F97b3Fe42

==========================
Chain 84532
Estimated max fee per gas: 0.011 gwei
Estimated base fee per gas: 0.005 gwei
Estimated max priority fee per gas: 0.001 gwei

Estimated total gas used for script: 6518871
Estimated amount required: 0.000071707581 ETH
==========================
SIMULATION COMPLETE.
```

The simulation confirmed that all 5 contracts deploy, link, and configure without any bytecode size issues, compilation warnings, or execution reverts on the real Base Sepolia network.

---

## 4. Signer Safety & Environment Inspection

In strict conformance with Section 3 ("Never commit private keys or secrets; report only SET / NOT SET and public addresses"):

| Variable | Status | Derived Public Address | Balance (ETH) | Balance (USDC) |
|---|---|---|---|---|
| `DEPLOYER_PRIVATE_KEY` | **NOT SET** | N/A | 0.00 ETH | 0.00 USDC |
| `RELAYER_PRIVATE_KEY` | **NOT SET** | N/A | 0.00 ETH | 0.00 USDC |
| `WALLET_PRIVATE_KEY` | **NOT SET** | N/A | 0.00 ETH | 0.00 USDC |

### Security Measures Verified:
- No keys or mnemonic phrases in Git history or tracked files.
- `.gitignore` verifies that `.env`, `.env.*`, `contracts/broadcast/`, and `contracts/cache/` are completely untracked.
- No secrets printed to console or logs.
- Hardcoded test private keys (e.g., Anvil dev keys) are rejected if used on Chain ID 8453 (Mainnet guard verified via `test_local_account_signer_sign_time_mainnet_guard`).

---

## 5. Blocker Determination: DEPLOYER FUNDING BLOCKER

Per Section 4 of Phase 6.9.1:
> *Before deployment: derive the public deployer address from DEPLOYER_PRIVATE_KEY. Verify: address is valid, chain ID is 84532, wallet has enough Base Sepolia ETH for deployment gas... If insufficient ETH exists: STOP. Report: DEPLOYER FUNDING BLOCKER. Do not attempt deployment.*

Because `DEPLOYER_PRIVATE_KEY` is currently not configured in the host environment, the deployer wallet cannot be derived or funded.

**Verdict:** **DEPLOYER FUNDING BLOCKER**

### Unblocking Procedure for Operator:
To unblock on-chain deployment and broadcast on Base Sepolia:
1. Generate or assign a dedicated Base Sepolia throwaway testnet wallet:
   ```bash
   cast wallet new
   ```
2. Request testnet ETH and testnet Circle USDC:
   - Base Sepolia ETH Faucet: `https://www.coinbase.com/faucets/base-ethereum-sepolia-faucet` or `https://sepoliafaucet.com` (Requires minimum ~0.01 ETH for gas safety buffer).
   - Base Sepolia USDC Faucet: Circle developer faucet for Base Sepolia (`0x036CbD53842c5426634e7929541eC2318f3dCF7e`).
3. Export the credentials in the local shell session (do NOT write into Git-tracked files):
   ```bash
   export DEPLOYER_PRIVATE_KEY="0x..."
   export RELAYER_PRIVATE_KEY="0x..."
   export BASE_SEPOLIA_RPC_URL="https://sepolia.base.org"
   ```
4. Broadcast deployment:
   ```bash
   cd contracts
   forge script script/Deploy.s.sol:DeployScript \
     --rpc-url $BASE_SEPOLIA_RPC_URL \
     --broadcast \
     -vvvv
   ```
5. Update contract addresses in backend configuration:
   ```bash
   export ESCROW_CONTRACT_BASE_SEPOLIA="0x..."
   export REVENUE_DISTRIBUTOR_CONTRACT_BASE_SEPOLIA="0x..."
   export RESULT_NOTARY_CONTRACT_BASE_SEPOLIA="0x..."
   export REPUTATION_REGISTRY_CONTRACT_BASE_SEPOLIA="0x..."
   ```

---

## 6. Regression Verification Summary

All regression test suites were re-executed and verified:

| Suite | Component | Test Count | Result |
|---|---|---|---|
| Backend Pytest | API, Services, Adversarial, Reorg, LifeCycle | 483 passed | **PASS** |
| Services Pytest | Indexer, Agent SDK, Background Worker | 8 passed | **PASS** |
| Foundry Smart Contracts | `Escrow`, `RevenueDistributor`, `Notary`, `Reputation` | 115 passed | **PASS** |
| Foundry State Invariants | Financial Conservation, Balance Matching | 128,000 calls | **PASS (0 reverts)** |
| Production Readiness | Mainnet Guards, Signer Pre-flight, Config Drift | 24 passed | **PASS (0 drift)** |
| Web Application | TypeScript strict check (`apps/web`) | `tsc --noEmit` | **PASS (0 errors)** |

---

## 7. Sign-Off & Status

- **Phase 6.9.1 Infrastructure Audit:** COMPLETE
- **Deployment Script `Deploy.s.sol` Simulation:** VERIFIED ON-CHAIN (Chain 84532)
- **Official Circle USDC:** VERIFIED ON-CHAIN (6 decimals)
- **Base Mainnet Hard-Block:** INTACT AND ENFORCED
- **Release Gate Status:** **DEPLOYER FUNDING BLOCKER**
