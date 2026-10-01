"""ReputationPolicy V1 — Deterministic Scoring Formula.

POLICY NAME: ReputationPolicyV1
POLICY VERSION: 1
ACTIVATION DATE: 2026-09-28

============================================================
FORMULA SPECIFICATION (Complete, human-readable)
============================================================

Input: The canonical ordered set of CONFIRMED, CANONICAL reputation events
       for a given agent on a given chain.

Outcome classification mapping:
  - VERIFIED_SUCCESS    → contributes positively to score
  - VERIFIED_FAILURE    → contributes negatively to score
  - VERIFIED_TIMEOUT    → contributes neutrally (SLA breach, not malicious)
  - VERIFIED_CANCELLATION → contributes neutrally (client decision, not agent fault)

Coefficients (all are non-negative integers stored in formula_json):
  SUCCESS_WEIGHT    = 1    (each success contributes 1 unit to numerator)
  FAILURE_WEIGHT    = 1    (each failure contributes 1 unit to denominator only)
  TIMEOUT_WEIGHT    = 0    (no contribution to score numerator or denominator penalty)
  CANCELLATION_WEIGHT = 0  (no contribution to score numerator or denominator penalty)

Score formula (integer arithmetic):
  Let:
    S = verified_successes
    F = verified_failures
    T = verified_timeouts    (informational only; excluded from ratio)
    C = verified_cancellations  (informational only; excluded from ratio)
    N = S + F  (active denominator; only outcomes attributable to agent performance)

  If N == 0:
    score_scaled = COLD_START_SCORE = 5000  (neutral; 0.5 on [0, 1] scale)
    experience_count = S + F + T + C
    confidence = "COLD_START"

  If N > 0:
    raw_score_scaled = round((S * SUCCESS_WEIGHT * SCORE_SCALE) / N)
    score_scaled = max(SCORE_MIN=0, min(SCORE_MAX=10000, raw_score_scaled))
    experience_count = S + F + T + C  (total canonical confirmed executions)

Bounded:
  score_scaled ∈ [0, 10000]
  Divide by 10000 → normalized score ∈ [0.0000, 1.0000]

Rounding:
  Python integer division: round(x * SCORE_SCALE // N) — round-half-to-even
  Specifically: round(numerator / denominator) where both are integers.
  Same result on any compliant Python 3 implementation.
  No floating-point intermediate values.

Cold-start behavior:
  - Agents with N == 0 (no failures or successes yet) receive COLD_START_SCORE=5000.
  - experience_count reflects total canonical verified executions (includes
    VERIFIED_TIMEOUT and VERIFIED_CANCELLATION which are neutral).
  - A separate field `experience_count` is exposed in metrics and explanation
    so callers can distinguish a new agent (experience=0) from an established
    one (experience=1000) that both happen to have score_scaled=5000.

Determinism guarantees:
  1. Same canonical event set → same score. Proven by sorting events by ID.
  2. Event ordering does not affect score. S, F, T, C counts are commutative.
  3. Duplicate events cannot inflate score. evidence_set_hash uses a SET.
  4. Integer arithmetic: no floating-point operations in score calculation.
  5. score_scaled is clamped to [SCORE_MIN, SCORE_MAX] before storage.

Manipulation resistance:
  - VERIFIED_CANCELLATION is NOT penalized. Cancellation = neutral.
  - VERIFIED_TIMEOUT is NOT penalized. Timeout = SLA breach, not malice.
  - Only confirmed canonical events are included (32-block policy).
  - Duplicate execution IDs cannot double-count (set-based deduplication).
  - Score cannot be manually set; it is always derived from evidence.

============================================================
OUTCOME TREATMENT TABLE (complete)
============================================================

| Outcome                | Numerator contrib | Denominator contrib | Notes                  |
|------------------------|-------------------|---------------------|------------------------|
| VERIFIED_SUCCESS       | +SUCCESS_WEIGHT   | +SUCCESS_WEIGHT     | Improves score         |
| VERIFIED_FAILURE       | 0                 | +FAILURE_WEIGHT     | Reduces score          |
| VERIFIED_TIMEOUT       | 0                 | 0                   | Neutral; informational |
| VERIFIED_CANCELLATION  | 0                 | 0                   | Neutral; informational |

============================================================
COEFFICIENT TABLE (all explicit; none hidden)
============================================================

  SCORE_SCALE        = 10000  # Integer scale factor
  SCORE_MIN          = 0      # Minimum score_scaled
  SCORE_MAX          = 10000  # Maximum score_scaled
  COLD_START_SCORE   = 5000   # score_scaled for agents with no failure/success yet
  SUCCESS_WEIGHT     = 1
  FAILURE_WEIGHT     = 1
  TIMEOUT_WEIGHT     = 0
  CANCELLATION_WEIGHT = 0

All coefficients are also serialized into formula_json for auditability.
"""

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


