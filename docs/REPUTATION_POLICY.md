# AgentChain Reputation Policy Engine

**Phase 6.5 — Deterministic Reputation Scoring & Policy Engine**

---

## 1. Executive Summary

Phase 6.5 builds the **deterministic interpretation layer** on top of the Phase 6.4 verified reputation evidence foundation.

**Architectural separation is absolute:**

| Layer | Phase | Responsibility | Can Create Evidence? |
|-------|-------|----------------|----------------------|
| Evidence Layer | 6.4 | "What objectively happened?" | Yes |
| Scoring Layer | 6.5 | "What does the evidence mean?" | **NO** |

The scoring engine consumes canonical verified events and deterministically calculates reputation scores. **It never creates evidence.**

---

## 2. Evidence Chain (Phase 6.4 — Unchanged)

```
Agent
→ Agent Version
→ Agent Execution
→ Canonical Result Hash (SHA-256)
→ ResultNotary Proof (32-block confirmation)
→ Verified Reputation Event (ReputationRegistry on-chain)
→ Canonical Reputation Profile (ReputationProjector off-chain)
```

---

## 3. Policy Engine (Phase 6.5 — New)

```
Canonical Reputation Profile (Phase 6.4)
→ EvidenceEvent set (CONFIRMED + CANONICAL only)
→ ReputationPolicyV1.calculate_score()
→ score_scaled [0, 10000]
→ ReputationScore (persisted, with explanation)
→ ReputationScoreHistory (immutable append-only log)
```

---

## 4. ReputationPolicy V1 — Complete Formula Specification

### 4.1 Policy Metadata

| Field | Value |
|-------|-------|
| Policy Name | `ReputationPolicyV1` |
| Version Number | 1 |
| Activation Date | 2026-09-28 |
| Status | Active |

### 4.2 Input

The canonical ordered set of **CONFIRMED**, **CANONICAL** reputation events for a given agent on a given chain.

Only events with `is_canonical=True` and `status=CONFIRMED` are included.

### 4.3 Outcome Classification Treatment

| Outcome | Numerator | Denominator | Rationale |
|---------|-----------|-------------|-----------|
| `VERIFIED_SUCCESS` | +1 | +1 | Agent completed task successfully |
| `VERIFIED_FAILURE` | 0 | +1 | Agent failed task; penalises score |
| `VERIFIED_TIMEOUT` | 0 | 0 | **Neutral**; SLA breach, not malice |
| `VERIFIED_CANCELLATION` | 0 | 0 | **Neutral**; client decision, not agent fault |

### 4.4 Coefficients (All Explicit)

```
SCORE_SCALE         = 10,000   # Integer scale factor
SCORE_MIN           = 0        # Minimum score_scaled
SCORE_MAX           = 10,000   # Maximum score_scaled
COLD_START_SCORE    = 5,000    # When N == 0
SUCCESS_WEIGHT      = 1
FAILURE_WEIGHT      = 1
TIMEOUT_WEIGHT      = 0        # Neutral
CANCELLATION_WEIGHT = 0        # Neutral
```

### 4.5 Score Formula

```
Let:
  S = verified_successes
  F = verified_failures
  T = verified_timeouts    (informational only; excluded from ratio)
  C = verified_cancellations  (informational only; excluded from ratio)
  N = S + F  (active denominator; attributable to agent performance)

If N == 0:
  score_scaled = COLD_START_SCORE = 5,000
  (Neutral for agents without success/failure history)
  experience_count = S + F + T + C

If N > 0:
  numerator_scaled = S × SUCCESS_WEIGHT × SCORE_SCALE
  raw_score_scaled = round(numerator_scaled / N)
  score_scaled = max(SCORE_MIN, min(SCORE_MAX, raw_score_scaled))
  experience_count = S + F + T + C
```

### 4.6 Score Interpretation

