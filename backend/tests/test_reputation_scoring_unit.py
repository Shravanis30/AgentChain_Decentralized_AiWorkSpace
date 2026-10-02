"""Unit tests for Phase 6.5 — Deterministic Reputation Scoring Policy Engine.

Tests the ReputationPolicyV1 formula, edge cases, and invariants in full isolation.
No database or async context required.

Test coverage:
1. Cold-start behavior (N==0)
2. Pure success (all successes)
3. Pure failure (all failures)
4. Mixed outcomes with exact formula verification
5. VERIFIED_TIMEOUT and VERIFIED_CANCELLATION are neutral (excluded from ratio)
6. evidence_set_hash determinism (order independence, deduplication)
7. Integer arithmetic — no floating-point intermediate values
8. Bounds enforcement [0, 10000]
9. Unknown outcome_type raises ValueError
10. Explanation contains all required fields
11. Formula spec completeness
12. ScoreMetrics correctness
"""

import hashlib
import pytest

from app.services.reputation_policy.policy_v1 import (
    COLD_START_SCORE,
    OUTCOME_VERIFIED_CANCELLATION,
    OUTCOME_VERIFIED_FAILURE,
    OUTCOME_VERIFIED_SUCCESS,
    OUTCOME_VERIFIED_TIMEOUT,
    SCORE_MAX,
    SCORE_MIN,
    SCORE_SCALE,
    EvidenceEvent,
    ReputationPolicyV1,
    compute_evidence_set_hash,
    get_policy_v1,
    _rate_scaled,
)


def make_event(ev_id: str, outcome: str, confirmed_at: str = "2026-01-01T00:00:00Z") -> EvidenceEvent:
    return EvidenceEvent(event_id=ev_id, outcome_type=outcome, confirmed_at=confirmed_at)


SUCCESS = OUTCOME_VERIFIED_SUCCESS
FAILURE = OUTCOME_VERIFIED_FAILURE
TIMEOUT = OUTCOME_VERIFIED_TIMEOUT
CANCEL = OUTCOME_VERIFIED_CANCELLATION


@pytest.fixture
def policy() -> ReputationPolicyV1:
    return ReputationPolicyV1()


# ─────────────────────── Cold Start ───────────────────────────────────────────


class TestColdStart:
    def test_empty_evidence_cold_start(self, policy):
        score, expl = policy.calculate_score([])
        assert score == COLD_START_SCORE
        assert expl.is_cold_start is True
        assert expl.experience_count == 0

    def test_only_timeouts_cold_start(self, policy):
        events = [make_event("1", TIMEOUT), make_event("2", TIMEOUT)]
        score, expl = policy.calculate_score(events)
        assert score == COLD_START_SCORE
        assert expl.is_cold_start is True
        assert expl.experience_count == 2
        assert expl.verified_timeouts == 2
        assert expl.verified_successes == 0
        assert expl.verified_failures == 0

    def test_only_cancellations_cold_start(self, policy):
        events = [make_event("1", CANCEL)]
        score, expl = policy.calculate_score(events)
        assert score == COLD_START_SCORE
        assert expl.is_cold_start is True
        assert expl.experience_count == 1
        assert expl.verified_cancellations == 1

    def test_mixed_timeout_cancellation_cold_start(self, policy):
        events = [make_event("1", TIMEOUT), make_event("2", CANCEL)]
        score, expl = policy.calculate_score(events)
        assert score == COLD_START_SCORE
        assert expl.is_cold_start is True
        assert expl.experience_count == 2


# ─────────────────────── Pure Success / Failure ───────────────────────────────


class TestPureOutcomes:
    def test_all_successes_score_is_max(self, policy):
        events = [make_event(str(i), SUCCESS) for i in range(10)]
        score, expl = policy.calculate_score(events)
        assert score == SCORE_MAX  # 10000
        assert expl.is_cold_start is False
        assert expl.verified_successes == 10
        assert expl.verified_failures == 0

    def test_all_failures_score_is_zero(self, policy):
        events = [make_event(str(i), FAILURE) for i in range(10)]
        score, expl = policy.calculate_score(events)
        assert score == SCORE_MIN  # 0
        assert expl.is_cold_start is False
        assert expl.verified_failures == 10
        assert expl.verified_successes == 0

    def test_one_success_one_failure_score_is_5000(self, policy):
        events = [make_event("s1", SUCCESS), make_event("f1", FAILURE)]
        score, expl = policy.calculate_score(events)
        # N=2, successes=1; score=round(1*1*10000/2)=5000
        assert score == 5000
        assert expl.is_cold_start is False

    def test_single_success(self, policy):
        events = [make_event("s1", SUCCESS)]
        score, expl = policy.calculate_score(events)
        assert score == SCORE_MAX

    def test_single_failure(self, policy):
        events = [make_event("f1", FAILURE)]
        score, expl = policy.calculate_score(events)
        assert score == SCORE_MIN