# ─────────────────────────── Policy Constants ────────────────────────────────

POLICY_NAME = "ReputationPolicy"
POLICY_VERSION_NUMBER = 1
POLICY_VERSION_STRING = f"{POLICY_NAME}V{POLICY_VERSION_NUMBER}"  # "ReputationPolicyV1"

# Integer arithmetic scale factor
SCORE_SCALE = 10_000
SCORE_MIN = 0
SCORE_MAX = 10_000
COLD_START_SCORE = 5_000

# Explicit coefficients (never hidden)
SUCCESS_WEIGHT = 1
FAILURE_WEIGHT = 1
TIMEOUT_WEIGHT = 0          # Neutral; informational only
CANCELLATION_WEIGHT = 0     # Neutral; informational only

# Outcome type constants (matches Phase 6.4 enum values)
OUTCOME_VERIFIED_SUCCESS = "VERIFIED_SUCCESS"
OUTCOME_VERIFIED_FAILURE = "VERIFIED_FAILURE"
OUTCOME_VERIFIED_TIMEOUT = "VERIFIED_TIMEOUT"
OUTCOME_VERIFIED_CANCELLATION = "VERIFIED_CANCELLATION"

FORMULA_JSON: dict[str, Any] = {
    "policy_name": POLICY_NAME,
    "policy_version": POLICY_VERSION_NUMBER,
    "description": (
        "Deterministic reputation score derived from canonical verified execution evidence. "
        "Score = round(verified_successes * SUCCESS_WEIGHT * SCORE_SCALE / "
        "(verified_successes + verified_failures)). "
        "VERIFIED_TIMEOUT and VERIFIED_CANCELLATION are neutral (weight=0)."
    ),
    "score_scale": SCORE_SCALE,
    "score_min": SCORE_MIN,
    "score_max": SCORE_MAX,
    "cold_start_score": COLD_START_SCORE,
    "coefficients": {
        "SUCCESS_WEIGHT": SUCCESS_WEIGHT,
        "FAILURE_WEIGHT": FAILURE_WEIGHT,
        "TIMEOUT_WEIGHT": TIMEOUT_WEIGHT,
        "CANCELLATION_WEIGHT": CANCELLATION_WEIGHT,
    },
    "formula": (
        "N = verified_successes + verified_failures; "
        "if N == 0: score_scaled = COLD_START_SCORE; "
        "else: score_scaled = round(verified_successes * SUCCESS_WEIGHT * SCORE_SCALE / N); "
        "score_scaled = max(SCORE_MIN, min(SCORE_MAX, score_scaled))"
    ),
    "outcome_treatment": {
        OUTCOME_VERIFIED_SUCCESS: "Numerator +1, Denominator +1 → improves score",
        OUTCOME_VERIFIED_FAILURE: "Numerator +0, Denominator +1 → reduces score",
        OUTCOME_VERIFIED_TIMEOUT: "Neutral; excluded from numerator and denominator",
        OUTCOME_VERIFIED_CANCELLATION: "Neutral; excluded from numerator and denominator",
    },
    "rounding": "Python round() applied to exact integer division; round-half-to-even",
    "arithmetic": "Pure integer arithmetic; no floating-point intermediate values",
    "manipulation_resistance": [
        "Only canonical confirmed events (32-block policy) included",
        "Duplicate execution_ids deduplicated via set-based evidence_set_hash",
        "VERIFIED_CANCELLATION is neutral (not penalized)",
        "VERIFIED_TIMEOUT is neutral (not penalized)",
        "Score cannot be manually set; always derived from evidence",
    ],
}


# ─────────────────────────── Data Structures ─────────────────────────────────


@dataclass(frozen=True)
class EvidenceEvent:
    """Immutable representation of a single canonical evidence event."""
    event_id: str           # UUID as string (lowercase)
    outcome_type: str       # e.g. "VERIFIED_SUCCESS"
    confirmed_at: str       # ISO-8601 UTC timestamp (for deterministic ordering)