| score_scaled | Normalized | Interpretation |
|-------------|------------|----------------|
| 10,000 | 1.0000 | Perfect (all verified successes) |
| 8,000 | 0.8000 | 80% success rate |
| 5,000 | 0.5000 | 50% success rate OR cold start |
| 2,500 | 0.2500 | 25% success rate |
| 0 | 0.0000 | All verified failures |

### 4.7 Cold-Start Transparency

Agents with no verified successes or failures receive `score_scaled = 5000` (neutral).

**Important:** The `experience_count` field exposes the total number of canonical verified executions separately from the score. This means:
- A brand-new agent with 0 executions has `experience_count=0`
- An established agent with 1000 timeout-only executions has `experience_count=1000`
- Both receive `score_scaled=5000` but the `experience_count` distinguishes them

### 4.8 Determinism Guarantees

1. **Same evidence set → same score.** Proven by sorting events by ID before hashing.
2. **Event ordering does not affect score.** S, F, T, C counts are commutative.
3. **Duplicate events cannot inflate score.** evidence_set_hash uses set-based deduplication.
4. **Integer arithmetic.** No floating-point operations in score calculation.
5. **Clamped.** score_scaled is always in [0, 10000].

### 4.9 Evidence Set Hash

Every score records an `evidence_set_hash` (SHA-256) that uniquely identifies the exact evidence that produced it.

**Canonicalization algorithm:**
1. Extract all event IDs as lowercase strings
2. Sort event IDs lexicographically
3. For each sorted event ID: `"{event_id}:{outcome_type}"`
4. Join with `"\n"`
5. Encode as UTF-8, compute SHA-256
6. Return lowercase 64-character hex digest

**Same `evidence_set_hash` always produces the same `score_scaled`.**

---

## 5. Database Schema

### 5.1 `reputation_policy_versions`

Immutable versioned policy registry.

| Column | Type | Description |
|--------|------|-------------|
| `id` | UUID | Primary key |
| `policy_name` | VARCHAR(64) | e.g. `ReputationPolicyV1` |
| `version_number` | INTEGER | Integer version; monotonically increasing |
| `description` | TEXT | Human-readable description |
| `formula_json` | JSONB | Complete formula specification |
| `is_active` | BOOLEAN | True if used for new calculations |
| `activated_at` | TIMESTAMPTZ | When activated |
| `deprecated_at` | TIMESTAMPTZ | When deprecated (rows remain) |

**Invariant:** Rows are insert-only. Existing rows must never be updated.

### 5.2 `reputation_scores`

Current active score snapshot per agent × policy.

| Column | Type | Description |
|--------|------|-------------|
| `id` | UUID | Primary key |
| `agent_id` | UUID | FK to agents |
| `chain_id` | BIGINT | Blockchain chain ID |
| `policy_version` | VARCHAR(64) | e.g. `ReputationPolicyV1` |
| `score_scaled` | INTEGER | In `[0, 10000]`; divide by 10000 for float |
| `evidence_set_hash` | VARCHAR(64) | SHA-256 of canonical evidence set |
| `evidence_event_count` | INTEGER | Number of canonical events in evidence set |
| `calculation_reason` | VARCHAR(64) | Why calculated |
| `explanation_json` | JSONB | Full auditable explanation |

**Check constraint:** `score_scaled >= 0 AND score_scaled <= 10000`

**Uniqueness:** One row per `(agent_id, chain_id, policy_version)`.

### 5.3 `reputation_score_history`

Immutable append-only log of every calculation event.

| Column | Type | Description |
|--------|------|-------------|
| `id` | UUID | Primary key |
| `reputation_score_id` | UUID | FK to current score record |
| `previous_score_scaled` | INTEGER | Previous score (NULL on first calculation) |
| `new_score_scaled` | INTEGER | New score |
| `calculation_reason` | VARCHAR(64) | Why recalculated |
| `explanation_json` | JSONB | Full explanation at time of calculation |

**Invariant:** Rows are append-only. Existing rows must never be updated or deleted.

