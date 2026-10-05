# AgentChain Deep Reorg Operator Runbook

> **Authoritative reorg window: 32 blocks**
> Reorgs deeper than 32 blocks trigger automatic chain halt and require manual operator recovery.

---

## 1. Overview

A chain reorganization ("reorg") occurs when the network switches to a longer competing chain,
invalidating previously-canonical blocks and their transactions.

AgentChain's reorg handling policy:

| Reorg Depth | Automatic Response | Manual Action Required |
|------------|-------------------|----------------------|
| 1–32 blocks | Indexer reprocesses. Affected events marked non-canonical. Settlements demoted. | None if recovered |
| > 32 blocks | **FULL HALT**: `ChainReorganization` record marked `FAILED`. All authorizations blocked. | YES — this runbook |

---

## 2. Detection

### Automatic Detection

The `ReorgHandler` detects all reorgs via block hash comparison during each indexer cycle.

When a reorg depth > 32 blocks is confirmed:
1. `ChainReorganization` record is created with `status=FAILED`.
2. `DeepReorganizationError` is raised and logged as CRITICAL.
3. All new settlement authorizations on the chain emit `SettlementAuthorizationError`.
4. `SettlementReconciliationService` transitions all `PENDING_AUTHORIZATION` and `SUBMITTED`
   settlements on the chain to `BLOCKED`.

### Alert Triggering

- `DeepReorgDetected` — Prometheus alert fires immediately (0m for: clause).
- `ReorgRecoveryManualActionRequired` — fires after 60 minutes if chain remains blocked.

### What to Look For

```
CRITICAL [reorg_handler] DeepReorganizationError: Chain reorg exceeds max depth 32 on chain 84532
ERROR    [settlement.service] Active unrecovered chain reorganization detected on chain 84532. Authorizations are halted...
```

---

## 3. Immediate Response

### Step 1: Confirm the Alert

1. Verify the alert chain ID and timestamp in Prometheus / Grafana.
2. Check application logs for `DeepReorganizationError`.
3. Confirm `ChainReorganization` table entry: `status='FAILED'`, `chain_id=<affected>`.

```sql
-- READ ONLY. Do not modify.
SELECT id, chain_id, start_block, detected_depth, status, created_at
FROM chain_reorganizations
WHERE status = 'FAILED'
ORDER BY created_at DESC
LIMIT 5;
```

### Step 2: Do NOT Panic and Do NOT

- **DO NOT** manually set any `ChainReorganization` record to `RECOVERED` without full investigation.
- **DO NOT** manually update settlement statuses via SQL without audit record.
- **DO NOT** restart the indexer without verifying canonical chain state.
- **DO NOT** clear the reorg record without verifying the RPC provider agrees on canonical blocks.
- **DO NOT** allow partial recovery — only full canonical verification is acceptable.

---

## 4. Investigate Canonical Chain State

### Step 1: Verify Your RPC Provider

```bash
# Check which chain head your primary RPC reports:
curl -X POST https://sepolia.base.org \
  -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"eth_blockNumber","params":[]}'

# Verify with a secondary RPC provider (e.g., Alchemy, Infura):
curl -X POST https://base-sepolia.g.alchemy.com/v2/$ALCHEMY_KEY \
  -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"eth_blockNumber","params":[]}'
```

**Expected**: Both providers agree on block number (within ~5 blocks).
**Red flag**: Providers disagree by >10 blocks — one provider is lagging or split.

### Step 2: Verify Block Hashes Around the Reorg

Find the `start_block` from the `ChainReorganization` record. Then verify:

```bash
# Replace START_BLOCK with the detected reorg start block
# Confirm that the canonical block at that height is what our indexer stored

curl -X POST https://sepolia.base.org \
  -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"eth_getBlockByNumber","params":["0x'$(printf '%x' START_BLOCK)'",false]}'
```

Compare returned `hash` against what is stored in `blockchain_indexed_blocks`:

```sql
-- READ ONLY
SELECT block_number, block_hash, is_canonical, created_at
FROM blockchain_indexed_blocks
WHERE chain_id = <chain_id>
  AND block_number >= <start_block - 10>
  AND block_number <= <start_block + 5>
ORDER BY block_number;
```

### Step 3: Identify Affected Events

```sql
-- READ ONLY: Events that were in the reorg window and may be orphaned
SELECT e.id, e.transaction_hash, e.block_number, e.event_name, e.is_canonical, e.status
FROM blockchain_events e
WHERE e.chain_id = <chain_id>
  AND e.block_number >= <start_block>
ORDER BY e.block_number;
```

### Step 4: Identify Affected Settlements