@dataclass
class ScoreMetrics:
    """Objective metrics exposed separately from the final score."""
    total_verified_executions: int = 0
    verified_successes: int = 0
    verified_failures: int = 0
    verified_timeouts: int = 0
    verified_cancellations: int = 0

    # Integer rates in [0, 10000]; None when total==0
    success_rate_scaled: int | None = None
    failure_rate_scaled: int | None = None
    timeout_rate_scaled: int | None = None
    cancellation_rate_scaled: int | None = None

    # Raw rates as float (for display; not used in score calculation)
    success_rate_float: float | None = None
    failure_rate_float: float | None = None
    timeout_rate_float: float | None = None
    cancellation_rate_float: float | None = None


@dataclass
class ScoreExplanation:
    """Full score explanation for audit and independent reproduction."""
    policy_name: str
    policy_version: int
    policy_version_string: str
    calculated_at: str                # ISO-8601 UTC
    evidence_set_hash: str            # SHA-256 hex of canonical evidence set
    evidence_event_count: int         # Number of events in evidence set
    # Intermediate values
    verified_successes: int
    verified_failures: int
    verified_timeouts: int
    verified_cancellations: int
    total_verified_executions: int
    active_denominator: int           # N = successes + failures
    numerator_scaled: int             # successes * SUCCESS_WEIGHT * SCORE_SCALE
    # Coefficients used
    success_weight: int = SUCCESS_WEIGHT
    failure_weight: int = FAILURE_WEIGHT
    timeout_weight: int = TIMEOUT_WEIGHT
    cancellation_weight: int = CANCELLATION_WEIGHT
    score_scale: int = SCORE_SCALE
    cold_start_score: int = COLD_START_SCORE
    # Result
    is_cold_start: bool = False
    raw_score_scaled: int = 0
    clamped: bool = False
    final_score_scaled: int = 0
    score_min: int = SCORE_MIN
    score_max: int = SCORE_MAX
    experience_count: int = 0
    # Rates (integer)
    success_rate_scaled: int | None = None
    failure_rate_scaled: int | None = None
    timeout_rate_scaled: int | None = None
    cancellation_rate_scaled: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "policy_name": self.policy_name,
            "policy_version": self.policy_version,
            "policy_version_string": self.policy_version_string,
            "calculated_at": self.calculated_at,
            "evidence_set_hash": self.evidence_set_hash,
            "evidence_event_count": self.evidence_event_count,
            "input_counts": {
                "verified_successes": self.verified_successes,
                "verified_failures": self.verified_failures,
                "verified_timeouts": self.verified_timeouts,
                "verified_cancellations": self.verified_cancellations,
                "total_verified_executions": self.total_verified_executions,
            },
            "formula_inputs": {
                "active_denominator_N": self.active_denominator,
                "numerator_scaled": self.numerator_scaled,
                "success_weight": self.success_weight,
                "failure_weight": self.failure_weight,
                "timeout_weight": self.timeout_weight,
                "cancellation_weight": self.cancellation_weight,
                "score_scale": self.score_scale,
                "cold_start_score": self.cold_start_score,
            },
            "cold_start": {
                "is_cold_start": self.is_cold_start,
                "experience_count": self.experience_count,
            },
            "normalization": {
                "raw_score_scaled": self.raw_score_scaled,
                "clamped": self.clamped,
                "score_min": self.score_min,
                "score_max": self.score_max,
            },
            "result": {
                "final_score_scaled": self.final_score_scaled,
                "final_score_float": self.final_score_scaled / SCORE_SCALE,
            },
            "rates_scaled": {
                "success_rate_scaled": self.success_rate_scaled,
                "failure_rate_scaled": self.failure_rate_scaled,
                "timeout_rate_scaled": self.timeout_rate_scaled,
                "cancellation_rate_scaled": self.cancellation_rate_scaled,
            },
        }


# ─────────────────────────── Evidence Set Hashing ────────────────────────────


