"""Phase 6.5.1 — Property & Fuzz Testing for Reputation Scoring Engine.

Executes at least 10,000 property test cases verifying:
1. Score Bounds: score_scaled is ALWAYS in [0, 10000]
2. Determinism: same evidence set ALWAYS produces identical score and evidence hash
3. Order-Independence: shuffling events produces identical score and identical evidence hash
4. Duplicate-Resistance: duplicate events cannot inflate counts or alter score
5. Neutrality: timeouts and cancellations never affect active denominator N or score
"""

import random
import uuid
import pytest

from app.services.reputation_policy.policy_v1 import (
    COLD_START_SCORE,
    OUTCOME_VERIFIED_CANCELLATION,
    OUTCOME_VERIFIED_FAILURE,
    OUTCOME_VERIFIED_SUCCESS,
    OUTCOME_VERIFIED_TIMEOUT,
    EvidenceEvent,
    ReputationPolicyV1,
    compute_evidence_set_hash,
    get_policy_v1,
)


def make_ev(ev_id: str, outcome: str, confirmed_at: str = "2026-01-01T00:00:00Z") -> EvidenceEvent:
    return EvidenceEvent(event_id=ev_id, outcome_type=outcome, confirmed_at=confirmed_at)


class TestPropertyFuzzing:
    """Property test suite executing 10,000+ verification cases."""

    @pytest.fixture
    def policy(self) -> ReputationPolicyV1:
        return get_policy_v1()

    def test_property_bounds_10000_cases(self, policy):
        """Property 1: score is ALWAYS an integer in [0, 10000] across 10,000 random input distributions."""
        rng = random.Random(42)
        total_cases = 10_000

        for _ in range(total_cases):
            s = rng.randint(0, 150)
            f = rng.randint(0, 150)
            t = rng.randint(0, 30)
            c = rng.randint(0, 30)

            events = (
                [make_ev(f"s_{i}", OUTCOME_VERIFIED_SUCCESS) for i in range(s)]
                + [make_ev(f"f_{i}", OUTCOME_VERIFIED_FAILURE) for i in range(f)]
                + [make_ev(f"t_{i}", OUTCOME_VERIFIED_TIMEOUT) for i in range(t)]
                + [make_ev(f"c_{i}", OUTCOME_VERIFIED_CANCELLATION) for i in range(c)]
            )
            rng.shuffle(events)
            score, expl = policy.calculate_score(events)

            assert isinstance(score, int)
            assert 0 <= score <= 10000
            assert expl.final_score_scaled == score
            if s + f == 0:
                assert score == 5000
                assert expl.is_cold_start is True
            else:
                expected = round((s * 10000) / (s + f))
                assert score == expected

        print(f"\n[PASSED] Property Bounds Verified: {total_cases} cases executed.")

    def test_property_order_independence_10000_cases(self, policy):
        """Property 2: Shuffling evidence order yields identical score and identical evidence-set hash (10,000 cases)."""
        rng = random.Random(1337)
        total_cases = 10_000

        # Create base event sets of varying compositions
        for case_idx in range(total_cases):
            s = rng.randint(0, 20)
            f = rng.randint(0, 20)
            t = rng.randint(0, 5)
            c = rng.randint(0, 5)

            events = (
                [make_ev(f"s_{i}_{case_idx}", OUTCOME_VERIFIED_SUCCESS) for i in range(s)]
                + [make_ev(f"f_{i}_{case_idx}", OUTCOME_VERIFIED_FAILURE) for i in range(f)]
                + [make_ev(f"t_{i}_{case_idx}", OUTCOME_VERIFIED_TIMEOUT) for i in range(t)]
                + [make_ev(f"c_{i}_{case_idx}", OUTCOME_VERIFIED_CANCELLATION) for i in range(c)]
            )

            # Compute canonical reference
            ref_score, ref_expl = policy.calculate_score(events)
            ref_hash = ref_expl.evidence_set_hash

            # Shuffle order and recompute
            shuffled = list(events)
            rng.shuffle(shuffled)
            shuf_score, shuf_expl = policy.calculate_score(shuffled)
            shuf_hash = shuf_expl.evidence_set_hash

            assert ref_score == shuf_score
            assert ref_hash == shuf_hash
            assert ref_expl.active_denominator == shuf_expl.active_denominator

        print(f"\n[PASSED] Property Order-Independence Verified: {total_cases} cases executed.")

    def test_property_duplicate_resistance_10000_cases(self, policy):
        """Property 3: Duplicating existing events does not change evidence count, score, or hash (10,000 cases)."""
        rng = random.Random(9999)
        total_cases = 10_000

        for case_idx in range(total_cases):
            s = rng.randint(1, 10)
            f = rng.randint(1, 10)

            unique_events = (
                [make_ev(f"s_{i}", OUTCOME_VERIFIED_SUCCESS) for i in range(s)]
                + [make_ev(f"f_{i}", OUTCOME_VERIFIED_FAILURE) for i in range(f)]
            )
            base_score, base_expl = policy.calculate_score(unique_events)

            # Inject random duplicates
            duped_events = list(unique_events)
            num_dups = rng.randint(1, 15)
            for _ in range(num_dups):
                dup = rng.choice(unique_events)
                duped_events.append(dup)

            rng.shuffle(duped_events)
            duped_score, duped_expl = policy.calculate_score(duped_events)

            assert base_score == duped_score
            assert base_expl.evidence_set_hash == duped_expl.evidence_set_hash
            assert base_expl.active_denominator == duped_expl.active_denominator
            assert base_expl.evidence_event_count == duped_expl.evidence_event_count

        print(f"\n[PASSED] Property Duplicate Resistance Verified: {total_cases} cases executed.")

    def test_property_neutrality_10000_cases(self, policy):
        """Property 4: Timeouts and cancellations NEVER affect active denominator N or final score (10,000 cases)."""
        rng = random.Random(777)
        total_cases = 10_000

        for case_idx in range(total_cases):
            s = rng.randint(0, 15)
            f = rng.randint(0, 15)
            base_events = (
                [make_ev(f"s_{i}", OUTCOME_VERIFIED_SUCCESS) for i in range(s)]
                + [make_ev(f"f_{i}", OUTCOME_VERIFIED_FAILURE) for i in range(f)]
            )
            base_score, base_expl = policy.calculate_score(base_events)

            # Add arbitrary timeouts and cancellations
            num_t = rng.randint(1, 20)
            num_c = rng.randint(1, 20)
            augmented = (
                base_events
                + [make_ev(f"t_{i}", OUTCOME_VERIFIED_TIMEOUT) for i in range(num_t)]
                + [make_ev(f"c_{i}", OUTCOME_VERIFIED_CANCELLATION) for i in range(num_c)]
            )
            rng.shuffle(augmented)
            aug_score, aug_expl = policy.calculate_score(augmented)

            assert base_score == aug_score
            assert base_expl.active_denominator == aug_expl.active_denominator

        print(f"\n[PASSED] Property Neutrality Verified: {total_cases} cases executed.")
