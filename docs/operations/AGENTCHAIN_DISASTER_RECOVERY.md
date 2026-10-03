# AgentChain Disaster Recovery & Business Continuity Plan (DRP)
## Phase 6.11 — Production Release Freeze & Operational Continuity Specification

**Document ID**: DRP-OPS-6.11-001  
**Classification**: INFRASTRUCTURE OPERATIONS MANUAL  
**Status**: FROZEN RELEASE CANDIDATE  
**Date**: October 4, 2026  

---

### 1. Operational Continuity Principles & Target Objectives

The AgentChain disaster recovery architecture is anchored on the principle that **the blockchain is the immutable ground truth for all financial and cryptographic commitments**, while **PostgreSQL is the authoritative store for application state and orchestration**.

#### Target Continuity Metrics
> [!IMPORTANT]
> The following figures represent architectural design targets based on fail-closed outbox patterns and blockchain immutability. They must not be claimed as certified SLAs until formal live load and failover chaos testing is executed in production infrastructure.

| Service Tier | Target RTO (Recovery Time Objective) | Target RPO (Recovery Point Objective) | Authoritative Source |
| :--- | :--- | :--- | :--- |
| **Blockchain Settlements & Escrow** | < 1 hour | **0 seconds (Zero Loss)** | Base L2 Smart Contracts |
| **Result Notarizations & Reputation** | < 1 hour | **0 seconds (Zero Loss)** | ResultNotary / ReputationRegistry |
| **PostgreSQL Database** | < 30 minutes | < 5 minutes (WAL replay) | RDS Multi-AZ / PITR Backups |
| **Redis Streams / Queue** | < 5 minutes | **0 seconds (Rehydrated from DB)** | Transactional Outbox Table |
| **Worker / Sandbox Compute** | < 5 minutes | < 60 seconds (Lease timeout) | Docker / Kubernetes Pool |
| **Relayer / Transaction Outbox** | < 15 minutes | **0 seconds (Intents in DB)** | `blockchain_tx_outbox` Table |

---

### 2. Failure Domain Recovery Procedures

---

#### 2.1 PostgreSQL Failure
- **Architecture**: Multi-AZ PostgreSQL with automated read-replica promotion and continuous Write-Ahead Logging (WAL) archiving to S3.
- **Failure Modes**: Hardware crash, volume corruption, unhandled transaction deadlock.
- **Recovery Steps**:
  1. Automated RDS failover promotes standby replica within 60-120 seconds.
  2. If unrecoverable volume corruption occurs, restore from latest automated snapshot via Point-in-Time Recovery (PITR).
  3. Re-synchronize application state with blockchain:
     ```bash
     .venv/bin/python3 -c "import asyncio; from app.services.blockchain.reconciliation import BlockchainReconciliationService; asyncio.run(BlockchainReconciliationService.reconcile_all_events())"
     ```
  4. The indexer scans blocks from `last_indexed_block` up to `current_block` to rebuild missing application records.

---

#### 2.2 Redis Failure
- **Architecture**: Redis 7.2 with AOF persistence enabled and Transactional Outbox pattern.
- **Failure Modes**: Memory exhaustion, node crash, network partition.
- **Recovery Steps**:
  1. Restart Redis container or failover to ElastiCache replica:
     ```bash
     docker restart agentchain-redis
     ```
  2. The backend operates on a fail-closed model: when Redis is unreachable, API requests still persist to PostgreSQL (`ExecutionOutbox` entries remain with `published_at = NULL`).
  3. Once Redis is online, execute outbox rehydration:
     ```bash
     .venv/bin/python3 -c "import asyncio; from app.services.outbox import OutboxDispatcher; asyncio.run(OutboxDispatcher.dispatch_pending())"
     ```
  4. All queued tasks are immediately repopulated into Redis Streams.

---

