# Phase 6.6.1 Completion & Verification Report: Selection Integrity & Reputation Snapshot Hardening

**Date:** 2026-09-29  
**Phase:** 6.6.1 — Selection Integrity & Reputation Snapshot Hardening  
**Status:** ✅ RELEASE GATE PASSED  

---

## 1. Executive Summary

Phase 6.6 introduced reputation-aware agent discovery and deterministic agent selection. Phase 6.6.1 is an exhaustive verification and hardening pass that addresses edge cases, eliminates semantic ambiguity, guarantees immutable decision audit trails, and hardens deterministic reproducibility.

### Key Hardening Accomplishments:
1. **Explicit Reputation State Classification**: Distinctly tracks `CANONICAL` ($N > 0$ with computed score), `COLD_START` ($N = 0$ neutral row at 5000), `ABSENT` (no reputation record; default 5000 neutrality), and `UNAVAILABLE` (error/unreachable).
2. **Elimination of Hidden Evidence-Count Ranking**: Removed unintended `evidence_count DESC` sorting. Ranking strictly adheres to `SelectionPolicyV1`: primary `score_scaled DESC`, secondary `agent_id ASC` (tie-breaker). `evidence_count` is captured as audit metadata only.
3. **Reselection & Retry Immutability**: Added `attempt_number` and unique constraint `uq_selection_decision_task_attempt` on `(task_id, attempt_number)`. Reselection records a new historical row rather than mutating prior records.
4. **Order-Normalised Canonical Hashing**: Candidate sets are explicitly sorted by `agent_id ASC` before hashing, guaranteeing candidate order independence and tamper detection.
5. **Full System Regression**: 435 backend tests passed (25 new Phase 6.6.1 hardening tests), 115 Foundry contract tests passed (128,000 invariant calls), 8 indexer/SDK tests passed, 0 config drift.

---

## 2. Selection Policy & State Semantics

### 2.1 Reputation State Enum

```python
class ReputationState(str, enum.Enum):
    CANONICAL = "CANONICAL"      # Explicit score from DB with verified executions (N > 0)
    COLD_START = "COLD_START"    # Explicit score from DB for new agent (N == 0, score=5000)
    ABSENT = "ABSENT"            # No reputation row exists in DB; treated as cold-start (5000)
    UNAVAILABLE = "UNAVAILABLE"  # Score calculation unreachable; agent excluded from ranking
```

### 2.2 Ranking Policy (`SelectionPolicyV1`)

For all eligible candidates matching capability or task constraints:
1. Primary criterion: `score_scaled` **DESC** (higher reputation score wins)
2. Tie-breaker: `agent_id` **ASC** (lexicographical UUID comparison)

`evidence_count` is **never** used as a ranking criterion or confidence multiplier in `SelectionPolicyV1`.

### 2.3 Canonical Selection Hash Specification

The deterministic selection hash is computed over canonical JSON:
```json
{
  "constraints": {
    "task_key": "<task_key>",
    "capability": "<capability>",
    "agent_id": "<uuid|null>",
    "agent_version": "<version|null>"
  },
  "candidates": [
    {
      "agent_id": "<uuid>",
      "agent_name": "<name>",
      "score_scaled": 8000,
      "reputation_state": "CANONICAL",
      "policy_version": "ReputationPolicyV1",
      "total_verified_executions": 10,
      "evidence_hash": "<sha256>"
    }
  ],
  "selected_agent_id": "<uuid>",
  "selected_version": "<version_string>",
  "policy": "SelectionPolicyV1"
}
```
*Note: `candidates` list is sorted strictly by `agent_id ASC` to ensure hash order-invariance.*

---

## 3. Database Schema Migration

**Alembic Migration:** `017_selection_hardening.py`  
Applied to `selection_decisions` table:
- `attempt_number` (`INTEGER NOT NULL DEFAULT 1`)
- `reputation_state` (`VARCHAR(32) NOT NULL DEFAULT 'CANONICAL'`)
- Unique constraint: `uq_selection_decision_task_attempt` on `(task_id, attempt_number)`
- Index: `ix_selection_decisions_task_attempt` on `(task_id, attempt_number)`

---

## 4. Phase 6.6.1 Test Matrix & Verification Results

All 25 tests in `backend/tests/test_agent_selection.py` passed:

