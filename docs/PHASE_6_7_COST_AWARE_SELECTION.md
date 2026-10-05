# Phase 6.7 — Cost-Aware & Constraint-Aware Agent Selection Architecture

## 1. Overview & Architectural Principle

Phase 6.7 extends AgentChain's deterministic agent selection system to account for client-specified budget constraints, agent pricing, execution cost, supported capabilities, and runtime/tool requirements.

> **CRITICAL ARCHITECTURAL INVARIANT:**  
> **Price is an economic constraint and is not a reputation signal.**  
> Reputation is derived strictly from verified execution evidence (Phase 6.4 & 6.5).  
> Price is declared economic metadata associated with an agent version.  
> Under no circumstances does the system calculate an opaque composite score (e.g. `reputation / price` or `reputation + price`). Price serves strictly as a hard eligibility filter.

---

## 2. Pricing Model & Integer Representation

### 2.1 Currency & Atomic Units
- **Currency:** Official Circle USDC (`USDC`) on Base Sepolia (`0x036CbD53842c5426634e7929541eC2318f3dCF7e`) and Base Mainnet (`0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913`).
- **Precision:** 6 decimal places ($10^6$ factor).
- **Smallest Unit:** Integer atomic units ($1\text{ USDC} = 1{,}000{,}000\text{ atomic units}$).
- **No Floating-Point Arithmetic:** All comparisons, conversions, and storage use exact integer or Python `Decimal` with string representations. Binary floating-point arithmetic is strictly prohibited for monetary calculations.

### 2.2 Agent Version Pricing
Pricing is declared per agent version in `AgentVersion.pricing_config` (originating from `AgentManifest.pricing`):
```json
{
  "model": "per_execution",
  "amount": "1.50",
  "currency": "USDC",
  "price_atomic": 1500000
}
```
If `amount` is specified as a decimal string, it is parsed deterministically into integer atomic units. Amounts with more than 6 decimal places are rejected. If pricing is omitted or empty, price defaults to 0 (free tier).

---

## 3. Client Budget Semantics & Task Constraints

Clients specify constraints on `TaskDefinitionInput`:
- `max_budget_atomic: int | None` — Maximum budget in atomic units (e.g. `20000000` for 20.00 USDC).
- `max_budget: str | None` — Human-readable display units (e.g. `"20.00"`). Parsed automatically into `max_budget_atomic`.
- `budget_currency: str` — Defaults to `"USDC"`. Unsupported currencies are rejected at schema validation.
- `required_tools: list[str]` — Tools that the candidate agent must provide and have enabled.
- `max_timeout_seconds: int | None` — Upper bound on execution duration.

---

## 4. Selection Policy (`SelectionPolicyV1` + `EconomicConstraintPolicyV1`)

The deterministic selection engine executes the following steps:

```
TaskDefinitionInput
      ↓
Step 1: Hard Lifecycle & Capability Filter (PUBLISHED agents only)
      ↓
Step 2: Constraint Evaluation (Tools, Timeout, Currency, Budget)
      ↓
Step 3: Affordability Filter (Exclude candidates exceeding budget)
      ↓
Step 4: Canonical Reputation Lookup (CANONICAL, COLD_START, ABSENT)
      ↓
Step 5: Deterministic Ranking (score_scaled DESC, agent_id ASC)
      ↓
Step 6: Order-Normalised Canonical Hashing (SHA-256 over canonical JSON)
      ↓
Step 7: SelectionDecision Snapshot & Version Pinning
```

### Deterministic Tie-Breaking
When multiple affordable candidates have identical reputation scores:
- **Primary Rank:** `score_scaled DESC`
- **Tie-Breaker:** `agent_id ASC` (lexicographical string UUID)
- **Price is NOT a ranking tiebreaker:** If Agent A (price 15 USDC) and Agent B (price 5 USDC) both have score 8000 and budget is 20 USDC, the winner is determined strictly by `agent_id ASC`.

---

## 5. Selection Snapshot & Canonical Hashing

### 5.1 Snapshot Persistence (`SelectionDecision`)
`SelectionDecision` permanently captures:
- `selected_price_atomic`: Exact integer atomic units for the winning agent.
- `price_currency`: `"USDC"`.
- `max_budget_atomic`: Client budget constraint applied.
- `economic_policy_version`: `"EconomicConstraintPolicyV1"`.
- `eligible_candidates_json`: Complete diagnostic list of evaluated candidates including their price, budget compliance (`is_affordable`), tool/runtime compliance (`meets_constraints`), and exclusion reasons.

### 5.2 Canonical Hash Invariance & Tamper Resistance
The canonical payload:
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
- `candidates` is sorted strictly by `agent_id ASC`, making the hash invariant to database discovery order.
- Tampering with any economic field (price, budget, winner price) changes the hash.

---

## 6. TOCTOU & Escrow Consistency

1. **Task-Level Pinning:**
   `OrchestrationTask.price_atomic` and `SelectionDecision.selected_price_atomic` record the authoritative price at the moment of selection.
2. **Subsequent Price Updates Ignored:**
   If a developer updates the agent version's price or publishes a new version, historical decisions and tasks remain pinned to their selection snapshot.
3. **Escrow Guarantee:**
   Escrow creation parameters (`createEscrow` / `createAndFundEscrow`) use `task.price_atomic` directly. The escrow amount cannot diverge from the selected price.

---

## 7. Failure Semantics

- **No Candidates Match Capability:** Raises `ValueError("NO_ELIGIBLE_AGENT")`.
- **Candidates Match Capability but Exceed Budget:** Raises `ValueError("NO_AFFORDABLE_AGENT")`.
- **Explicit Agent Version Missing:** Raises `ValueError("Agent '{name}' does not have published version '{version}'")`.
- **Currency Mismatch:** Candidates with non-USDC pricing are excluded from selection.

---

## 8. Limitations & Future Scope

- **Fixed Fee Model:** Currently supports fixed per-execution pricing in USDC. Dynamic resource metering (per-second or per-token) will be addressed in future economic phases.
- **Single Settlement Currency:** Currently restricted to Base USDC. Multi-currency bridging is out of scope.