# ─────────────────────── Mixed Outcomes ───────────────────────────────────────


class TestMixedOutcomes:
    def test_formula_3_success_1_failure(self, policy):
        events = [
            make_event("s1", SUCCESS),
            make_event("s2", SUCCESS),
            make_event("s3", SUCCESS),
            make_event("f1", FAILURE),
        ]
        score, expl = policy.calculate_score(events)
        # N=4 (3 success weight=1 + 1 failure weight=1)
        # numerator=3*1*10000=30000; score=round(30000/4)=7500
        expected = round(3 * 1 * SCORE_SCALE / 4)
        assert score == expected
        assert expl.verified_successes == 3
        assert expl.verified_failures == 1

    def test_formula_1_success_3_failures(self, policy):
        events = [
            make_event("s1", SUCCESS),
            make_event("f1", FAILURE),
            make_event("f2", FAILURE),
            make_event("f3", FAILURE),
        ]
        score, expl = policy.calculate_score(events)
        # N=4; score=round(1*10000/4)=2500
        expected = round(1 * SCORE_SCALE / 4)
        assert score == expected
        assert score == 2500

    def test_timeouts_are_neutral_in_ratio(self, policy):
        """Adding timeouts does not change the success/failure ratio."""
        events_no_timeout = [make_event("s1", SUCCESS), make_event("f1", FAILURE)]
        events_with_timeouts = [
            make_event("s1", SUCCESS),
            make_event("f1", FAILURE),
            make_event("t1", TIMEOUT),
            make_event("t2", TIMEOUT),
        ]
        score_no, _ = policy.calculate_score(events_no_timeout)
        score_with, expl = policy.calculate_score(events_with_timeouts)
        assert score_no == score_with
        assert expl.verified_timeouts == 2
        # Experience count should differ (includes timeouts)
        assert expl.experience_count == 4

    def test_cancellations_are_neutral_in_ratio(self, policy):
        """Adding cancellations does not change the success/failure ratio."""
        base_events = [make_event("s1", SUCCESS), make_event("f1", FAILURE)]
        extended_events = [
            make_event("s1", SUCCESS),
            make_event("f1", FAILURE),
            make_event("c1", CANCEL),
            make_event("c2", CANCEL),
        ]
        score_base, _ = policy.calculate_score(base_events)
        score_ext, expl = policy.calculate_score(extended_events)
        assert score_base == score_ext
        assert expl.verified_cancellations == 2

    def test_experience_count_includes_all_outcomes(self, policy):
        events = [
            make_event("s1", SUCCESS),
            make_event("f1", FAILURE),
            make_event("t1", TIMEOUT),
            make_event("c1", CANCEL),
        ]
        _, expl = policy.calculate_score(events)
        assert expl.experience_count == 4
        assert expl.total_verified_executions == 4


# ─────────────────────── Determinism ──────────────────────────────────────────


