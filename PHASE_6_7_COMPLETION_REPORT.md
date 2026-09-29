# Phase 6.7 Completion Report: Cost-Aware & Constraint-Aware Agent Selection

**Date:** 2026-09-29  
**Phase:** 6.7 — Cost-Aware & Constraint-Aware Agent Selection  
**Status:** ✅ RELEASE GATE PASSED  

---

## 1. Executive Summary

Phase 6.7 extends AgentChain's deterministic agent selection architecture to incorporate client-specified budget constraints, integer-safe agent pricing, tool requirements, and execution timeouts.

The system enforces a strict architectural boundary: **price is an economic constraint, not a reputation signal**. No composite "reputation + price" scoring is introduced. Price functions exclusively as a hard eligibility filter, and ranking among affordable candidates is governed strictly by verified reputation (`score_scaled DESC`) with deterministic tie-breaking (`agent_id ASC`).

---

## 2. Repository Audit Findings

1. **Existing Pricing Infrastructure:**
   - `AgentManifest.pricing` defines `PricingConfig` (`model="per_execution"`, `amount="1.00"`, `currency="USDC"`).
   - Stored in `agent_versions.pricing_config` (JSONB).
   - Reused cleanly without creating duplicate pricing models.
2. **Monetary Precision & Conventions:**
   - USDC on Base (Sepolia 84532 & Mainnet 8453) utilizes 6 decimal places ($10^6$ factor).
   - Smallest unit is integer atomic units ($1\text{ USDC} = 1{,}000{,}000\text{ atomic units}$).
   - Floating-point arithmetic is strictly prohibited; calculations utilize Python `Decimal` and integer arithmetic.
3. **Escrow Integration:**
   - Smart contract `Escrow.sol` consumes integer `amount` via `createEscrow` / `createAndFundEscrow`.
   - Settlement revenue split remains 85% Developer, 10% Staker, 5% DAO.

---

## 3. Pricing Model & Budget Semantics

### 3.1 Precision-Safe Economic Module ([pricing.py](file:///Users/shravani/Desktop/AgentChain_AIWorkSpace/backend/app/services/pricing.py))
- `parse_usdc_display_to_atomic(val)`: Converts string/decimal (e.g. `"1.50"`) to integer atomic units (`1500000`). Rejects amounts with $>6$ decimal places, negative numbers, or invalid formats.
- `extract_version_price(version)`: Extracts atomic price and currency from `AgentVersion.pricing_config`. Defaults to 0 atomic units if unconfigured.
- `validate_budget_constraint(price_atomic, max_budget_atomic)`: Exact integer comparison (`price_atomic <= max_budget_atomic`).

### 3.2 Task Budget Constraints ([schemas/orchestration.py](file:///Users/shravani/Desktop/AgentChain_AIWorkSpace/backend/app/schemas/orchestration.py))
`TaskDefinitionInput` supports:
- `max_budget_atomic: int | None` — Budget in atomic units.
- `max_budget: str | None` — Human-readable display units (automatically parsed).
- `budget_currency: str` — Enforced to `"USDC"`.
- `required_tools: list[str]` — Required tools from the candidate agent.
- `max_timeout_seconds: int | None` — Maximum execution timeout.

---

## 4. Selection Policy (`SelectionPolicyV1` + `EconomicConstraintPolicyV1`)

### 4.1 Strict Price / Reputation Separation
$$\text{Price is an economic eligibility filter, NOT a ranking score.}$$

Under no circumstances is reputation diluted, penalized, or divided by price. The policy guarantees:
1. **Hard Eligibility & Budget Filter:** Candidates exceeding `max_budget_atomic` or missing required tools/timeouts are marked `meets_constraints=False` and excluded.
2. **Reputation Ranking:** Affordable candidates are sorted strictly by `score_scaled DESC`.
3. **Deterministic Tie-Breaker:** Equal reputation scores break ties on `agent_id ASC` (lexicographical string UUID). Price difference does NOT decide ties.

---

## 5. Database Schema & Migration

**Alembic Migration:** `018_cost_aware_selection.py`  
Applied to:
- `selection_decisions`:
  - `selected_price_atomic` (`NUMERIC(38, 0)`, nullable)
  - `price_currency` (`VARCHAR(16)`, not null, default `'USDC'`)
  - `max_budget_atomic` (`NUMERIC(38, 0)`, nullable)
  - `economic_policy_version` (`VARCHAR(64)`, not null, default `'EconomicConstraintPolicyV1'`)
- `orchestration_tasks`:
  - `price_atomic` (`NUMERIC(38, 0)`, nullable)
  - `price_currency` (`VARCHAR(16)`, not null, default `'USDC'`)

Migration was tested through `upgrade head -> downgrade -1 -> upgrade head` without errors.

---

## 6. Selection Snapshot & Canonical Hashing

### 6.1 Order-Normalised Canonical Hash
The canonical selection hash is computed over RFC-8785 canonical JSON:
```json
{
  "candidates": [
    {
      "agent_id": "<uuid>",
      "agent_name": "<name>",
      "evidence_hash": "<sha256|null>",
      "exclusion_reason": "<reason|null>",
      "is_affordable": true,
      "meets_constraints": true,
      "policy_version": "ReputationPolicyV1",
      "price_atomic": 1500000,
      "price_currency": "USDC",
      "reputation_state": "CANONICAL",
      "score_scaled": 8000,
      "total_verified_executions": 10
    }
  ],
  "constraints": {
    "agent_id": null,
    "agent_version": null,
    "budget_currency": "USDC",
    "capability": "research",
    "max_budget_atomic": 20000000,
    "max_timeout_seconds": null,
    "required_tools": [],
    "task_key": "t_research_01"
  },
  "economic_policy": "EconomicConstraintPolicyV1",
  "policy": "SelectionPolicyV1",
  "selected_agent_id": "<uuid>",
  "selected_price_atomic": 1500000,
  "selected_version": "1.0.0"
}
```
- Shuffling candidate discovery order produces identical canonical hashes.
- Tampering with `selected_price_atomic`, candidate prices, or budget immediately invalidates the hash.

