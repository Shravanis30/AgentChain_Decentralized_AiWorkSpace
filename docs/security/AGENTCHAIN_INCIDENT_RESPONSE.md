# AgentChain Comprehensive Incident Response Plan (IRP)
## Phase 6.11 — Production Release Freeze & Security Response Standard

**Document ID**: IRP-SEC-6.11-001  
**Classification**: OPERATIONAL INCIDENT MANUAL  
**Status**: ACTIVE / FROZEN RELEASE CANDIDATE  
**Date**: October 4, 2026  

---

### 1. Incident Severity Classification & Escalation Matrix

| Severity | Definition | Initial Response SLA | Escalation Target |
| :--- | :--- | :--- | :--- |
| **SEV-1 (Critical)** | Active fund drainage, private key / KMS compromise, smart contract exploit, or unauthorized settlement broadcast. | **< 15 minutes** | Security Lead, Platform Lead, Multi-Sig Signers |
| **SEV-2 (High)** | RPC eclipse attack, database corruption, worker container breakout, or deep chain reorganization (>16 blocks). | **< 30 minutes** | DevSecOps On-Call, Reliability Lead |
| **SEV-3 (Medium)** | Redis queue partition, worker lease starvation, transient relayer gas exhaustion, or SIWE auth rate-limiting trigger. | **< 2 hours** | Backend Engineering Team |
| **SEV-4 (Low)** | Minor logging inconsistencies, non-blocking telemetry alert, or informational warning. | **Next business day** | Application Maintainers |

---

### 2. Emergency Incident Playbooks

---