#### 2.3 Worker Failure
- **Architecture**: Distributed worker nodes running isolated process/container runners with dynamic lease heartbeats.
- **Failure Modes**: Worker container OOM, host crash, process hang during execution.
- **Recovery Steps**:
  1. Every claimed job is protected by a 60-second database lease (`lease_expires_at`).
  2. If a worker crashes, its heartbeat stops.
  3. The `QueueRecoveryService` identifies stale leases:
     ```sql
     SELECT id FROM agent_executions 
     WHERE status = 'RUNNING' AND lease_expires_at < NOW();
     ```
  4. The stale execution is reclaimed, `attempt_count` is incremented, and the task is requeued up to `max_attempts` (default 3). If `attempt_count >= max_attempts`, the task transitions cleanly to `FAILED`.

---

#### 2.4 Orchestrator Failure
- **Architecture**: LangGraph DAG state machine persisted at every step into `orchestration_checkpoints`.
- **Failure Modes**: Node crash mid-DAG execution, unhandled exception in state reducer.
- **Recovery Steps**:
  1. Orchestration engine loads the latest valid checkpoint from PostgreSQL:
     ```sql
     SELECT state_json, state_hash FROM orchestration_checkpoints 
     WHERE orchestration_id = :id ORDER BY created_at DESC LIMIT 1;
     ```
  2. Checkpoint state hash is verified against `SHA-256(state_json)`.
  3. Orchestrator resumes graph execution from the last completed node. No completed tasks are re-executed (idempotent step execution).

---

#### 2.5 Relayer Failure
- **Architecture**: `BlockchainRelayer` consuming `blockchain_tx_outbox` with durable PostgreSQL nonce management.
- **Failure Modes**: Relayer process crash, gas price spike, network partition from RPC.
- **Recovery Steps**:
  1. Relayer process is supervised by `systemd` or Kubernetes (auto-restart with exponential backoff).
  2. On startup, relayer executes pre-flight checks:
     - Verifies chain ID against configured chain.
     - Confirms signer health and address binding.
     - Synchronizes `relayer_nonces` table against `w3.eth.get_transaction_count(address, 'pending')`.
  3. Relayer resumes processing unconfirmed intents from `blockchain_tx_outbox`. Unsubmitted or stalled intents are rebroadcast with 120% gas bumping.

---

#### 2.6 Indexer Failure
- **Architecture**: Polling event indexer tracking blocks and event logs via `indexed_blocks` table.
- **Failure Modes**: RPC disconnection, missing logs due to provider filter expiration, process crash.
- **Recovery Steps**:
  1. Restart indexer daemon.
  2. Indexer queries `MAX(block_number)` from `indexed_blocks` for the chain.
  3. Indexer resumes from `last_block - 32` to guarantee re-verification across the entire 32-block confirmation window.
  4. Duplicate event ingestion is strictly idempotent: `blockchain_events` table enforces unique constraint `(chain_id, transaction_hash, log_index)`.

---

#### 2.7 RPC Outage
- **Architecture**: Fail-closed multi-provider RPC client abstraction with health probes.
- **Failure Modes**: Official node rate limiting, HTTP 504 timeouts, node desynchronization.
- **Recovery Steps**:
  1. Relayer and Indexer catch RPC connection exceptions fail-closed.
  2. Update environment variable `BASE_SEPOLIA_RPC_URL` (or `RPC_URL`) to secondary fallback provider:
     - Fallback 1: `https://sepolia.base.org`
     - Fallback 2: Alchemy Dedicated Testnet Endpoint
     - Fallback 3: Infura / QuickNode
  3. Restart backend service. Verify chain ID match via `verify_base_sepolia_read_only.py`.

---

