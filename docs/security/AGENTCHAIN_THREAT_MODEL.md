# AgentChain Comprehensive Threat Model
## Phase 6.11 — Production Audit Readiness & Defense-in-Depth Specification

**Document Version**: 2.0.0  
**Classification**: PUBLIC AUDIT SPECIFICATION  
**Author**: Application Security, Blockchain Security, & Reliability Engineering  
**Status**: FROZEN RELEASE CANDIDATE  

---

### 1. System Overview & Trust Boundaries

AgentChain coordinates autonomous AI agents executing paid asynchronous tasks using on-chain escrow, deterministic selection, LangGraph DAG orchestration, sandboxed container workers, cryptographic result notarization, and automated 85/10/5 revenue distribution on Base L2.

```
       [ Client Browser / dApp ]
                  | (SIWE / HTTPS)
                  v
       [ FastAPI Backend / Auth ] <---- (JWT / RBAC)
                  |
     +------------+------------+
     |                         |
     v                         v
[ PostgreSQL ]            [ Redis Streams ]
(Authoritative State)     (Queue & Outbox)
     ^                         |
     |                         v
[ Indexer ]           [ Worker Pool / Sandbox ]
     ^                         |
     | (32-block depth)        v
[ Relayer / KMS ] <--- [ Orchestrator ]
     |
     v
[ Base L2 Contracts: Escrow, Distributor, Notary, Reputation ]
```

---

### 2. Threat Analysis by Actor & Attack Surface

The following table comprehensively analyzes the 17 threat actors, compromise vectors, and threat scenarios defined in Phase 6.11.

---

#### 2.1 Threat: Client
- **Asset**: Escrow USDC balance, task deliverables, agent compute capacity.
- **Attacker Capability**: Authenticated client account with funded wallet; can initiate tasks, fund escrows, trigger timeouts, open disputes.
- **Attack Path**:
  1. Client funds escrow, waits for worker to lock execution, and attempts instant refund before work finishes.
  2. Client disputes completed work dishonestly to recover funds.
  3. Client supplies adversarial input data designed to trigger worker timeout.
- **Existing Mitigation**:
  - `Escrow.sol` strictly enforces: once `LOCKED`, client refund is blocked until `block.timestamp >= executionDeadline`.
  - Beneficiary or Arbitrator alone can refund during active work.
  - Disputes freeze funds in `DISPUTED` state; only platform `ARBITRATOR_ROLE` can divide funds via `resolveDispute`.
- **Residual Risk**: Client griefing by setting short deadlines or locking funds in long disputes.
- **Detection**: Prometheus metric `agentchain_escrow_disputes_total`, audit log `DISPUTE_OPENED`.
- **Response**: Platform arbitrator reviews notarized execution hashes and audit logs; releases funds to developer if valid proof exists.

---

#### 2.2 Threat: Developer
- **Asset**: Agent Registry, marketplace task fees, distribution payouts.
- **Attacker Capability**: Registered agent author; publishes agent manifests, container definitions, and sets pricing models.
- **Attack Path**:
  1. Developer publishes malicious agent container that attempts container breakout or infinite loop.
  2. Developer claims 85% revenue without delivering valid outputs.
  3. Developer sets payout address to contract that reverts on token receipt to DoS settlement.
- **Existing Mitigation**:
  - Container execution is sandboxed with gVisor/Docker, read-only root, memory caps, unprivileged UID, dropped capabilities (`CAP_DROP=ALL`).
  - Output hashes must match canonical result commitment in `ResultNotary`.
  - Payout uses OpenZeppelin `SafeERC20`; if recipient reverts, transaction reverts fail-closed without corrupting distributor balances.
- **Residual Risk**: Developer publishing low-quality or hallucinated responses that pass schema checks.
- **Detection**: Result verification failures (`VERIFICATION_FAILED`), worker timeout metrics.
- **Response**: ReputationRegistry records `VERIFIED_FAILURE`; agent reputation score degrades, disqualifying from selection.

---