def compute_evidence_set_hash(events: list[EvidenceEvent]) -> str:
    """Compute a deterministic SHA-256 hash of the canonical evidence set.

    Canonicalization algorithm (documented for independent reproduction):
    1. Extract all event IDs as lowercase strings.
    2. Sort event IDs lexicographically (UUID strings sort deterministically).
    3. For each sorted event ID, concatenate: "{event_id}:{outcome_type}".
    4. Join all entries with newline ("\\n").
    5. Encode as UTF-8 and compute SHA-256.
    6. Return lowercase hex digest (64 characters).

    Properties:
    - Same evidence set → same hash regardless of original order.
    - Duplicate event IDs are deduplicated before hashing.
    - Adding a new event changes the hash deterministically.
    - Empty evidence set → well-defined hash of empty string.

    Args:
        events: List of EvidenceEvent objects (may be unordered, may have duplicates).

    Returns:
        64-character lowercase hex SHA-256 digest.
    """
    # Deduplicate by event_id (set-based)
    seen_ids: set[str] = set()
    unique_events: list[EvidenceEvent] = []
    for ev in events:
        ev_id = ev.event_id.lower()
        if ev_id not in seen_ids:
            seen_ids.add(ev_id)
            unique_events.append(ev)

    # Sort by event_id lexicographically for order independence
    sorted_events = sorted(unique_events, key=lambda e: e.event_id.lower())

    # Canonicalize
    canonical_lines = [f"{ev.event_id.lower()}:{ev.outcome_type}" for ev in sorted_events]
    canonical_string = "\n".join(canonical_lines)

    return hashlib.sha256(canonical_string.encode("utf-8")).hexdigest().lower()


def _rate_scaled(count: int, total: int) -> int | None:
    """Compute integer rate = round(count * SCORE_SCALE / total).

    Returns None if total == 0 (undefined; cold start).
    Pure integer arithmetic; no floating-point.
    """
    if total <= 0:
        return None
    # Use Python's exact integer arithmetic
    return round(count * SCORE_SCALE / total)


# ─────────────────────────── ReputationPolicyV1 ──────────────────────────────