class TestDeterminism:
    def test_order_independence(self, policy):
        """Same outcomes in different order → same score."""
        events_a = [make_event("s1", SUCCESS), make_event("f1", FAILURE), make_event("t1", TIMEOUT)]
        events_b = [make_event("t1", TIMEOUT), make_event("s1", SUCCESS), make_event("f1", FAILURE)]
        events_c = [make_event("f1", FAILURE), make_event("t1", TIMEOUT), make_event("s1", SUCCESS)]
        scores = [policy.calculate_score(e)[0] for e in [events_a, events_b, events_c]]
        assert len(set(scores)) == 1

    def test_duplicate_events_deduplication(self, policy):
        """Duplicate event IDs cannot inflate the score."""
        events_unique = [make_event("s1", SUCCESS), make_event("f1", FAILURE)]
        events_with_dupes = [
            make_event("s1", SUCCESS),
            make_event("s1", SUCCESS),  # duplicate
            make_event("s1", SUCCESS),  # duplicate
            make_event("f1", FAILURE),
        ]
        score_unique, _ = policy.calculate_score(events_unique)
        score_dupes, expl = policy.calculate_score(events_with_dupes)
        assert score_unique == score_dupes
        # Deduplicated: only 2 unique events
        assert expl.verified_successes == 1
        assert expl.verified_failures == 1

    def test_same_evidence_same_hash(self, policy):
        """Same evidence always produces same hash, regardless of order."""
        events_a = [make_event("s1", SUCCESS), make_event("f1", FAILURE)]
        events_b = [make_event("f1", FAILURE), make_event("s1", SUCCESS)]
        hash_a = compute_evidence_set_hash(events_a)
        hash_b = compute_evidence_set_hash(events_b)
        assert hash_a == hash_b
        assert len(hash_a) == 64  # SHA-256 hex digest

    def test_different_evidence_different_hash(self, policy):
        events_a = [make_event("s1", SUCCESS)]
        events_b = [make_event("s2", SUCCESS)]
        hash_a = compute_evidence_set_hash(events_a)
        hash_b = compute_evidence_set_hash(events_b)
        assert hash_a != hash_b

    def test_empty_evidence_deterministic_hash(self):
        h = compute_evidence_set_hash([])
        expected = hashlib.sha256(b"").hexdigest().lower()
        assert h == expected
        assert len(h) == 64

    def test_adding_event_changes_hash(self):
        events_base = [make_event("s1", SUCCESS)]
        events_extended = [make_event("s1", SUCCESS), make_event("f1", FAILURE)]
        assert compute_evidence_set_hash(events_base) != compute_evidence_set_hash(events_extended)

    def test_score_reproducibility_from_explanation(self, policy):
        """Score can be reproduced from the explanation without running the policy."""
        events = [make_event("s1", SUCCESS), make_event("f1", FAILURE), make_event("t1", TIMEOUT)]
        score, expl = policy.calculate_score(events)

        # Re-derive from explanation fields
        S = expl.verified_successes
        F = expl.verified_failures
        N = S * 1 + F * 1  # SUCCESS_WEIGHT=1, FAILURE_WEIGHT=1
        if N == 0:
            expected = COLD_START_SCORE
        else:
            expected = max(SCORE_MIN, min(SCORE_MAX, round(S * 1 * SCORE_SCALE / N)))
        assert score == expected


# ─────────────────────── Bounds ───────────────────────────────────────────────


class TestBounds:
    def test_score_always_in_bounds(self, policy):
        test_cases = [
            [],
            [make_event("1", SUCCESS)],
            [make_event("1", FAILURE)],
            [make_event("1", SUCCESS), make_event("2", FAILURE)],
            [make_event(str(i), SUCCESS) for i in range(100)],
            [make_event(str(i), FAILURE) for i in range(100)],
        ]
        for case in test_cases:
            score, _ = policy.calculate_score(case)
            assert SCORE_MIN <= score <= SCORE_MAX

    def test_score_scale_is_10000(self):
        assert SCORE_SCALE == 10_000

    def test_cold_start_score_is_midpoint(self):
        assert COLD_START_SCORE == SCORE_SCALE // 2


# ─────────────────────── Explanation ──────────────────────────────────────────


class TestExplanation:
    def test_explanation_contains_required_fields(self, policy):
        events = [make_event("s1", SUCCESS)]
        _, expl = policy.calculate_score(events)
        d = expl.to_dict()
        required = [
            "policy_name", "policy_version", "policy_version_string",
            "calculated_at", "evidence_set_hash", "evidence_event_count",
            "input_counts", "formula_inputs", "cold_start", "normalization",
            "result", "rates_scaled",
        ]
        for key in required:
            assert key in d, f"Missing key in explanation: {key}"

    def test_explanation_formula_inputs(self, policy):
        events = [make_event("s1", SUCCESS), make_event("f1", FAILURE)]
        _, expl = policy.calculate_score(events)
        d = expl.to_dict()
        fi = d["formula_inputs"]
        assert fi["success_weight"] == 1
        assert fi["failure_weight"] == 1
        assert fi["timeout_weight"] == 0
        assert fi["cancellation_weight"] == 0
        assert fi["score_scale"] == 10000
        assert fi["cold_start_score"] == 5000

    def test_explain_score_dict_is_json_serializable(self, policy):
        import json
        events = [make_event("s1", SUCCESS), make_event("f1", FAILURE)]
        d = policy.explain_score(events)
        # Must not raise
        json.dumps(d)

    def test_explanation_result_matches_score(self, policy):
        events = [make_event("s1", SUCCESS), make_event("f1", FAILURE)]
        score, expl = policy.calculate_score(events)
        d = expl.to_dict()
        assert d["result"]["final_score_scaled"] == score


# ─────────────────────── Error Handling ───────────────────────────────────────