#### 2.3 Threat: Worker
- **Asset**: Execution queue jobs, worker lease tokens, intermediate results, host infrastructure.
- **Attacker Capability**: Compromised or rogue worker process executing arbitrary agent containers.
- **Attack Path**:
  1. Worker modifies execution result before returning output.
  2. Worker crashes mid-execution holding lease to stall task.
  3. Worker double-processes jobs or claims jobs without executing them.
- **Existing Mitigation**:
  - Worker leases enforce 60-second TTL with heartbeat renewal (`agent_executions.lease_expires_at`).
  - OutboxDispatcher and Queue reclaim stale leases automatically.
  - Output hashes are independently canonicalized via RFC-8785 JSON canonization and SHA-256 before settlement.
- **Residual Risk**: Malicious worker submitting synthetic result hashes matching invalid schema.
- **Detection**: Stale lease alerts `WorkerStaleLeaseDetected`, heartbeat dropouts.
- **Response**: Worker quarantined; lease re-assigned to healthy worker; execution attempt count incremented.

---

#### 2.4 Threat: Orchestrator
- **Asset**: LangGraph DAG state, task execution order, task-to-attempt bindings.
- **Attacker Capability**: Corrupted orchestrator node or state deserialization injection.
- **Attack Path**:
  1. Orchestrator introduces cycles in task execution DAG.
  2. Orchestrator executes child tasks out of dependency order.
  3. Orchestrator confuses logical task key with physical execution attempt key.
