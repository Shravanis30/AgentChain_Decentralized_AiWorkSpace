# AgentChain External Security Audit Package
## Release Candidate Audit Dossier — Phase 6.11

**Target System**: AgentChain Protocol & Execution Runtime  
**Document Classification**: EXTERNAL AUDIT SPECIFICATION & READINESS DOSSIER  
**Auditor Target**: Independent Third-Party Smart Contract & Application Security Auditors  
**Audit Readiness Status**: **AUDIT READY WITH FINDINGS**  
**Release Freeze Status**: **FEATURE DEVELOPMENT FROZEN**  
**Date of Dossier**: October 4, 2026  

---

### Table of Contents
1. [Architecture Overview](#1-architecture-overview)
2. [Trust Boundaries & Security Perimeter](#2-trust-boundaries--security-perimeter)
3. [Smart Contract Inventory](#3-smart-contract-inventory)
4. [Backend Protocol Inventory](#4-backend-protocol-inventory)
5. [Signer Architecture](#5-signer-architecture)
6. [Threat Model Summary](#6-threat-model-summary)
7. [Security Findings Summary](#7-security-findings-summary)
8. [Master Protocol Invariants](#8-master-protocol-invariants)
9. [Test Evidence & Verification Suites](#9-test-evidence--verification-suites)
10. [Blockchain Reorganization Model](#10-blockchain-reorganization-model)
11. [Settlement & Economic Distribution Model (85/10/5)](#11-settlement--economic-distribution-model-85105)
12. [Cryptographic Result Notarization & Proof Model](#12-cryptographic-result-notarization--proof-model)
13. [Verified Reputation Model](#13-verified-reputation-model)
14. [Deployment & Configuration Governance Model](#14-deployment--configuration-governance-model)
15. [AWS KMS Hardware Security Module Architecture](#15-aws-kms-hardware-security-module-architecture)
16. [Incident Response Procedures](#16-incident-response-procedures)
17. [Disaster Recovery & Business Continuity](#17-disaster-recovery--business-continuity)
18. [Known Limitations](#18-known-limitations)
19. [Remaining Blockers for Mainnet Release](#19-remaining-blockers-for-mainnet-release)

---

### 1. Architecture Overview

AgentChain is a decentralized network for coordinating, executing, and settling autonomous AI agent workloads. It integrates off-chain containerized execution with on-chain financial guarantees and cryptographic proof verification:

```
[ Client / dApp ] --(SIWE / EIP-4361)--> [ FastAPI Gateway ]
                                                |
                 +------------------------------+-------------------------------+
                 |                                                              |
                 v                                                              v
       [ PostgreSQL 16 ]                                              [ Redis Streams 7.2 ]
(Authoritative App State / Checkpoints)                              (Task Queue / Dispatch)
                 |                                                              |
                 v                                                              v
       [ LangGraph Orchestrator ]                                    [ Worker Pool (gVisor) ]
   (DAG State / Kahn Validation)                                     (Sandboxed Tool Exec)
                 |                                                              |
                 +------------------------------+-------------------------------+
                                                |
                                                v
                                  [ BlockchainRelayer / Outbox ]
                                                |
                                                v (Hardware Custody)
                                         [ AwsKmsSigner ]
                                                |
                                                v
                              +-----------------+-----------------+
                              |        Base L2 (EVM) Contracts    |
                              +-----------------+-----------------+
                              | 1. Escrow.sol (USDC)              |
                              | 2. RevenueDistributor.sol         |
                              | 3. ResultNotary.sol (SHA-256)     |
                              | 4. ReputationRegistry.sol         |
                              | 5. AgentRegistry.sol              |
                              +-----------------------------------+
```

---

### 2. Trust Boundaries & Security Perimeter

1. **Client / Gateway Boundary**: EIP-4361 Sign-In with Ethereum (SIWE) authenticates client identity. Session tokens (JWT) are HTTP-only and bound to domain, chain ID, and client address.
2. **Gateway / Database Boundary**: Parameterized SQLAlchemy ORM with asyncpg over TLS 1.3. No raw SQL concatenation.
3. **Worker / Host Boundary**: Container isolation (gVisor/Docker), read-only root filesystems, dropped capabilities (`CAP_DROP=ALL`), unprivileged user (UID 1000), 2GB RAM cap, 1 CPU core cap, no host mounts.
4. **Relayer / Blockchain Boundary**: Fail-closed relayer enforcing:
   - Target contract allowlist (only canonical Escrow, Distributor, Notary, Reputation).
   - Method selector allowlist (`ALLOWED_OPERATIONS`).
   - Hardware signing via AWS KMS (`ECC_SECG_P256K1`).
   - Base Mainnet hard block.
5. **Smart Contract Trust Boundary**: Contracts assume zero trust from the backend. Caller permissions enforced by OpenZeppelin `AccessControl`. Reentrancy prevented by `ReentrancyGuard`. Financial tokens handled by `SafeERC20`.

---

### 3. Smart Contract Inventory

All contracts are written in Solidity `0.8.24` and located in [contracts/src/](file:///Users/shravani/Desktop/AgentChain_AIWorkSpace/contracts/src/).

| Contract | Lines of Code | Primary Roles | Critical External Calls | Immutability |
| :--- | :--- | :--- | :--- | :--- |
| **AgentRegistry.sol** | 88 | `owner()` (Ownable2Step) | None | Non-upgradeable |
| **Escrow.sol** | 458 | `DEFAULT_ADMIN_ROLE`, `ARBITRATOR_ROLE`, `PAUSER_ROLE` | `IERC20.safeTransfer`, `safeTransferFrom`, `IRevenueDistributor.distribute` | Non-upgradeable |
| **RevenueDistributor.sol** | 229 | `DEFAULT_ADMIN_ROLE`, `DISTRIBUTOR_ADMIN_ROLE` | `IERC20.safeTransfer` | Non-upgradeable |
| **ResultNotary.sol** | 106 | `DEFAULT_ADMIN_ROLE`, `NOTARIZER_ROLE` | None | Non-upgradeable |
| **ReputationRegistry.sol** | 138 | `DEFAULT_ADMIN_ROLE`, `REPUTATION_ORACLE_ROLE` | `IResultNotary.verifyProof` | Non-upgradeable |

#### Canonical Deployed Testnet Addresses (Base Sepolia — Chain ID 84532)
- **AgentRegistry**: `0x96C3945e264A880EbaB5e95fDF786Fc18A4389B5`
- **RevenueDistributor**: `0x2F2d5Cc40e2C4C32cC86BC5127c2833a7784ee1c`
- **Escrow**: `0x3e5C09D2123cA106b2E24C4f76bFad4a022640D1`
- **ResultNotary**: `0xed58B4E37f81dFbD08c3d2DeF613286bC8Cf8056`
- **ReputationRegistry**: `0x423856529583F536d5dFaE80f7Bc142075c6Fc71`
- **Official Circle USDC**: `0x036CbD53842c5426634e7929541eC2318f3dCF7e`

---

### 4. Backend Protocol Inventory

Located in [backend/app/](file:///Users/shravani/Desktop/AgentChain_AIWorkSpace/backend/app/):
- **Core Config** ([config.py](file:///Users/shravani/Desktop/AgentChain_AIWorkSpace/backend/app/core/config.py)): Strict Pydantic models for environment and secrets.
- **Relayer Engine** ([relayer.py](file:///Users/shravani/Desktop/AgentChain_AIWorkSpace/backend/app/services/blockchain/relayer.py)): Durable nonce tracking, gas estimation, replacement transactions, and fail-closed submit guards.
- **Indexer & Projectors** ([event_indexer.py](file:///Users/shravani/Desktop/AgentChain_AIWorkSpace/backend/app/services/blockchain/event_indexer.py)): Polling event indexer tracking blocks and event logs via `indexed_blocks` table with 32-block depth.
- **Reorg Engine** ([reorg_handler.py](file:///Users/shravani/Desktop/AgentChain_AIWorkSpace/backend/app/services/blockchain/reorg_handler.py)): Deep block ancestry detection, orphan unrolling, and project re-evaluation.
- **Reconciliation Engine** ([reconciliation.py](file:///Users/shravani/Desktop/AgentChain_AIWorkSpace/backend/app/services/blockchain/reconciliation.py)): Cross-layer assertion between database states and on-chain logs.
- **Transactional Outbox** ([tx_outbox.py](file:///Users/shravani/Desktop/AgentChain_AIWorkSpace/backend/app/services/blockchain/tx_outbox.py)): PostgreSQL-persisted queue entries with at-least-once delivery guarantees.
- **Orchestration Service** ([engine.py](file:///Users/shravani/Desktop/AgentChain_AIWorkSpace/backend/app/services/orchestration/engine.py)): LangGraph stateful DAG engine with Kahn cycle detection, step-level checkpointing, and durable wake-ups.
- **Worker Daemon** (`services/worker`): Pull-based Redis Streams worker with 60-second lease renewal heartbeats and sandboxed execution.

---

### 5. Signer Architecture

AgentChain implements a strict three-tier signer abstraction:
1. **LocalAccountSigner**: In-memory private key signer. **Accepted ONLY on Anvil (Chain ID 31337) and Base Sepolia (84532)**. Strictly rejected on Base Mainnet.
2. **AwsKmsSigner**: Hardware security module custody. Produces ECDSA SECP256k1 signatures via AWS KMS API (`Sign` operation with `MessageType='DIGEST'`). Performs DER decoding, low-S normalization (BIP-62 / EIP-2), recovery ID calculation (`v`), and pre-broadcast address assertion.
3. **ProductionSigner Factory**: Enforces fail-closed selection:
   - If `APP_ENV=production`: requires `SIGNER_PROVIDER=aws_kms`. Any attempt to supply raw private keys or local accounts raises `ProductionSignerSecurityError`.
   - On Base Mainnet (8453): all signing attempts unconditionally raise `MainnetSubmissionBlockedError`.

---

### 6. Threat Model Summary
*Source of Truth: [docs/security/AGENTCHAIN_THREAT_MODEL.md](file:///Users/shravani/Desktop/AgentChain_AIWorkSpace/docs/security/AGENTCHAIN_THREAT_MODEL.md)*

The threat model evaluates 17 threat actors across 4 security layers. Key mitigations include:
- Upfront USDC escrow lock prevents non-paying client attacks.
- Execution lock deadline prevents client front-running refunds during active worker execution.
- Sandboxed gVisor container runners prevent malicious agent host breakouts.
- Immutable contract bindings prevent governance rug pulls on fee splits.
- 32-block confirmation rules prevent sequencer reorg attacks.

---

### 7. Security Findings Summary
*Source of Truth: [docs/security/PHASE_6_11_SECURITY_FINDINGS_REGISTER.md](file:///Users/shravani/Desktop/AgentChain_AIWorkSpace/docs/security/PHASE_6_11_SECURITY_FINDINGS_REGISTER.md)*

Ten findings were cataloged and addressed in Phase 6.11 and Phase 6.11.1:
- **SEC-6.11-001 (HIGH)**: Live AWS KMS verification not executed -> **OPEN / KNOWN BLOCKER** (fail-closed, production blocked).
- **SEC-6.11-002 (HIGH)**: Local Anvil ephemeral state -> **MITIGATED** (automated deterministic bootstrap & restart test suite).
- **SEC-6.11-003 (MEDIUM)**: Redis Streams test contention -> **MITIGATED** (terminated orphan worker, raised socket timeouts to 5s, pre/post fixture cleanup; 3x repeated contention runs verified).
- **SEC-6.11-004 (MEDIUM)**: Testnet multi-role operator overlap -> **ACCEPTED TESTNET RISK / PROD BLOCKED** (scoped strictly to Base Sepolia testnet; production role overlap blocked by Gate 7).
- **SEC-6.11-005 (LOW)**: Slither enum strict equality -> **ACCEPTED RISK** (formal false positive; existence sentinel protected by `nonReentrant`).
- **SEC-6.11-006 (LOW)**: Timestamp comparisons -> **ACCEPTED RISK** (intended coarse-grained L2 time-lock design).
- **SEC-6.11-007 (LOW)**: Starlette 422 deprecation warning -> **MITIGATED** (replaced `HTTP_422_UNPROCESSABLE_ENTITY` with `HTTP_422_UNPROCESSABLE_CONTENT`).
- **SEC-6.11-008 (INFORMATIONAL)**: Local `.env` testnet key -> **MITIGATED** (multi-layer `.gitignore` and `.dockerignore` verification; Gate 1 & 2 PASS).
- **SEC-6.11-009 (INFORMATIONAL)**: Slither pragma discrepancy -> **ACCEPTED RISK** (pinned Solidity `0.8.24` satisfies `^0.8.20`).
- **SEC-6.11-010 (INFORMATIONAL)**: LangGraph serializer warning -> **JUSTIFIED / ACCEPTED RISK** (vendor API boundary documented, warning filtered, canonical RFC 8785 JSON hashing preserved).

---

### 8. Master Protocol Invariants

#### 8.1 Escrow Invariants
1. **Financial Conservation**: Total contract USDC balance equals cumulative active escrows:
   $$\text{IERC20}(\text{USDC}).\text{balanceOf}(\text{Escrow}) = \text{totalEscrowBalance}$$
2. **Bounded Release**: For any escrow $e$, released amount equals escrow amount ($A_e$).
3. **Mutual Exclusivity**: An escrow can transition to `RELEASED` or `REFUNDED`, but never both.
4. **Deadline Protection**: Client cannot refund a `LOCKED` escrow before `block.timestamp >= executionDeadline`.

#### 8.2 Revenue Distribution Invariants (85/10/5)
1. **Zero Dust Conservation**: For any gross payout $G$:
   $$\text{dev} + \text{stk} + \text{dao} = G$$
   $$\text{dev} = \lfloor G \times 85 / 100 \rfloor, \quad \text{stk} = \lfloor G \times 10 / 100 \rfloor, \quad \text{dao} = G - \text{dev} - \text{stk}$$
2. **Transient Custody**: `RevenueDistributor` holds zero USDC balance between distributions.
3. **Immutable Recipients**: `stakerRecipientAddress` and `daoRecipientAddress` are immutable.

#### 8.3 Cryptographic Result Notarization Invariants
1. **Single Notarization**: Each `executionId` can be notarized at most once (strict replay prevention).
2. **Deterministic Digest**: Proof hash equals $\text{SHA-256}(\text{RFC-8785}(\text{result\_json}))$.

#### 8.4 Verified Reputation Invariants
1. **Cryptographic Proof Binding**: `VERIFIED_SUCCESS` cannot be registered unless `ResultNotary.verifyProof(executionId, resultHash) == true`.
2. **Single Outcome**: Each `executionId` can be registered at most once in `ReputationRegistry`.

#### 8.5 Blockchain & Confirmation Invariants
1. **32-Block Authoritative Window**: State transitions requiring finality must achieve $\ge 32$ block confirmations.
2. **Reorg Neutrality**: Reorganized blocks unroll associated canonical events.

---

### 9. Test Evidence & Verification Suites

#### 9.1 Smart Contract Tests (Foundry)
- **Total Test Suites**: 7
- **Total Tests Passed**: 122 / 122 (100%)
- **Fuzzing & Invariants**:
  - Invariant Test `EscrowInvariantTest`: 256 runs, 128,000 calls across all state transitions with **0 reverts and 0 invariant violations**.
  - Fuzz runs on `ResultNotary` and `ReputationRegistry`: 10,000 runs per test.

#### 9.2 Backend Automated Test Suites (pytest)
- **Total Tests Run**: 587
- **Total Tests Passed**: 587 / 587 (100%)
- **Core Security Suites**:
  - `test_aws_kms_signer.py`: 43 / 43 passed
  - `test_kms_operational_controls.py`: 22 / 22 passed
  - `test_phase_6_10_production_signer_mainnet_safety.py`: 21 / 21 passed
  - `test_production_readiness.py`: 24 / 24 passed
  - `test_anvil_bootstrap.py`: 11 / 11 passed
  - High-contention concurrency suite (`test_queue_recovery.py`, `test_orchestration_engine.py`, `test_cancellation.py`): 13 / 13 passed (verified across 3 consecutive runs)

#### 9.3 Production Safety Gate Verification
- Script: `python3 scripts/verify_production_safety_gates.py`
- Result: **8/8 checks passed (100%)**
  - Tracked Secret Scanning: PASS
  - Git/Docker Hardening: PASS
  - Contract Mainnet Hard Block: PASS
  - Relayer Startup Guard: PASS
  - Signer Abstraction Guard: PASS
  - Fail-Closed Provider Boundaries: PASS
  - Role Separation Guard: PASS
  - Production Config Invariants: PASS

#### 9.4 Configuration Drift Scanner
- Script: `python3 scripts/check_config_drift.py`
- Result: **Zero configuration drift detected across all backend, smart contract, and documentation sources**.

#### 9.5 Base Sepolia Live Read-Only Verification
- Script: `python3 scripts/verify_base_sepolia_read_only.py`
- Result: **15 on-chain state reads and 6 bytecode verifications passed**. Zero state-changing transactions broadcast.

---

### 10. Blockchain Reorganization Model

AgentChain models chain reorganizations up to a 32-block depth.
- The `BlockTracker` maintains an in-memory and database sliding window of the last 32 canonical block headers.
- If a new block header presents a `parentHash` that does not match the previous block in the window, `ReorgHandler` traverses backwards to locate the common ancestor.
- All events indexed after the common ancestor are updated: `is_canonical = false`, `status = 'ORPHANED'`.
- Projectors roll back projections to the common ancestor state.
- Authoritative settlements halt until 32 new consecutive canonical blocks confirm the chain.

---

### 11. Settlement & Economic Distribution Model (85/10/5)

Settlement release executes atomically:
1. `Escrow.releaseEscrow(escrowId)` verifies authorization (Client or Arbitrator).
2. Escrow updates state to `RELEASED`, deducts `totalEscrowBalance`, and transfers USDC to `RevenueDistributor`.
3. Escrow calls `RevenueDistributor.distribute(escrowId, referenceId, grossAmount, developer)`.
4. Distributor calculates integer split (85% developer, 10% staker, 5% DAO remainder).
5. Distributor executes 3 safe transfers atomically and emits `DistributionExecuted`.

---

### 12. Cryptographic Result Notarization & Proof Model

1. Agent produces result JSON payload.
2. Result is normalized using RFC-8785 JSON Canonicalization Scheme (JCS).
3. SHA-256 digest is computed over canonical bytes.
4. Transaction intent is queued to outbox for `ResultNotary.notarizeResult(executionId, resultHash, artifactCommitment)`.
5. Relayer submits transaction; `ResultNotarized` event is emitted.
6. Public verification API `/api/v1/notarizations/{execution_id}/verify` checks on-chain proof against client deliverable.

---

### 13. Verified Reputation Model

1. After notarization confirms, reputation event intent is queued.
2. `ReputationRegistry.registerReputationEvent(...)` is called with outcome (`VERIFIED_SUCCESS`, `VERIFIED_FAILURE`, `VERIFIED_TIMEOUT`, `VERIFIED_CANCELLATION`).
3. For `VERIFIED_SUCCESS`, contract calls `resultNotary.verifyProof(executionId, resultHash)` on-chain. If proof does not match, transaction reverts with `ResultNotNotarized`.
4. Indexer ingests `ReputationEventRegistered`; updates rolling Elo-like Bayesian reputation scores.

---

### 14. Deployment & Configuration Governance Model

- **Deployment Script**: [contracts/script/Deploy.s.sol](file:///Users/shravani/Desktop/AgentChain_AIWorkSpace/contracts/script/Deploy.s.sol)
- Enforces explicit deployer private keys and role addresses.
- Strictly blocks Base Mainnet (8453) via `MainnetDeploymentBlocked()`.
- Validates official Circle USDC addresses for target networks.

---

### 15. AWS KMS Hardware Security Module Architecture

- **Key Specification**: SECG P-256k1 elliptic curve (`ECC_SECG_P256K1`), `SIGN_VERIFY` key usage.
- **Hardware Level**: FIPS 140-2 Level 3 HSM.
- **Signature Processing**: Raw DER ASN.1 signature output is decoded into `(r, s)`. If $s > \text{SECP256K1\_N} / 2$, $s$ is inverted ($s = N - s$) to enforce BIP-62 low-S malleability protection.
- Recovery ID ($v \in \{27, 28\}$) is calculated by testing public key recovery against the message hash and matching the derived Ethereum address.

---

### 16. Incident Response Procedures
*Source of Truth: [docs/security/AGENTCHAIN_INCIDENT_RESPONSE.md](file:///Users/shravani/Desktop/AgentChain_AIWorkSpace/docs/security/AGENTCHAIN_INCIDENT_RESPONSE.md)*

Contains structured emergency playbooks for all operational failure scenarios, including KMS disablement, emergency pause, mempool cancellation, and deep reorg recovery.

---

### 17. Disaster Recovery & Business Continuity
*Source of Truth: [docs/operations/AGENTCHAIN_DISASTER_RECOVERY.md](file:///Users/shravani/Desktop/AgentChain_AIWorkSpace/docs/operations/AGENTCHAIN_DISASTER_RECOVERY.md)*

Details crash recovery, transactional outbox rehydration, and local Anvil contract reseeding.

---

### 18. Known Limitations

1. **Non-Proxy Smart Contracts**: Smart contracts are immutable and cannot be upgraded in-place. Contract bug fixes require redeployment and address pointer updates.
2. **Synchronous Settlement Gas**: Settlements execute 3 ERC-20 transfers inside `RevenueDistributor.distribute()`, consuming ~120k gas on Base L2.
3. **Sequencer Dependency**: System relies on Base L2 sequencer uptime; prolonged L2 halts require waiting for L1 settlement windows.

---

### 19. Remaining Blockers for Mainnet Release

The following gates MUST remain explicitly active and unresolved prior to Mainnet release:

```text
[BLOCKER 1] LIVE AWS KMS PROVIDER VERIFICATION:
NOT EXECUTED (Pending operator activation with live AWS credentials)

[BLOCKER 2] PRODUCTION KMS KEY CUSTODY:
PENDING OPERATOR ACTIVATION

[BLOCKER 3] BASE MAINNET (CHAIN ID 8453):
STRICTLY BLOCKED ACROSS CONTRACTS, RELAYER, CONFIG, AND SIGNERS
```

---

### Audit Sign-Off Recommendation

The AgentChain protocol implementation is classified as:

```text
RELEASE CLASSIFICATION: AUDIT READY WITH FINDINGS
FEATURE DEVELOPMENT:    FROZEN
```

The codebase is frozen and structured for an independent third-party smart contract and application security audit.