---

## 7. TOCTOU & Escrow Consistency

1. **Pinned Price in Task & Decision:**
   Both `SelectionDecision.selected_price_atomic` and `OrchestrationTask.price_atomic` lock the exact price at selection time.
2. **Subsequent Mutation Immunity:**
   Post-selection changes to agent pricing or version manifests do not alter historical decisions or execution terms.
3. **Escrow Consistency:**
   Escrow creation parameters use `task.price_atomic`, preventing selection-to-escrow price divergence.

---

## 8. Comprehensive Test Results

### 8.1 Targeted Phase 6.7 Test Suite (`test_cost_aware_selection.py`)
All 17 tests passed:
- `test_pricing_utilities_precision`: Precision safety & rejection of $>6$ decimals.
- `test_budget_filter_price_below_budget`: Affordable candidate accepted.
- `test_budget_filter_price_equals_budget`: Exact boundary condition accepted.
- `test_budget_filter_price_exceeds_budget_rejected`: Over-budget raises `NO_AFFORDABLE_AGENT`.
- `test_budget_filter_zero_budget`: Zero budget filters to free agents.
- `test_unconstrained_budget_selects_all`: `budget=None` allows any price.
- `test_over_budget_higher_reputation_excluded`: Over-budget higher score agent rejected; affordable lower score agent selected.
- `test_deterministic_tie_break_with_different_prices`: Equal score agents tie-break on `agent_id ASC`.
- `test_tool_requirement_filter`: Missing required tools excluded.
- `test_runtime_timeout_filter`: Excessive timeout excluded.
- `test_price_mutation_after_selection_does_not_mutate_decision`: Snapshot immutability verified.
- `test_pinned_price_in_orchestration_task`: Task pins selected price.
- `test_escrow_amount_consistency`: Selected price matches escrow parameter.
- `test_canonical_hash_detects_price_tampering`: Tampered price changes hash.
- `test_property_discovery_order_independence`: Shuffled candidate iterations yield identical selection and hash.
- `test_concurrent_cost_aware_selection`: Concurrent selections remain consistent.
- `test_selection_performance_benchmark`: Candidate pool latency $<50\text{ms}$.

### 8.2 Selection Suite Regression (`test_agent_selection.py`)
All 25 existing selection integrity tests passed with 100% backwards compatibility.

---

## 9. System Regression Audit

| Component | Command | Result | Status |
|---|---|---|---|
| Backend Test Suite | `pytest backend/tests/ -q` | 452 passed, 0 failed, 392 warnings in 68.78s | **PASS** |
| Indexer & Agent SDK | `pytest services/indexer/tests packages/agent-sdk/tests` | 8 passed, 0 failed in 0.52s | **PASS** |
| Smart Contracts (Foundry) | `forge test` | 115 passed, 0 failed (128,000 invariant calls) | **PASS** |
| Authoritative Config Drift | `python scripts/check_config_drift.py` | 0 drift detected; all invariants hold | **PASS** |
| Web Application | `npm run typecheck && npm run lint` | 0 errors | **PASS** |

---

## 10. Evidence Classification

| Item | Classification | Verification Method |
|---|---|---|
| Monetary Precision | **TESTED** | Unit tested against decimal boundaries and precision rules |
| Budget Constraints | **TESTED** | Filter tests with varying price/budget ratios |
| Immutability Triggers | **TESTED** | Database flush and reload testing after price mutation |
| TOCTOU Protection | **TESTED** | Task execution against pinned price snapshot |
| Concurrency | **TESTED** | `asyncio.gather` concurrent selection execution |
| Canonical Hashing | **TESTED** | Order-independence property and tamper verification tests |
| Smart Contracts | **TESTED** | 115 Foundry tests on local EVM |
| Config Drift | **TESTED** | Automated scanner verifying zero drift |
| Production Key Security | **DOCUMENTED** | Mainnet transactions blocked fail-closed |

---

## 11. Known Limitations & Out of Scope

- **Supported Token:** Restricted to USDC on Base. Other settlement tokens remain out of scope.
- **Pricing Dimension:** Fixed fee per execution. Usage-metered (per-token/per-second) pricing is deferred to future economic enhancements.
- **Out of Scope:** Staking, DAO voting, dynamic reputation decay, Mainnet deployment.

---

## 12. Final Release-Gate Verdict

All criteria for Phase 6.7 are met:
1. Pricing semantics are explicit and precision-safe (USDC 6-decimal atomic units).
2. Budget filtering is strictly enforced before ranking.
3. No binary floating-point monetary operations exist.
4. Price is strictly an economic constraint, never conflated with reputation.
5. Historical selection decisions and tasks remain immutable and pinned.
6. Canonical selection hashes incorporate all economic inputs with order-normalization.
7. Full regression suites across backend, contracts, indexer, SDK, and frontend pass cleanly.

**PHASE 6.7 — RELEASE GATE PASSED**