```sql
-- READ ONLY: Settlements currently in BLOCKED status on the affected chain
SELECT s.id, s.escrow_id, s.action, s.status, s.updated_at
FROM settlements s
WHERE s.chain_id = <chain_id>
  AND s.status = 'BLOCKED';
```

---

## 5. RPC Provider Verification

If providers disagree:

1. Rotate the primary RPC URL to the secondary provider.
2. Restart indexer against the secondary provider.
3. Wait for the indexer to re-sync and re-confirm canonical events.
4. Monitor for `blockchain_indexing_lag_blocks` to drop to < 10.

Update `BASE_SEPOLIA_RPC_URL` in the environment and redeploy.

---

## 6. Recovery Authorization

**Only a designated on-call operator with `ADMIN` role may authorize recovery.**

A recovery action is:
1. **Authenticated**: operator must be logged in with SIWE/RBAC.
2. **Audited**: each recovery step creates an `AuditLog` entry.
3. **Fail-closed**: partial recovery attempts are rejected.

### Recovery Criteria

Recovery is only permitted when ALL of the following are true:

- [ ] Primary and secondary RPC providers agree on canonical chain
- [ ] `blockchain_indexing_lag_blocks` < 10 for at least 5 minutes
- [ ] All events in the reorg window have been re-indexed
- [ ] `blockchain_events` records have `is_canonical = TRUE` or `FALSE` (not ambiguous)
- [ ] Affected settlement states have been manually verified against canonical events

### Recovery SQL (Execute Only After All Criteria Are Met)

```sql
-- OPERATOR ACTION: Update reorg record to RECOVERED
-- Requires written post-incident authorization from senior engineer
-- MUST be accompanied by an AuditLog entry

BEGIN;

-- 1. Mark the reorg as recovered
UPDATE chain_reorganizations
SET status = 'RECOVERED', updated_at = NOW()
WHERE id = '<reorg_record_id>'
  AND status = 'FAILED';

-- 2. Verify affected settlements are in expected state
-- (do not unblock BLOCKED settlements yet — let reconciliation do it)

-- 3. Create audit log
INSERT INTO audit_logs (id, event_type, metadata, created_at)
VALUES (
  gen_random_uuid(),
  'DEEP_REORG_RECOVERY',
  '{"operator": "<operator_user_id>", "reorg_id": "<reorg_record_id>", "authorization": "incident-YYYY-MM-DD"}',
  NOW()
);

COMMIT;
```

**After this SQL**: The `SettlementReconciliationService` will automatically pick up `BLOCKED` settlements
and attempt to re-authorize them against the now-restored canonical chain state.

---

## 7. What Operators Must NOT Do

| Prohibited Action | Reason |
|------------------|--------|
| Manually set `settlement.status = 'CONFIRMED'` | Bypasses canonical event verification |
| Manually set `blockchain_event.is_canonical = TRUE` | Bypasses indexer reorg detection |
| Delete from `chain_reorganizations` | Removes audit trail |
| Set `BLOCK_MAINNET_SETTLEMENT=false` as a "fix" | Unrelated; never do this |
| Skip the AuditLog entry | Non-repudiation requirement |
| Restart indexer without RPC verification | May cause further corruption |

---

## 8. Post-Recovery Verification

After marking the reorg as RECOVERED:

1. Monitor `SettlementReconciliationService` logs for BLOCKED → CONFIRMED or FAILED transitions.
2. Verify `blockchain_indexing_lag_blocks` remains < 10.
3. Verify no new `ChainReorganization` records with `status=FAILED` appear.
4. Review all settlements that were BLOCKED — confirm each resolved correctly.
5. Write a post-incident report documenting:
   - Root cause
   - Affected blocks / events / settlements
   - Recovery actions taken
   - Operator who authorized recovery
   - Preventive measures

---

## 9. Shallow Reorg Handling (No Operator Action Required)

For reorgs <= 32 blocks, the system recovers automatically:

1. `ReorgHandler` marks affected events `is_canonical = FALSE`.
2. `EscrowStateProjector` re-evaluates canonical escrow state from remaining canonical events.
3. `SettlementReconciliationService` demotes affected settlements to `SUBMITTED` or `BLOCKED`.
4. On next reconciliation cycle, settlements are re-verified against the restored canonical state.

Operator only needs to monitor for recovery. No SQL intervention required.

---

## 10. Reorg Safety Testing

Test: `test_reorg_after_confirmation` in `backend/tests/test_settlement_adversarial_hardening.py`
Test: `test_deep_reorg_fail_closed_behavior` in `backend/tests/test_settlement_adversarial_hardening.py`

---

*Authoritative reorg depth: 32 blocks | Updated: Phase 6.2B.2*