---

## 6. Calculation Reasons

| Reason | Trigger |
|--------|---------|
| `INITIAL_CALCULATION` | First score for this agent × policy |
| `NEW_VERIFIED_EVENT` | New canonical confirmed reputation event added |
| `REORG_RECALCULATION` | Blockchain reorg invalidated an evidence event |
| `POLICY_VERSION_CHANGE` | New policy version applied to same evidence |
| `MANUAL_RECALCULATION` | Admin-triggered recalculation (still deterministic) |

---

## 7. API Endpoints

### Phase 6.5 Scoring API

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/v1/agents/{id}/reputation/score` | Current deterministic score |
| GET | `/api/v1/agents/{id}/reputation/metrics` | Objective evidence metrics |
| GET | `/api/v1/agents/{id}/reputation/history` | Immutable score history |
| GET | `/api/v1/agents/{id}/reputation/explanation` | Full auditable explanation |
| GET | `/api/v1/agents/{id}/reputation/policy` | Active policy version + formula |
| GET | `/api/v1/agents/{id}/reputation/evidence` | Evidence set hash + count |
| POST | `/api/v1/agents/{id}/reputation/recalculate` | Admin-only recalculation |

### Security

- All scoring endpoints are **read-only** (authenticated users).
- The recalculation endpoint requires **ADMIN** role.
- **Admins cannot set a score directly.** The endpoint triggers deterministic recomputation.
- Every recalculation is **audit-logged** with actor, timestamp, and result.

---

## 8. Separation of Concerns

```
EVIDENCE LAYER (Phase 6.4)          SCORING LAYER (Phase 6.5)
══════════════════════════          ═════════════════════════
ReputationRegistry.sol              ReputationPolicyVersion (DB)
  (on-chain anchor)                   (immutable policy registry)

ReputationEvent (DB)                ReputationScore (DB)
  (what happened)                     (what it means)

ReputationProfile (DB)              ReputationScoreHistory (DB)
  (aggregated counts)                 (immutable calculation log)

ReputationProjector                 ReputationScoringService
  (reads blockchain)                  (reads Phase 6.4 evidence)

ReputationService                   ReputationPolicyV1
  (creates evidence)                  (computes score from evidence)
```

**Critical invariant:** ReputationPolicyV1 reads evidence. It never writes to Phase 6.4 tables. ReputationService never writes to Phase 6.5 tables.

---

## 9. Manipulation Resistance

1. **Only canonical confirmed events included** (32-block confirmation policy).
2. **Duplicate execution IDs cannot double-count** (set-based deduplication in evidence_set_hash).
3. **VERIFIED_CANCELLATION is not penalized.** Cancellation = neutral client decision.
4. **VERIFIED_TIMEOUT is not penalized.** Timeout = SLA breach, not malice.
5. **Score cannot be manually set.** Admin endpoint always recomputes from evidence.
6. **Reorg handling.** When blockchain reorgs invalidate evidence, the score is automatically recalculated from the post-reorg canonical evidence set.

---

## 10. Observability

### Prometheus Metrics

| Metric | Type | Description |
|--------|------|-------------|
| `reputation_score_calculation_total{reason_code}` | Counter | Score calculations by reason |
| `reputation_score_calculation_failed_total` | Counter | Failed calculations |
| `reputation_score_recalculation_total` | Counter | Admin recalculations |
| `reputation_score_reorg_recalculation_total` | Counter | Reorg-triggered recalculations |
| `reputation_score_calculation_duration_seconds` | Histogram | Calculation latency |

---

## 11. Hard Stops

The following features are explicitly **excluded** from Phase 6.5 and any subsequent phase without separate architectural review:

- Staking or collateral-based reputation modification
- DAO governance over score parameters
- Reputation tokens or NFTs
- Dynamic decay functions
- On-chain numerical scores
- Any subjective star ratings or user reviews

**Phase 6.5 ends here.**