#### 2.1 Incident Playbook: Signer / KMS Compromise
*Source of Truth: [docs/security/aws-kms-emergency-response.md](file:///Users/shravani/Desktop/AgentChain_AIWorkSpace/docs/security/aws-kms-emergency-response.md)*

1. **Immediate Circuit Breaker**:
   - Stop relayer process immediately: `systemctl stop agentchain-relayer` (or kill worker container).
   - In AWS Console / CLI, disable KMS key: `aws kms disable-key --key-id <KMS_KEY_ARN>`.
   - Detach `kms:Sign` IAM policy from the relayer runtime identity.
2. **On-Chain Containment**:
   - Multi-sig admin / pauser executes `Escrow.pause()` to freeze all deposits, locks, releases, and refunds.
3. **Forensic Evidence Collection**:
   - Snapshot AWS CloudTrail logs filtering for `kms:Sign` and `kms:GetPublicKey` calls.
   - Dump PostgreSQL `blockchain_transaction_intents` and `blockchain_transactions`.
4. **Key Rotation & Recovery**:
   - Follow [docs/security/aws-kms-key-rotation-runbook.md](file:///Users/shravani/Desktop/AgentChain_AIWorkSpace/docs/security/aws-kms-key-rotation-runbook.md) to provision clean replacement key.
   - Derive new Ethereum address via `verify_production_kms_configuration.py`.
   - Multi-sig grants `NOTARIZER_ROLE` and `REPUTATION_ORACLE_ROLE` to the new address and revokes old key.

---

#### 2.2 Incident Playbook: Suspicious Blockchain Transactions
1. **Detection**: Alert triggered by transaction value threshold breach, unrecognized contract destination, or relayer gas drain.
2. **Immediate Triage**:
   - Query mempool and pending transactions: `w3.eth.get_transaction(tx_hash)`.
   - Inspect `blockchain_transaction_intents` where `status = 'SUBMITTED'`.
3. **Mempool Replacement / Cancellation**:
   - If transaction is pending in mempool with invalid parameters:
     - Relayer submits a zero-value cancellation transaction with identical nonce and 150% gas price (`to = relayer_address`, `value = 0`).
4. **Containment**:
   - If destination contract was unallowlisted, immediately halt `BlockchainRelayer` daemon.
   - Verify all nonces in `relayer_nonces` table against on-chain transaction count.

---

#### 2.3 Incident Playbook: Incorrect Settlement / Distribution Anomaly
1. **Detection**: Alert `SettlementConservationMismatch` or user dispute regarding 85/10/5 distribution.
2. **Analysis**:
   - Check `settlements` table and `distribution_history` in PostgreSQL.
   - Retrieve transaction logs for `DistributionExecuted(distributionId, escrowId, ...)`.
   - Verify equation: `developerAmount + stakerAmount + daoAmount == grossAmount`.
3. **Containment**:
   - If incorrect payout occurred due to configuration error:
     - Halt settlement pipeline: `SET BLOCK_MAINNET_SETTLEMENT = true`.
     - Halt `DistributionService` worker dispatch.
4. **Remediation**:
   - Correct database discrepancy via authorized migration script.
   - If funds were underpaid to developer, disburse difference from platform treasury reserve.

---

#### 2.4 Incident Playbook: Smart Contract Vulnerability
1. **Detection**: External security disclosure, Immunefi bug bounty report, or anomalous revert surge.
2. **Emergency Action**:
   - Invoke `PAUSER_ROLE`:
     - `Escrow.pause()` (blocks new escrows, releases, and disputes).
     - `AgentRegistry.pause()` (blocks new agent registrations).
3. **Vulnerability Assessment**:
   - Reproduce flaw in local Foundry test suite using adversarial PoC (`test/EscrowExploit.t.sol`).
   - Assess contract immutability:
     - Production contracts (`Escrow.sol`, `RevenueDistributor.sol`, `ResultNotary.sol`) are immutable (non-proxy).
     - Any code remediation requires deploying a clean replacement contract.
4. **Migration & Drain Prevention**:
   - If active funds are at risk, arbitrator coordinates emergency dispute resolution or client refunds before contract retirement.
   - Deploy patched contract version; update backend contract address registry in Vault and restart services.

---

#### 2.5 Incident Playbook: Worker Execution Compromise
1. **Detection**: Worker anomaly alert, sandbox timeout spike, or unauthorized host filesystem access attempt.
2. **Immediate Quarantine**:
   - Terminate worker host instance or Kubernetes pod: `kubectl delete pod <worker-pod>`.
   - Invalidate worker lease in database:
     ```sql
     UPDATE agent_executions 
     SET lease_owner = NULL, lease_expires_at = NOW() 
     WHERE worker_id = 'compromised-worker-id' AND status = 'RUNNING';
     ```
3. **Forensic Analysis**:
   - Preserve container cgroups, memory core dump, and host audit logs.
   - Inspect malicious agent image hash and manifest in `agent_versions`.
4. **Mitigation**:
   - Blacklist offending agent: `UPDATE agents SET is_active = false WHERE id = 'malicious-agent-id'`.
   - Quarantine developer account and revoke SIWE authentication sessions.

---

#### 2.6 Incident Playbook: Credential & Secret Exposure
1. **Detection**: Git secret detection webhook, AWS GuardDuty alert, or compromised API token notice.
2. **Immediate Revocation**:
   - Database credentials: rotate PostgreSQL password in AWS Secrets Manager; terminate active connections (`pg_terminate_backend`).
   - Redis token: execute `CONFIG SET requirepass <new_password>` and update backend connection pool.
   - S3 / MinIO keys: deactivate compromised IAM Access Key immediately.
3. **Audit**:
   - Query CloudTrail and PostgreSQL access logs for unauthorized queries during the compromise window.
   - Scan git commit history to ensure no persistent exposures exist.

---

#### 2.7 Incident Playbook: RPC Provider Compromise / Outage
1. **Detection**: `RPCChainIdMismatchError`, persistent HTTP 5xx errors, or stale block height.
2. **Failover Execution**:
   - Relayer and indexer automatically catch RPC errors.
   - Switch active RPC URL via environment configuration:
     - Primary: Base Sepolia Official (`https://sepolia.base.org`)
     - Secondary Fallback: Alchemy / Infura dedicated node.
   - Restart `BlockchainRpcClient` instances.
3. **Block Height Verification**:
   - Run `eth_blockNumber` and compare against public block explorer (BaseScan) to ensure no fork/stale node ingestion.

---

#### 2.8 Incident Playbook: PostgreSQL Database Corruption / Crash
1. **Detection**: Connection failure, `DataError`, or database container crash loop.
2. **Crash Recovery**:
   - PostgreSQL container / RDS instance automated restart.
   - Verify disk storage and WAL log integrity: `pg_controldata /var/lib/postgresql/data`.
3. **Point-in-Time Recovery (PITR)**:
   - If unrecoverable data loss occurs:
     - Restore PostgreSQL from latest hourly snapshot.
     - Execute `BlockchainReconciliationService.reconcile_chain()` to ingest all missing on-chain events from the blockchain since snapshot timestamp.
     - Blockchain state is immutable ground truth for all financial transactions.

---

#### 2.9 Incident Playbook: Redis / Queue Outage & Partition
1. **Detection**: `redis.exceptions.ConnectionError`, Outbox backlog accumulation.
2. **Behavior Under Failure**:
   - System operates on **fail-closed transactional outbox**:
     - Client requests persist execution records to PostgreSQL with `status = 'QUEUED'` and `ExecutionOutbox.published_at = NULL`.
     - Zero task loss occurs during Redis outage.
3. **Recovery Procedure**:
   - Restart Redis server: `docker restart agentchain-redis` (or restart ElastiCache node).
   - Verify Redis health: `redis-cli ping`.
   - Trigger `OutboxDispatcher.dispatch_pending()`: scans all unpublished outbox entries and rehydrates the Redis Streams queue.

---

#### 2.10 Incident Playbook: Blockchain Deep Reorganization Anomaly
*Source of Truth: [docs/DEEP_REORG_RUNBOOK.md](file:///Users/shravani/Desktop/AgentChain_AIWorkSpace/docs/DEEP_REORG_RUNBOOK.md)*

1. **Detection**: `ReorgHandler` detects parent hash mismatch; depth exceeds 32 blocks.
2. **Immediate Lock**:
   - Relayer halts submission on chain.
   - Settlement service blocks authorization: `ReconciliationStatus.REORG_DETECTED`.
3. **Rewind & Reconciliation**:
   - Follow steps in `docs/DEEP_REORG_RUNBOOK.md`:
     1. Stop indexer service.
     2. Identify common ancestor block number and hash.
     3. Mark all orphaned `blockchain_events` as `is_canonical = false`.
     4. Re-index canonical blocks from ancestor to current head.
     5. Re-run projectors (`EscrowStateProjector`, `NotarizationProjector`, `ReputationProjector`).
     6. Advance confirmation depth by 32 blocks before resuming settlements.

---

### 3. Post-Incident Review & Sign-Off

Within 24 hours of resolving any SEV-1 or SEV-2 incident, the engineering team must publish a Root Cause Analysis (RCA) including:
- Exact timeline of events (UTC).
- Root cause category.
- Total financial impact (USDC / gas).
- Preventive measures and regression tests added.