class ReputationPolicyV1:
    """Deterministic, versioned reputation scoring policy.

    This class implements ReputationPolicy V1.

    Usage:
        policy = ReputationPolicyV1()
        score_scaled, explanation = policy.calculate_score(events)
        metrics = policy.calculate_metrics(events)
        expl_dict = policy.explain_score(events)

    All methods are pure functions of their inputs.
    Given the same evidence set, the same score is always returned.
    Event ordering does not affect the result.
    Duplicate events are deduplicated automatically.
    """

    POLICY_NAME = POLICY_NAME
    POLICY_VERSION = POLICY_VERSION_NUMBER
    POLICY_VERSION_STRING = POLICY_VERSION_STRING
    FORMULA_JSON = FORMULA_JSON

    def calculate_score(
        self,
        events: list[EvidenceEvent],
    ) -> tuple[int, ScoreExplanation]:
        """Calculate deterministic score_scaled for the given evidence set.

        Args:
            events: List of EvidenceEvent. May be unordered. Duplicates ignored.

        Returns:
            (score_scaled: int, explanation: ScoreExplanation)
            score_scaled is in [SCORE_MIN, SCORE_MAX] = [0, 10000].

        Raises:
            ValueError: If evidence contains an unrecognised outcome_type.
        """
        now_str = datetime.now(timezone.utc).isoformat()
        evidence_set_hash = compute_evidence_set_hash(events)
        evidence_event_count = len({ev.event_id.lower() for ev in events})  # deduplicated count

        # Count outcomes (deduplicated)
        seen_ids: set[str] = set()
        successes = 0
        failures = 0
        timeouts = 0
        cancellations = 0

        for ev in events:
            ev_id = ev.event_id.lower()
            if ev_id in seen_ids:
                continue  # deduplicate
            seen_ids.add(ev_id)

            ot = ev.outcome_type
            if ot == OUTCOME_VERIFIED_SUCCESS:
                successes += 1
            elif ot == OUTCOME_VERIFIED_FAILURE:
                failures += 1
            elif ot == OUTCOME_VERIFIED_TIMEOUT:
                timeouts += 1
            elif ot == OUTCOME_VERIFIED_CANCELLATION:
                cancellations += 1
            else:
                raise ValueError(f"Unknown outcome_type: {ot!r}")

        total = successes + failures + timeouts + cancellations
        experience_count = total

        # Active denominator: only outcomes attributable to agent performance
        N = successes * SUCCESS_WEIGHT + failures * FAILURE_WEIGHT

        # Rate calculations (integer, order-independent)
        success_rate_scaled = _rate_scaled(successes, total)
        failure_rate_scaled = _rate_scaled(failures, total)
        timeout_rate_scaled = _rate_scaled(timeouts, total)
        cancellation_rate_scaled = _rate_scaled(cancellations, total)

        # Score calculation
        is_cold_start = N == 0
        if is_cold_start:
            # Cold start: no failures or successes to ratio from
            raw_score_scaled = COLD_START_SCORE
            numerator_scaled = 0
        else:
            numerator_scaled = successes * SUCCESS_WEIGHT * SCORE_SCALE
            # Pure integer arithmetic; round() is deterministic in Python 3
            raw_score_scaled = round(numerator_scaled / N)

        # Clamp to bounds
        clamped = not (SCORE_MIN <= raw_score_scaled <= SCORE_MAX)
        final_score_scaled = max(SCORE_MIN, min(SCORE_MAX, raw_score_scaled))

        explanation = ScoreExplanation(
            policy_name=self.POLICY_NAME,
            policy_version=self.POLICY_VERSION,
            policy_version_string=self.POLICY_VERSION_STRING,
            calculated_at=now_str,
            evidence_set_hash=evidence_set_hash,
            evidence_event_count=evidence_event_count,
            verified_successes=successes,
            verified_failures=failures,
            verified_timeouts=timeouts,
            verified_cancellations=cancellations,
            total_verified_executions=total,
            active_denominator=N,
            numerator_scaled=numerator_scaled,
            success_weight=SUCCESS_WEIGHT,
            failure_weight=FAILURE_WEIGHT,
            timeout_weight=TIMEOUT_WEIGHT,
            cancellation_weight=CANCELLATION_WEIGHT,
            score_scale=SCORE_SCALE,
            cold_start_score=COLD_START_SCORE,
            is_cold_start=is_cold_start,
            raw_score_scaled=raw_score_scaled,
            clamped=clamped,
            final_score_scaled=final_score_scaled,
            score_min=SCORE_MIN,
            score_max=SCORE_MAX,
            experience_count=experience_count,
            success_rate_scaled=success_rate_scaled,
            failure_rate_scaled=failure_rate_scaled,
            timeout_rate_scaled=timeout_rate_scaled,
            cancellation_rate_scaled=cancellation_rate_scaled,
        )

        return final_score_scaled, explanation

    def calculate_metrics(self, events: list[EvidenceEvent]) -> ScoreMetrics:
        """Calculate objective metrics from evidence set.

        Returns ScoreMetrics with counts and rates.
        Rates are None when total_verified_executions == 0.
        """
        _, explanation = self.calculate_score(events)
        return ScoreMetrics(
            total_verified_executions=explanation.total_verified_executions,
            verified_successes=explanation.verified_successes,
            verified_failures=explanation.verified_failures,
            verified_timeouts=explanation.verified_timeouts,
            verified_cancellations=explanation.verified_cancellations,
            success_rate_scaled=explanation.success_rate_scaled,
            failure_rate_scaled=explanation.failure_rate_scaled,
            timeout_rate_scaled=explanation.timeout_rate_scaled,
            cancellation_rate_scaled=explanation.cancellation_rate_scaled,
            success_rate_float=(
                explanation.success_rate_scaled / SCORE_SCALE
                if explanation.success_rate_scaled is not None else None
            ),
            failure_rate_float=(
                explanation.failure_rate_scaled / SCORE_SCALE
                if explanation.failure_rate_scaled is not None else None
            ),
            timeout_rate_float=(
                explanation.timeout_rate_scaled / SCORE_SCALE
                if explanation.timeout_rate_scaled is not None else None
            ),
            cancellation_rate_float=(
                explanation.cancellation_rate_scaled / SCORE_SCALE
                if explanation.cancellation_rate_scaled is not None else None
            ),
        )

    def explain_score(self, events: list[EvidenceEvent]) -> dict[str, Any]:
        """Return a complete JSON-serializable explanation of the score.

        The explanation contains enough information for an auditor to
        independently reproduce the score without reading the source code.
        """
        _, explanation = self.calculate_score(events)
        return explanation.to_dict()

    def get_formula_spec(self) -> dict[str, Any]:
        """Return the complete formula specification."""
        return dict(self.FORMULA_JSON)


# Module-level singleton
_policy_v1 = ReputationPolicyV1()


def get_policy_v1() -> ReputationPolicyV1:
    """Return the module-level ReputationPolicyV1 singleton."""
    return _policy_v1