- **Existing Mitigation**:
  - Strict DAG validation: cycle detection (Kahn's algorithm), max graph depth (10), max tasks (20), max dependencies (5).
  - Explicit separation: `orchestration_tasks.task_key` (logical) != `orchestration_tasks.execution_id` (physical attempt).
  - Checkpoint integrity verified with SHA-256 state hashing and PostgreSQL row locks.
- **Residual Risk**: Orchestrator node crash during graph transition.
- **Detection**: Checkpoint state hash mismatches, graph validation errors (`422 Unprocessable`).
- **Response**: Orchestration recovery engine reloads last verified checkpoint and resumes DAG traversal.

---

#### 2.5 Threat: Relayer
- **Asset**: Blockchain transaction submission pipeline, relayer gas balance, nonces.
- **Attacker Capability**: Process controlling transaction submission and gas price estimation.
- **Attack Path**:
  1. Relayer sends unauthorized transaction payload to unapproved contract.
  2. Relayer corrupts nonce sequence, causing stalled transaction queue.
  3. Relayer broadcasts transaction to unauthorized chain (e.g. Mainnet).
- **Existing Mitigation**:
  - Relayer verifies `intent.chain_id == configured_chain_id`.
  - Target contracts strictly allowlisted: only Escrow, Distributor, ResultNotary, ReputationRegistry.
  - Operations allowlisted: only approved 12 methods in `ALLOWED_OPERATIONS`.
  - Nonces serialized and managed with PostgreSQL `FOR UPDATE` row locks.
  - Base Mainnet hard blocked at relayer startup: `MainnetSubmissionBlockedError`.
- **Residual Risk**: RPC provider rejections or gas spikes delaying transaction confirmation.
- **Detection**: Transaction stall metric `blockchain_relayer_stalled_transactions`, replacement alerts.
- **Response**: Automated gas bumping (120% replacement transaction) up to 3 attempts, then quarantine to DLQ.

---

#### 2.6 Threat: Admin
- **Asset**: Contract role administration, pause/unpause levers, arbitrator powers.
- **Attacker Capability**: Compromised administrative key or insider rogue administrator.
- **Attack Path**:
  1. Admin pauses contract indefinitely to freeze funds.
  2. Admin resolves disputes unilaterally to drain funds.
  3. Admin reassigns roles to attacker addresses.
- **Existing Mitigation**:
  - Admin cannot release locked escrow directly (`EscrowTest.test_AccessControl_AdminCannotRelease`).
  - Admin cannot resolve disputes directly without `ARBITRATOR_ROLE`.
  - Production requires multi-signature custody (Gnosis Safe) with timelocks for role modifications.
  - Emergency pausing emits on-chain events observable by indexer.
- **Residual Risk**: Malicious multi-sig threshold compromise.
- **Detection**: Real-time monitoring on `RoleGranted`, `RoleRevoked`, `Paused`, `Unpaused` events.
- **Response**: Contract timelock veto; legal and governance community circuit-breaker.

---

#### 2.7 Threat: Staker
- **Asset**: Staking reward pool, 10% distribution cut.
- **Attacker Capability**: User staking in platform pools to capture fee share.
- **Attack Path**:
  1. Staker front-runs settlement to capture fees without providing liquidity.
  2. Staker attempts to manipulate distributor recipient address.
- **Existing Mitigation**:
  - Distributor recipient is immutable: `stakerRecipientAddress` is set at constructor deployment and cannot be changed by governance.
  - Staker pool rewards distributed according to time-weighted checkpoints, not block-atomic balances.
- **Residual Risk**: Staker pool contract vulnerabilities (external to AgentChain core).
- **Detection**: Divergence between distributor output and pool deposit logs.
- **Response**: Distributor sends directly to pool contract address; no dynamic router calls.

---

#### 2.8 Threat: DAO
- **Asset**: 5% protocol treasury cut, platform governance parameters.
- **Attacker Capability**: Governance proposal authors; token holders.
- **Attack Path**:
  1. Governance passes malicious proposal redirecting funds or changing fee splits.
  2. DAO treasury address compromised.
- **Existing Mitigation**:
  - Revenue split (85% developer, 10% staker, 5% DAO) is hardcoded in `RevenueDistributor.sol` integer arithmetic; not adjustable by storage variables.
  - `daoRecipientAddress` is immutable.
  - DAO receives exact remainder ensuring zero dust leak.
- **Residual Risk**: Compromise of DAO treasury cold storage.
- **Detection**: On-chain transfer monitoring of DAO treasury address.
- **Response**: DAO treasury cold key rotation via governance fork or proxy upgrade (if enabled).

---

#### 2.9 Threat: Attacker (External Unauthenticated)
- **Asset**: Public API endpoints, database, RPC nodes, smart contracts.
- **Attacker Capability**: Public internet traffic, automated fuzzers, front-running bots, MEV searchers.
- **Attack Path**:
  1. Public API DDoS against `/api/v1/agents` or `/api/v1/orchestrations`.
  2. Front-running escrow creations or claiming escrow IDs.
  3. Replay attacks on historical signatures or transaction calldata.
- **Existing Mitigation**:
  - Redis token-bucket rate limiting on all public endpoints (30 nonces/min, 15 verifications/min).
  - Escrow ID includes client address, referenceId, and unique salt: `keccak256(chain_id, this, client, ref, salt)`. External parties cannot front-run or hijack escrow IDs.
  - Nonces in SIWE and blockchain transactions expire and are strictly single-use.
- **Residual Risk**: L2 sequencer front-running or transaction re-ordering.
- **Detection**: Rate limit trigger metrics `agentchain_rate_limit_exceeded_total`.
- **Response**: IP block via reverse proxy (Cloudflare); API key revocation.

---

#### 2.10 Threat: Malicious Agent
- **Asset**: Worker environment, host system, downstream workflow tasks.
- **Attacker Capability**: Code executed inside agent container or generated model payloads.
- **Attack Path**:
  1. Agent attempts to read host environment variables or AWS metadata credentials (`169.254.169.254`).
  2. Agent executes fork bomb or consumes 100% host disk space.
  3. Agent returns corrupted output format to crash downstream DAG nodes.
- **Existing Mitigation**:
  - Docker/gVisor runner blocks access to `169.254.169.254` (cloud metadata service).
  - Container resources capped: 2GB RAM, 1 CPU core, PID limit 100.
  - Temporary workspace mounted with strict storage quota (500MB) and destroyed on teardown.
  - Output schema validation strictly enforces expected Pydantic models before persisting.
- **Residual Risk**: Zero-day Linux kernel vulnerability escaping container sandbox.
- **Detection**: Host anomalous syscall detection; sandbox runner process termination alerts.
- **Response**: Worker container immediately SIGKILLed; worker node recycled.

---

#### 2.11 Threat: Malicious Token
- **Asset**: Escrow accounting balance, RevenueDistributor payout mechanism.
- **Attacker Capability**: Deployer attempts to initialize Escrow with fee-on-transfer, rebasing, or malicious ERC-20 token.
- **Attack Path**:
  1. Attacker deploys Escrow with token that deducts fees on transfer, corrupting `totalEscrowBalance`.
  2. Attacker configures re-entrant token hook (ERC-777) to re-enter `releaseEscrow`.
- **Existing Mitigation**:
  - Constructor validates official Circle USDC address on Base Sepolia (`0x036CbD...`) and Base Mainnet (`0x83358...`).
  - Strict token decimals check: must equal exactly 6 (`revert InvalidTokenDecimals`).
  - OpenZeppelin `SafeERC20` used for all operations.
  - `ReentrancyGuard` on all state-changing external functions.
- **Residual Risk**: Circle USDC blacklisting a client or beneficiary address during active escrow.
- **Detection**: Failed transfer reverts with `SafeERC20FailedOperation`.
- **Response**: Platform arbitrator resolves escrow to alternate safe address or unpauses after resolution.

---

#### 2.12 Threat: Compromised RPC Provider
- **Asset**: Blockchain state observation, gas estimation, raw transaction broadcast.
- **Attacker Capability**: Rogue or hijacked RPC endpoint returning fabricated blocks, receipts, or logs.
- **Attack Path**:
  1. Compromised RPC reports false `eth_blockNumber` or fabricates fake `EscrowFunded` event to trigger worker execution without funding.
  2. RPC drops submitted transaction hashes, causing relayer to over-spend gas.
- **Existing Mitigation**:
  - Indexer enforces 32-block confirmation window before treating events as authoritative.
  - Marketplace coordinator strictly checks `EscrowChainState.status == 'FUNDED'` AND `confirmations >= 32` before releasing task to workers.
  - Multi-RPC quorum validation in production architecture.
- **Residual Risk**: Deep persistent eclipse attack against relayer node.
- **Detection**: Block hash continuity validation (`parentHash` matches previous block); RPC latency anomalies.
- **Response**: Circuit-breaker triggers; relayer halts transaction submission; fallback to secondary RPC provider (Alchemy / Infura).

---

#### 2.13 Threat: Blockchain Chain Reorganization
- **Asset**: Authoritative execution state, settlement payouts, reputation proof validity.
- **Attacker Capability**: L2 sequencer reorganization or L1 reorg on Base.
- **Attack Path**:
  1. Reorg orphans a block containing `EscrowFunded`; worker completes work but escrow never existed.
  2. Reorg unrolls `ResultNotarized` event while `ReputationRegistry` records verified success.
- **Existing Mitigation**:
  - 32-block confirmation policy across all authoritative services (`MAX_REORG_DEPTH = 32`).
  - Reorg engine (`ReorgHandler`) monitors block parent hashes and unwinds orphaned events.
  - `NotarizationProjector` and `ReputationProjector` flag reorged events with `is_canonical = False`.
  - Database reconciliation service halts settlements if an unresolved reorg exists on the active chain.
- **Residual Risk**: Deep reorg exceeding 32 blocks (extreme catastrophe on L2).
- **Detection**: `ReorgHandler` detects parent hash mismatch; emits `agentchain_chain_reorg_detected_total`.
- **Response**: Emergency freeze; automatic rollback of unconfirmed state; manual operator verification per `DEEP_REORG_RUNBOOK.md`.

---

#### 2.14 Threat: Database Compromise
- **Asset**: User identity, off-chain task metadata, outbox table, API nonces.
- **Attacker Capability**: Attacker with SQL injection or compromised PostgreSQL credentials.
- **Attack Path**:
  1. Attacker updates `agent_executions.status` to `SUCCEEDED` without execution.
  2. Attacker modifies outbox payload to submit fraudulent transaction intent.
- **Existing Mitigation**:
  - On-chain contracts do NOT trust PostgreSQL database state; contracts verify caller role, on-chain state, and cryptographic proofs independently.
  - Relayer verifies cryptographic hash against `ResultNotary` and checks allowlisted function arguments.
  - SQLAlchemy ORM with parametrized queries prevents SQL injection.
  - Encrypted database connections with TLS 1.3.
- **Residual Risk**: Confidentiality loss of task inputs and user metadata.
- **Detection**: PostgreSQL audit logging, connection anomaly alerts.
- **Response**: Database failover; revoke compromised credentials; rollback from point-in-time recovery (PITR) backup.

---

#### 2.15 Threat: Redis / Queue Compromise
- **Asset**: Task queue stream, outbox dispatcher, rate-limiter counters.
- **Attacker Capability**: Attacker with access to Redis port 6379 or compromised Redis token.
- **Attack Path**:
  1. Attacker deletes task stream or drops messages.
  2. Attacker injects duplicate job payloads to trigger double execution.
  3. Attacker flushes rate-limiter keys to bypass throttling.
- **Existing Mitigation**:
  - Transactional Outbox pattern: PostgreSQL is authoritative. If Redis drops messages, `OutboxDispatcher` re-polls unpublished entries and republishes.
  - Workers enforce database idempotency keys: duplicate job claims detect `AgentExecution.status != QUEUED` and reject duplicate processing.
  - Redis runs in isolated private subnet with AUTH required.
- **Residual Risk**: Temporary queue throughput degradation during Redis flush.
- **Detection**: Queue depth anomalies, outbox lag metric `agentchain_outbox_lag_seconds`.
- **Response**: Restart Redis; `OutboxDispatcher` automatically rehydrates streams from PostgreSQL.

---

#### 2.16 Threat: AWS KMS Compromise
- **Asset**: Relayer private key, transaction signing authority.
- **Attacker Capability**: Attacker with compromised AWS IAM credentials or KMS key access.
- **Attack Path**:
  1. Attacker uses `kms:Sign` to sign arbitrary transactions draining relayer funds.
  2. Attacker schedules KMS key deletion (`kms:ScheduleKeyDeletion`) to disable platform.
- **Existing Mitigation**:
  - KMS key policy strictly limits `kms:Sign` to dedicated Relayer IAM Role.
  - Key deletion disabled; multi-party approval required in AWS Organization Service Control Policies (SCPs).
  - Relayer wallet balance kept at minimal working capital (<0.05 ETH).
  - Smart contracts enforce role allowlisting and recipient controls (e.g. Escrow payouts only go to designated beneficiaries or clients).
- **Residual Risk**: Attacker signing valid transactions until relayer gas is exhausted.
- **Detection**: AWS CloudTrail alert on unauthorized `kms:Sign` calls; CloudWatch anomaly detector.
- **Response**: Execute `docs/security/aws-kms-emergency-response.md`: immediately revoke IAM session, pause contracts via multi-sig `PAUSER_ROLE`.

---

#### 2.17 Threat: Credential / Secret Compromise
- **Asset**: API keys, database passwords, session tokens.
- **Attacker Capability**: Exposure of `.env`, logs, or developer workstation credentials.
- **Attack Path**:
  1. Attacker obtains developer session token to execute agents on behalf of user.
  2. Attacker obtains database credentials to query customer deliverables.
- **Existing Mitigation**:
  - Strict CI secret scanning (`scripts/verify_production_safety_gates.py`).
  - Session tokens bound to wallet address with 7-day expiration and SIWE domain matching.
  - Zero raw private keys permitted in production (`ProductionSigner` fail-closed).
  - All production secrets managed via AWS Secrets Manager or HashiCorp Vault.
- **Residual Risk**: Phishing of user wallet signatures.
- **Detection**: SIWE nonce verification failures, anomalous IP login patterns.
- **Response**: Revoke session token in Redis; user disconnects wallet; rotate compromised database/API credentials.

---

### 3. Threat Model Review & Sign-Off

This Threat Model satisfies the requirements of Phase 6.11 and accurately reflects the current codebase implementation and defense-in-depth architecture.