| Category | Test Case | Description | Result |
|---|---|---|---|
| **A. State Classification** | `test_A1_canonical_reputation` | $N > 0$ score row correctly classified as `CANONICAL` | **PASSED** |
| | `test_A2_cold_start_reputation_row_exists` | $N = 0$, score 5000 classified as `COLD_START` | **PASSED** |
| | `test_A3_absent_reputation_no_row` | No score row classified as `ABSENT`, score defaults to 5000 | **PASSED** |
| | `test_A4_canonical_and_cold_start_not_conflated` | Verifies states are distinct even when score is both 5000 | **PASSED** |
| **B. Selection Policy** | `test_B1_highest_score_wins` | Candidate with highest `score_scaled` is selected | **PASSED** |
| | `test_B2_tiebreak_agent_id_asc_not_evidence_count` | Tied score breaks tie on `agent_id ASC`, ignores evidence count | **PASSED** |
| | `test_B3_evidence_count_is_informational_only` | Lower evidence count agent wins if score is higher | **PASSED** |
| | `test_B4_lifecycle_filtering` | Non-published agents (DRAFT, DEPRECATED, ARCHIVED) excluded | **PASSED** |
| **C. Hash Integrity** | `test_C1_hash_recomputation_matches_stored` | Hash matches recomputed payload from stored decision | **PASSED** |
| | `test_C2_hash_detects_tampering` | Tampered score in payload changes the canonical hash | **PASSED** |
| | `test_C3_candidate_order_independence` | Discovery candidate order permutation does not alter hash | **PASSED** |
| **D. Snapshot Immutability** | `test_D1_score_change_does_not_mutate_stored_decision` | Post-selection score mutation does not alter stored decision | **PASSED** |
| | `test_D2_evidence_count_change_does_not_mutate_decision` | Post-selection execution additions do not alter decision | **PASSED** |
| **E. Version Pinning** | `test_E1_version_pinned_at_selection` | Exact published version pinned in selection result | **PASSED** |
| | `test_E2_version_change_does_not_affect_historical_decision` | Subsequent version publication preserves historical decision | **PASSED** |
| **F. TOCTOU Protection** | `test_F1_new_higher_score_agent_does_not_replace_pinned_selection` | New higher reputation agent does not displace pinned agent | **PASSED** |
| | `test_F2_toctou_combined_score_change_and_new_agent` | Pinned selection survives simultaneous score & candidate changes | **PASSED** |
| **G. Reselection Traceability** | `test_G1_first_selection_attempt_number_is_one` | Initial selection assigns `attempt_number = 1` | **PASSED** |
| | `test_G2_reselection_creates_new_row_not_overwrite` | Reselection produces new row with `attempt_number = 2` | **PASSED** |
| **H. Request Constraints** | `test_H1_client_constraints_cannot_override_reputation` | Caller cannot inject fabricated reputation scores | **PASSED** |
| | `test_H2_reputation_comes_from_db_not_request` | Score strictly derived from database state | **PASSED** |
| **I. Concurrency** | `test_I1_concurrent_selection_same_candidates` | Concurrent selections on same candidate set yield identical decision | **PASSED** |
| **J. Cold-Start Matrix** | `test_J1_two_absent_agents_deterministic_by_agent_id` | Multiple `ABSENT` agents tie-break by `agent_id ASC` | **PASSED** |
| | `test_J2_cold_start_row_vs_canonical_same_score` | `COLD_START` vs `CANONICAL` at 5000 tie-breaks by `agent_id ASC` | **PASSED** |
| | `test_J3_absent_vs_cold_start_same_score_tie_break` | `ABSENT` vs `COLD_START` at 5000 tie-breaks by `agent_id ASC` | **PASSED** |

---

## 5. System Regression Audit

| Component | Command | Result | Status |
|---|---|---|---|
| Backend Test Suite | `pytest backend/tests/` | 435 passed, 0 failed, 392 warnings in 86.14s | **PASS** |
| Indexer & Agent SDK | `pytest services/indexer/tests packages/agent-sdk/tests` | 8 passed, 0 failed in 0.81s | **PASS** |
| Smart Contracts | `forge test` | 115 passed, 0 failed (128,000 invariant calls) | **PASS** |
| Authoritative Config Drift | `python scripts/check_config_drift.py` | 0 drift detected; all invariants hold | **PASS** |
| Web Application | `npm run typecheck && npm run lint` | 0 errors | **PASS** |

---

## 6. Conclusion & Release Gate Signoff

Phase 6.6.1 has successfully hardened selection integrity, established explicit reputation state classification, eliminated hidden ranking biases, guaranteed attempt-based immutable audit logging, and preserved complete backwards compatibility across the entire AgentChain platform.

**Phase 6.6.1 is complete, verified, and APPROVED.**