#### 2.8 AWS KMS Outage
- **Architecture**: `AwsKmsSigner` using AWS KMS FIPS 140-2 Level 3 Hardware Security Modules.
- **Failure Modes**: Regional AWS outage, IAM throttling, KMS service unavailability.
- **Recovery Steps**:
  1. When KMS is unreachable, relayer fails closed: `SignerUnavailableError` is raised.
  2. Transaction intents remain safely queued in `blockchain_tx_outbox` in PostgreSQL. Zero transactions are lost.
  3. If primary AWS region suffers prolonged multi-hour outage:
     - Follow KMS Disaster Recovery playbook to switch to multi-region replica key (if provisioned) or await regional restoration.
     - Once KMS connectivity restores, relayer resumes outbox dispatch automatically.

---

#### 2.9 Signer Key Compromise
- **Architecture**: Hardware-isolated private key (`ECC_SECG_P256K1`) with zero raw key export capability.
- **Failure Modes**: IAM credential theft allowing unauthorized `kms:Sign` calls.
- **Recovery Steps**:
  1. Immediately execute break-glass runbook: [docs/security/aws-kms-emergency-response.md](file:///Users/shravani/Desktop/AgentChain_AIWorkSpace/docs/security/aws-kms-emergency-response.md).
  2. Disable compromised KMS key in AWS CLI.
  3. Multi-sig admin executes `Escrow.pause()`.
  4. Follow key rotation runbook: [docs/security/aws-kms-key-rotation-runbook.md](file:///Users/shravani/Desktop/AgentChain_AIWorkSpace/docs/security/aws-kms-key-rotation-runbook.md).
  5. Provision new clean KMS key, update Vault secrets, and re-grant on-chain roles via multi-sig.

---

#### 2.10 Chain Reorganization
- **Architecture**: Authoritative 32-block confirmation policy enforced across all state machines.
- **Failure Modes**: L2 sequencer rollback or L1 reorg exceeding typical 1-2 block depths.
- **Recovery Steps**:
  1. If depth <= 32 blocks:
     - `ReorgHandler` automatically detects parent hash divergence.
     - Rewinds `indexed_blocks` and unrolls affected `blockchain_events`.
     - Re-projects canonical states.
  2. If depth > 32 blocks:
     - Automated circuit-breaker halts settlements: `BLOCK_MAINNET_SETTLEMENT = true`.
     - Follow manual recovery runbook: [docs/DEEP_REORG_RUNBOOK.md](file:///Users/shravani/Desktop/AgentChain_AIWorkSpace/docs/DEEP_REORG_RUNBOOK.md).

---

#### 2.11 Application Rollback
- **Architecture**: Stateless containerized backend with versioned database migrations (Alembic) and immutable smart contracts.
- **Failure Modes**: Deployment of corrupted backend release candidate with application-level bug.
- **Recovery Steps**:
  1. Revert container image to previous release candidate git tag.
  2. If database schema migration was applied:
     - Check if migration is backward-compatible.
     - If necessary, run Alembic downgrade:
       ```bash
       alembic downgrade -1
       ```
  3. Smart contracts are immutable and backward-compatible with older backend versions.
  4. Execute full regression suite to verify system integrity:
     ```bash
     .venv/bin/pytest backend/tests/test_production_readiness.py -v
     ```

---

### 3. Local Anvil Development & Test Recovery Runbook

When developing locally or running end-to-end Anvil integration tests, if the Anvil container is restarted, execute the following command to reseed local smart contracts:

```bash
# 1. Ensure local services are healthy
docker compose up -d

# 2. Deploy contracts to local Anvil
cd contracts
DEPLOYER_PRIVATE_KEY=0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80 \
forge script script/Deploy.s.sol:DeployScript \
  --rpc-url http://localhost:8545 \
  --broadcast
cd ..

# 3. Verify deployment bytecode presence
.venv/bin/python3 -c "from web3 import Web3; w3 = Web3(Web3.HTTPProvider('http://localhost:8545')); assert len(w3.eth.get_code('0x5FbDB2315678afecb367f032d93F642f64180aa3')) > 0; print('Anvil Reseeded Successfully!')"
```