class TestErrorHandling:
    def test_unknown_outcome_type_raises_value_error(self, policy):
        events = [make_event("x1", "UNKNOWN_OUTCOME")]
        with pytest.raises(ValueError, match="Unknown outcome_type"):
            policy.calculate_score(events)


# ─────────────────────── Formula Spec ─────────────────────────────────────────


class TestFormulaSpec:
    def test_formula_json_complete(self, policy):
        spec = policy.get_formula_spec()
        required_keys = [
            "policy_name", "policy_version", "description",
            "score_scale", "score_min", "score_max", "cold_start_score",
            "coefficients", "formula", "outcome_treatment", "rounding", "arithmetic",
        ]
        for key in required_keys:
            assert key in spec, f"Missing key in formula_json: {key}"

    def test_formula_coefficients(self, policy):
        spec = policy.get_formula_spec()
        assert spec["coefficients"]["SUCCESS_WEIGHT"] == 1
        assert spec["coefficients"]["FAILURE_WEIGHT"] == 1
        assert spec["coefficients"]["TIMEOUT_WEIGHT"] == 0
        assert spec["coefficients"]["CANCELLATION_WEIGHT"] == 0

    def test_all_outcomes_documented_in_spec(self, policy):
        spec = policy.get_formula_spec()
        ot = spec["outcome_treatment"]
        assert OUTCOME_VERIFIED_SUCCESS in ot
        assert OUTCOME_VERIFIED_FAILURE in ot
        assert OUTCOME_VERIFIED_TIMEOUT in ot
        assert OUTCOME_VERIFIED_CANCELLATION in ot


# ─────────────────────── Rate Calculations ────────────────────────────────────


class TestRateCalculations:
    def test_success_rate_is_none_for_zero_total(self):
        assert _rate_scaled(0, 0) is None
        assert _rate_scaled(5, 0) is None

    def test_success_rate_100_percent(self):
        assert _rate_scaled(10, 10) == SCORE_SCALE  # 10000

    def test_success_rate_0_percent(self):
        assert _rate_scaled(0, 10) == 0

    def test_success_rate_50_percent(self):
        assert _rate_scaled(5, 10) == 5000

    def test_success_rate_75_percent(self):
        assert _rate_scaled(3, 4) == round(3 * SCORE_SCALE / 4)  # 7500

    def test_rates_in_metrics(self, policy):
        events = [make_event("s1", SUCCESS), make_event("f1", FAILURE), make_event("t1", TIMEOUT)]
        metrics = policy.calculate_metrics(events)
        # Total = 3, successes=1, failures=1, timeouts=1
        assert metrics.total_verified_executions == 3
        assert metrics.verified_successes == 1
        assert metrics.verified_failures == 1
        assert metrics.verified_timeouts == 1
        # Rate for all: 1/3 ~ 0.3333
        expected_rate = round(1 * SCORE_SCALE / 3)
        assert metrics.success_rate_scaled == expected_rate
        assert metrics.failure_rate_scaled == expected_rate
        assert metrics.timeout_rate_scaled == expected_rate
        assert metrics.cancellation_rate_scaled == 0


# ─────────────────────── Singleton ────────────────────────────────────────────


class TestSingleton:
    def test_get_policy_v1_returns_policy(self):
        p = get_policy_v1()
        assert isinstance(p, ReputationPolicyV1)
        assert p.POLICY_NAME == "ReputationPolicy"
        assert p.POLICY_VERSION == 1
        assert p.POLICY_VERSION_STRING == "ReputationPolicyV1"

    def test_singleton_is_same_instance(self):
        p1 = get_policy_v1()
        p2 = get_policy_v1()
        assert p1 is p2


# ─────────────────────── Integer Arithmetic Proof ────────────────────────────


class TestIntegerArithmetic:
    def test_score_is_always_integer(self, policy):
        """score_scaled must be an int, never a float."""
        cases = [
            [],
            [make_event("1", SUCCESS)],
            [make_event("1", FAILURE)],
            [make_event("1", SUCCESS), make_event("2", FAILURE)],
            [make_event(str(i), SUCCESS) for i in range(7)],
        ]
        for case in cases:
            score, _ = policy.calculate_score(case)
            assert isinstance(score, int), f"Expected int, got {type(score)} for {case}"

    def test_rate_scaled_is_always_integer_or_none(self):
        for count in range(0, 5):
            for total in range(0, 6):
                r = _rate_scaled(count, total)
                if total == 0:
                    assert r is None
                else:
                    assert isinstance(r, int)
