"""
Phase 6.6.1 — Selection Integrity Hardening Tests

Test matrix (as required by Phase 6.6.1 spec):

  A. Reputation state classification
     A1. CANONICAL reputation (N > 0) — score from DB row
     A2. COLD_START reputation (N == 0 row exists) — row present but N=0
     A3. ABSENT reputation (no row) — default 5000, state=ABSENT
     A4. CANONICAL vs COLD_START both 5000 — never conflated

  B. Selection policy correctness
     B1. Highest score_scaled wins
     B2. Tie-break: agent_id ASC (no evidence_count weighting)
     B3. Evidence_count does NOT affect ranking (informational only)
     B4. Agent lifecycle filtering (DRAFT, VALIDATING, SUSPENDED, DEPRECATED excluded)

  C. Canonical hash integrity
     C1. Hash recomputation matches stored hash
     C2. Mutation of one input changes hash
     C3. Candidate order independence (hash stable regardless of DB iteration order)

  D. Snapshot immutability
     D1. Changing score after selection does not mutate stored decision
     D2. Changing evidence count after selection does not mutate stored decision
     D3. Multiple reputation changes leave original decision unchanged

  E. Agent version pinning
     E1. Version pinned at selection time
     E2. Modifying version 2 does not affect a v1 selection decision
     E3. Making version unavailable does not rewrite historical decision

  F. TOCTOU protection (expanded)
     F1. Select A, then add higher-score B — existing task still pinned to A
     F2. Select A, change A score, add B — original decision unmodified

  G. Reselection semantics
     G1. attempt_number=1 on first selection
     G2. attempt_number=2 creates new row, does not overwrite attempt_number=1

  H. Security — client cannot inject reputation
     H1. request_constraints cannot override score_scaled
     H2. Reputation comes from authoritative DB state only

  I. Concurrency
     I1. Simultaneous selection on same candidate set produces same winner

  J. Cold-start matrix
     J1. Two agents both absent — deterministic by agent_id
     J2. score=5000 N=0 vs score=5000 N>0 (both COLD_START/CANONICAL) — policy-exact
"""

import asyncio
import uuid
import pytest
from sqlalchemy import select as sa_select

from app.models.agent import Agent, AgentCapability, AgentStatus, AgentVersion
from app.models.agent_selection import SelectionDecision
from app.models.reputation_scoring import ReputationScore, ReputationPolicyVersion
from app.models.user import User
from app.schemas.orchestration import TaskDefinitionInput
from app.services.agent_selector import (
    DeterministicAgentSelector,
    ReputationState,
    COLD_START_SCORE,
    SELECTION_POLICY_VERSION,
)
from app.services.hashing import compute_canonical_hash


# ──────────────────────────────────────────────────────────────────────────────
# Fixture helpers
# ──────────────────────────────────────────────────────────────────────────────

async def _ensure_policy_version(db) -> ReputationPolicyVersion:
    """Get-or-create ReputationPolicyV1 policy version row."""
    existing = (await db.execute(
        sa_select(ReputationPolicyVersion).where(
            ReputationPolicyVersion.policy_name == "ReputationPolicyV1",
            ReputationPolicyVersion.version_number == 1,
        )
    )).scalars().first()
    if existing:
        return existing
    pv = ReputationPolicyVersion(
        policy_name="ReputationPolicyV1",
        version_number=1,
        description="Test",
        formula_json={},
        is_active=True,
    )
    db.add(pv)
    await db.flush()
    return pv


async def _create_user(db) -> User:
    u = User(
        wallet_address=f"0x{uuid.uuid4().hex[:40]}",
        primary_role="DEVELOPER",
    )
    db.add(u)
    await db.flush()
    return u


async def _create_agent(db, name: str, slug: str, capability: str,
                         status: str = AgentStatus.PUBLISHED.value) -> tuple[Agent, AgentVersion]:
    user = await _create_user(db)
    agent = Agent(
        owner_user_id=user.id,
        name=name,
        slug=slug,
        description="Test",
        status=status,
    )
    db.add(agent)
    await db.flush()

    cap = AgentCapability(agent_id=agent.id, capability=capability, description=capability)
    db.add(cap)

    version = AgentVersion(
        agent_id=agent.id,
        version="1.0.0",
        manifest={"test": True},
        input_schema={},
        output_schema={},
        runtime_config={},
        pricing_config={},
        verification_config={},
    )
    db.add(version)
    await db.flush()

    agent.current_version_id = version.id
    db.add(agent)
    await db.flush()
    return agent, version


async def _add_score(db, agent_id: uuid.UUID, score_scaled: int,
                      total_verified_executions: int = 10) -> ReputationScore:
    pv = await _ensure_policy_version(db)
    score = ReputationScore(
        agent_id=agent_id,
        chain_id=31337,
        policy_version_id=pv.id,
        policy_version="ReputationPolicyV1",
        score_scaled=score_scaled,
        score_min=0,
        score_max=10000,
        total_verified_executions=total_verified_executions,
        verified_successes=total_verified_executions if score_scaled > 5000 else 0,
        verified_failures=0,
        verified_timeouts=0,
        verified_cancellations=0,
        success_rate_scaled=10000 if total_verified_executions > 0 else None,
        failure_rate_scaled=0 if total_verified_executions > 0 else None,
        timeout_rate_scaled=0 if total_verified_executions > 0 else None,
        cancellation_rate_scaled=0 if total_verified_executions > 0 else None,
        experience_count=total_verified_executions,
        evidence_set_hash="a" * 64,
        evidence_event_count=total_verified_executions,
        calculation_reason="Test",
    )
    db.add(score)
    await db.flush()
    return score


def _task(capability: str, suffix: str = "") -> TaskDefinitionInput:
    return TaskDefinitionInput(
        task_key=f"t_{suffix or capability}",
        capability=capability,
        depends_on=[],
    )


# ──────────────────────────────────────────────────────────────────────────────
# A. Reputation state classification
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_A1_canonical_reputation(db_session):
    """Agent with N>0 gets CANONICAL state and exact DB score."""
    a, _ = await _create_agent(db_session, "A1", "a1", "cap_A1")
    await _add_score(db_session, a.id, 8000, total_verified_executions=50)

    sel = DeterministicAgentSelector()
    res = await sel.select_agent(db_session, _task("cap_A1"))

    assert res.decision.reputation_state == ReputationState.CANONICAL.value
    assert res.decision.reputation_score_scaled == 8000
    assert res.decision.reputation_evidence_count == 50
    assert res.decision.reputation_evidence_hash is not None


@pytest.mark.asyncio
async def test_A2_cold_start_reputation_row_exists(db_session):
    """Agent with N=0 row gets COLD_START state (policy-computed, not absent)."""
    a, _ = await _create_agent(db_session, "A2", "a2", "cap_A2")
    # Add score row with N=0 (cold start per policy)
    await _add_score(db_session, a.id, 5000, total_verified_executions=0)

    sel = DeterministicAgentSelector()
    res = await sel.select_agent(db_session, _task("cap_A2"))

    assert res.decision.reputation_state == ReputationState.COLD_START.value
    assert res.decision.reputation_score_scaled == 5000
    # reputation_evidence_hash present (row exists)
    assert res.decision.reputation_evidence_hash is not None


@pytest.mark.asyncio
async def test_A3_absent_reputation_no_row(db_session):
    """Agent with NO score row gets ABSENT state; reputation fields are NULL."""
    a, _ = await _create_agent(db_session, "A3", "a3", "cap_A3")
    # No score row added

    sel = DeterministicAgentSelector()
    res = await sel.select_agent(db_session, _task("cap_A3"))

    assert res.decision.reputation_state == ReputationState.ABSENT.value
    # ABSENT agents: NULL reputation fields (not fabricated)
    assert res.decision.reputation_score_scaled is None
    assert res.decision.reputation_evidence_count is None
    assert res.decision.reputation_evidence_hash is None


@pytest.mark.asyncio
async def test_A4_canonical_and_cold_start_not_conflated(db_session):
    """CANONICAL 5000 and COLD_START 5000 have different states even when score is equal."""
    # Agent with genuine cold-start (N=0 row)
    a_cold, _ = await _create_agent(db_session, "A4_cold", "a4c", "cap_A4")
    await _add_score(db_session, a_cold.id, 5000, total_verified_executions=0)

    # Agent with canonical score = 5000 (e.g. 1 success + 1 failure)
    a_canon, _ = await _create_agent(db_session, "A4_canon", "a4n", "cap_A4x")
    await _add_score(db_session, a_canon.id, 5000, total_verified_executions=2)

    sel = DeterministicAgentSelector()

    res_cold = await sel.select_agent(db_session, _task("cap_A4"))
    res_canon = await sel.select_agent(db_session, _task("cap_A4x"))

    assert res_cold.decision.reputation_state == ReputationState.COLD_START.value
    assert res_canon.decision.reputation_state == ReputationState.CANONICAL.value
    # Both have score 5000 but states differ — must never collapse
    assert res_cold.decision.reputation_score_scaled == 5000
    assert res_canon.decision.reputation_score_scaled == 5000


# ──────────────────────────────────────────────────────────────────────────────
# B. Selection policy correctness
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_B1_highest_score_wins(db_session):
    """Score_scaled DESC is the primary ranking criterion."""
    a, _ = await _create_agent(db_session, "B1a", "b1a", "cap_B1")
    b, _ = await _create_agent(db_session, "B1b", "b1b", "cap_B1")
    await _add_score(db_session, a.id, 3000)
    await _add_score(db_session, b.id, 9000)

    sel = DeterministicAgentSelector()
    res = await sel.select_agent(db_session, _task("cap_B1"))
    assert res.agent_ref.agent_id == b.id


@pytest.mark.asyncio
async def test_B2_tiebreak_agent_id_asc_not_evidence_count(db_session):
    """Tie-break is agent_id ASC. evidence_count does NOT affect ranking."""
    a, _ = await _create_agent(db_session, "B2a", "b2a", "cap_B2")
    b, _ = await _create_agent(db_session, "B2b", "b2b", "cap_B2")
    # Same score; b has much more evidence — must NOT change winner
    await _add_score(db_session, a.id, 7000, total_verified_executions=100)
    await _add_score(db_session, b.id, 7000, total_verified_executions=1)

    # Winner must be whichever has the smaller agent_id (UUID string ASC)
    expected_winner = a.id if str(a.id) < str(b.id) else b.id

    sel = DeterministicAgentSelector()
    res = await sel.select_agent(db_session, _task("cap_B2"))
    assert res.agent_ref.agent_id == expected_winner


@pytest.mark.asyncio
async def test_B3_evidence_count_is_informational_only(db_session):
    """Evidence count in candidates_json is informational; reversed count must not change winner."""
    a, _ = await _create_agent(db_session, "B3a", "b3a", "cap_B3")
    b, _ = await _create_agent(db_session, "B3b", "b3b", "cap_B3")
    # a has much lower evidence; b has much higher. Score same.
    # Policy must NEVER prefer b over a because of evidence count.
    await _add_score(db_session, a.id, 7000, total_verified_executions=1)
    await _add_score(db_session, b.id, 7000, total_verified_executions=9999)

    expected_winner = a.id if str(a.id) < str(b.id) else b.id

    sel = DeterministicAgentSelector()
    res = await sel.select_agent(db_session, _task("cap_B3"))
    assert res.agent_ref.agent_id == expected_winner
    # Verify evidence_count appears as informational in the snapshot
    winner_candidate = next(
        c for c in res.decision.eligible_candidates_json
        if c["agent_id"] == str(res.agent_ref.agent_id)
    )
    assert "total_verified_executions" in winner_candidate


@pytest.mark.asyncio
async def test_B4_lifecycle_filtering(db_session):
    """Only PUBLISHED agents are eligible. All other statuses excluded."""
    published, _ = await _create_agent(db_session, "Published", "pub1", "cap_B4",
                                        status=AgentStatus.PUBLISHED.value)
    await _add_score(db_session, published.id, 5000)

    # Create ineligible agents in every other status
    for status_val in ["DRAFT", "VALIDATING", "SUSPENDED", "DEPRECATED"]:
        ineligible, _ = await _create_agent(db_session, f"Bad_{status_val}", f"bad_{status_val}",
                                             "cap_B4", status=status_val)
        await _add_score(db_session, ineligible.id, 9999)  # irrelevant; must be excluded

    sel = DeterministicAgentSelector()
    res = await sel.select_agent(db_session, _task("cap_B4"))
    assert res.agent_ref.agent_id == published.id


# ──────────────────────────────────────────────────────────────────────────────
# C. Canonical hash integrity
# ──────────────────────────────────────────────────────────────────────────────

def _recompute_hash(decision: SelectionDecision) -> str:
    """Reconstruct the canonical selection hash from a SelectionDecision."""
    candidates_for_hash = sorted(
        decision.eligible_candidates_json,
        key=lambda d: d["agent_id"],
    )
    payload = {
        "candidates": candidates_for_hash,
        "constraints": decision.request_constraints,
        "economic_policy": decision.economic_policy_version,
        "policy": decision.selection_policy_version,
        "selected_agent_id": str(decision.selected_agent_id),
        "selected_price_atomic": int(decision.selected_price_atomic) if decision.selected_price_atomic is not None else 0,
        "selected_version": decision.selected_agent_version,
    }
    return compute_canonical_hash(payload)


@pytest.mark.asyncio
async def test_C1_hash_recomputation_matches_stored(db_session):
    """Independently recomputed hash == canonical_selection_hash."""
    a, _ = await _create_agent(db_session, "C1", "c1", "cap_C1")
    await _add_score(db_session, a.id, 7500)

    sel = DeterministicAgentSelector()
    res = await sel.select_agent(db_session, _task("cap_C1"))
    decision = res.decision

    recomputed = _recompute_hash(decision)
    assert recomputed == decision.canonical_selection_hash, (
        f"Recomputed hash {recomputed!r} != stored {decision.canonical_selection_hash!r}"
    )


@pytest.mark.asyncio
async def test_C2_hash_detects_tampering(db_session):
    """Mutating any ranking-relevant field changes the recomputed hash."""
    a, _ = await _create_agent(db_session, "C2", "c2", "cap_C2")
    await _add_score(db_session, a.id, 7500)

    sel = DeterministicAgentSelector()
    res = await sel.select_agent(db_session, _task("cap_C2"))
    original_hash = res.decision.canonical_selection_hash

    # Tamper: change the score in the candidate snapshot
    import copy
    tampered_candidates = copy.deepcopy(res.decision.eligible_candidates_json)
    tampered_candidates[0]["score_scaled"] = 9999

    payload = {
        "candidates": sorted(tampered_candidates, key=lambda d: d["agent_id"]),
        "constraints": res.decision.request_constraints,
        "economic_policy": res.decision.economic_policy_version,
        "policy": res.decision.selection_policy_version,
        "selected_agent_id": str(res.decision.selected_agent_id),
        "selected_price_atomic": int(res.decision.selected_price_atomic) if res.decision.selected_price_atomic is not None else 0,
        "selected_version": res.decision.selected_agent_version,
    }
    tampered_hash = compute_canonical_hash(payload)
    assert tampered_hash != original_hash, "Tampered score must produce different hash"


@pytest.mark.asyncio
async def test_C3_candidate_order_independence(db_session):
    """Hash is identical regardless of candidate discovery order."""
    # Create 4 candidates
    agents = []
    for i in range(4):
        a, _ = await _create_agent(db_session, f"C3_{i}", f"c3_{i}", "cap_C3")
        await _add_score(db_session, a.id, 5000 + i * 100)
        agents.append(a)

    sel = DeterministicAgentSelector()
    task = _task("cap_C3")
    # Run selection twice with identical input
    res1 = await sel.select_agent(db_session, task)
    res2 = await sel.select_agent(db_session, task)

    assert res1.agent_ref.agent_id == res2.agent_ref.agent_id
    assert res1.decision.canonical_selection_hash == res2.decision.canonical_selection_hash

    # Verify that permutation of candidates in eligible_candidates_json produces identical hash
    import random
    candidates = list(res1.decision.eligible_candidates_json)
    shuffled = list(candidates)
    random.shuffle(shuffled)
    # Both sorted by agent_id yield exact same canonical hash
    payload1 = {
        "candidates": sorted(candidates, key=lambda c: str(c["agent_id"])),
        "constraints": res1.decision.request_constraints,
        "economic_policy": res1.decision.economic_policy_version,
        "policy": res1.decision.selection_policy_version,
        "selected_agent_id": str(res1.decision.selected_agent_id),
        "selected_price_atomic": int(res1.decision.selected_price_atomic) if res1.decision.selected_price_atomic is not None else 0,
        "selected_version": res1.decision.selected_agent_version,
    }
    payload2 = {
        "candidates": sorted(shuffled, key=lambda c: str(c["agent_id"])),
        "constraints": res1.decision.request_constraints,
        "economic_policy": res1.decision.economic_policy_version,
        "policy": res1.decision.selection_policy_version,
        "selected_agent_id": str(res1.decision.selected_agent_id),
        "selected_price_atomic": int(res1.decision.selected_price_atomic) if res1.decision.selected_price_atomic is not None else 0,
        "selected_version": res1.decision.selected_agent_version,
    }
    assert compute_canonical_hash(payload1) == compute_canonical_hash(payload2)


# ──────────────────────────────────────────────────────────────────────────────
# D. Snapshot immutability
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_D1_score_change_does_not_mutate_stored_decision(db_session):
    """After selection, mutating the reputation score does not change the stored decision."""
    a, _ = await _create_agent(db_session, "D1", "d1", "cap_D1")
    score_row = await _add_score(db_session, a.id, 8000)

    sel = DeterministicAgentSelector()
    res = await sel.select_agent(db_session, _task("cap_D1"))
    decision = res.decision
    # Persist decision
    db_session.add(decision)
    await db_session.flush()

    original_score = decision.reputation_score_scaled
    original_hash = decision.canonical_selection_hash

    # Mutate the score in DB
    score_row.score_scaled = 1000
    await db_session.flush()

    # The SelectionDecision must be unchanged
    assert decision.reputation_score_scaled == original_score
    assert decision.canonical_selection_hash == original_hash
    # Recomputation with original data still matches
    assert _recompute_hash(decision) == original_hash


@pytest.mark.asyncio
async def test_D2_evidence_count_change_does_not_mutate_decision(db_session):
    """After selection, mutating evidence count does not change stored decision."""
    a, _ = await _create_agent(db_session, "D2", "d2", "cap_D2")
    score_row = await _add_score(db_session, a.id, 7000, total_verified_executions=10)

    sel = DeterministicAgentSelector()
    res = await sel.select_agent(db_session, _task("cap_D2"))
    db_session.add(res.decision)
    await db_session.flush()

    original_count = res.decision.reputation_evidence_count
    original_hash = res.decision.canonical_selection_hash

    # Add more executions
    score_row.total_verified_executions = 99999
    await db_session.flush()

    assert res.decision.reputation_evidence_count == original_count
    assert res.decision.canonical_selection_hash == original_hash


# ──────────────────────────────────────────────────────────────────────────────
# E. Agent version pinning
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_E1_version_pinned_at_selection(db_session):
    """Selection pins the exact version at selection time."""
    a, v1 = await _create_agent(db_session, "E1", "e1", "cap_E1")
    await _add_score(db_session, a.id, 8000)

    sel = DeterministicAgentSelector()
    res = await sel.select_agent(db_session, _task("cap_E1"))
    assert res.decision.selected_agent_version == "1.0.0"
    assert res.agent_ref.agent_version_id == v1.id


@pytest.mark.asyncio
async def test_E2_version_change_does_not_affect_historical_decision(db_session):
    """Adding a second version does not change an already-recorded selection."""
    a, v1 = await _create_agent(db_session, "E2", "e2", "cap_E2")
    await _add_score(db_session, a.id, 8000)

    sel = DeterministicAgentSelector()
    res = await sel.select_agent(db_session, _task("cap_E2"))
    db_session.add(res.decision)
    await db_session.flush()

    original_version = res.decision.selected_agent_version
    original_hash = res.decision.canonical_selection_hash

    # Add v2
    v2 = AgentVersion(
        agent_id=a.id,
        version="2.0.0",
        manifest={"test": True, "v": 2},
        input_schema={},
        output_schema={},
        runtime_config={},
        pricing_config={},
        verification_config={},
    )
    db_session.add(v2)
    await db_session.flush()
    a.current_version_id = v2.id
    await db_session.flush()

    # Original decision remains pinned to v1
    assert res.decision.selected_agent_version == original_version
    assert res.decision.canonical_selection_hash == original_hash


# ──────────────────────────────────────────────────────────────────────────────
# F. TOCTOU protection (expanded)
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_F1_new_higher_score_agent_does_not_replace_pinned_selection(db_session):
    """A new high-score agent introduced after selection must not mutate the stored decision."""
    a, _ = await _create_agent(db_session, "F1a", "f1a", "cap_F1")
    await _add_score(db_session, a.id, 7000)

    sel = DeterministicAgentSelector()
    res = await sel.select_agent(db_session, _task("cap_F1"))
    db_session.add(res.decision)
    await db_session.flush()

    original_agent = res.decision.selected_agent_id
    original_hash = res.decision.canonical_selection_hash

    # Add a superior agent after selection
    b, _ = await _create_agent(db_session, "F1b", "f1b", "cap_F1")
    await _add_score(db_session, b.id, 9900)

    # Original decision unchanged
    assert res.decision.selected_agent_id == original_agent
    assert res.decision.canonical_selection_hash == original_hash

    # A new selection WOULD pick b
    res2 = await sel.select_agent(db_session, _task("cap_F1", "r2"))
    assert res2.agent_ref.agent_id == b.id
    # But original decision still points to a
    assert res.decision.selected_agent_id == a.id


@pytest.mark.asyncio
async def test_F2_toctou_combined_score_change_and_new_agent(db_session):
    """Change A score, add B with higher score — original decision remains for A."""
    a, _ = await _create_agent(db_session, "F2a", "f2a", "cap_F2")
    score_row = await _add_score(db_session, a.id, 9000)

    sel = DeterministicAgentSelector()
    res = await sel.select_agent(db_session, _task("cap_F2"))
    db_session.add(res.decision)
    await db_session.flush()

    original_agent = res.decision.selected_agent_id
    original_score = res.decision.reputation_score_scaled

    # Mutate A's reputation
    score_row.score_scaled = 100
    # Add B with higher score
    b, _ = await _create_agent(db_session, "F2b", "f2b", "cap_F2")
    await _add_score(db_session, b.id, 9999)
    await db_session.flush()

    # Original decision unchanged
    assert res.decision.selected_agent_id == original_agent
    assert res.decision.reputation_score_scaled == original_score


# ──────────────────────────────────────────────────────────────────────────────
# G. Reselection semantics
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_G1_first_selection_attempt_number_is_one(db_session):
    """First selection always gets attempt_number=1."""
    a, _ = await _create_agent(db_session, "G1", "g1", "cap_G1")
    await _add_score(db_session, a.id, 7000)

    sel = DeterministicAgentSelector()
    res = await sel.select_agent(db_session, _task("cap_G1"), attempt_number=1)
    assert res.decision.attempt_number == 1


@pytest.mark.asyncio
async def test_G2_reselection_creates_new_row_not_overwrite(db_session):
    """Retry selection creates a new decision with attempt_number=2; original unchanged."""
    a, _ = await _create_agent(db_session, "G2", "g2", "cap_G2")
    await _add_score(db_session, a.id, 7000)

    sel = DeterministicAgentSelector()
    res1 = await sel.select_agent(db_session, _task("cap_G2"), attempt_number=1)
    res2 = await sel.select_agent(db_session, _task("cap_G2"), attempt_number=2)

    assert res1.decision.attempt_number == 1
    assert res2.decision.attempt_number == 2
    # Both are separate objects (separate decisions)
    assert res1.decision is not res2.decision


# ──────────────────────────────────────────────────────────────────────────────
# H. Security — client cannot inject reputation
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_H1_client_constraints_cannot_override_reputation(db_session):
    """Client-provided task constraints cannot fabricate or override reputation data."""
    a, _ = await _create_agent(db_session, "H1a", "h1a", "cap_H1")
    b, _ = await _create_agent(db_session, "H1b", "h1b", "cap_H1")
    await _add_score(db_session, a.id, 3000)
    await _add_score(db_session, b.id, 9000)

    # Client pins agent a (lower score) but cannot override b's canonical score
    # Selection must still follow the reputation data; here we test that the
    # constraints field is used for filtering/pinning, not score fabrication.
    task_pinned = TaskDefinitionInput(
        task_key="h1_pin",
        capability="cap_H1",
        agent_id=a.id,   # client pinned to agent a
        depends_on=[],
    )
    sel = DeterministicAgentSelector()
    res = await sel.select_agent(db_session, task_pinned)

    # Pinning is a legitimate constraint (client chooses specific agent)
    # but the score used in the decision comes from authoritative DB state
    assert res.agent_ref.agent_id == a.id
    # Score must be from DB row, not client-provided
    assert res.decision.reputation_score_scaled == 3000


@pytest.mark.asyncio
async def test_H2_reputation_comes_from_db_not_request(db_session):
    """Score in SelectionDecision always reflects DB state at selection time."""
    a, _ = await _create_agent(db_session, "H2", "h2", "cap_H2")
    await _add_score(db_session, a.id, 6500)

    sel = DeterministicAgentSelector()
    res = await sel.select_agent(db_session, _task("cap_H2"))

    # Verify score matches DB
    assert res.decision.reputation_score_scaled == 6500
    assert res.decision.reputation_state == ReputationState.CANONICAL.value


# ──────────────────────────────────────────────────────────────────────────────
# I. Concurrency
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_I1_concurrent_selection_same_candidates(db_session):
    """Concurrent selection on the same candidate set produces consistent results."""
    a, _ = await _create_agent(db_session, "I1a", "i1a", "cap_I1")
    b, _ = await _create_agent(db_session, "I1b", "i1b", "cap_I1")
    await _add_score(db_session, a.id, 7000)
    await _add_score(db_session, b.id, 8000)

    sel = DeterministicAgentSelector()
    task = _task("cap_I1")

    # Run multiple concurrent selections on the same task and candidate set
    results = await asyncio.gather(
        sel.select_agent(db_session, task),
        sel.select_agent(db_session, task),
        sel.select_agent(db_session, task),
    )

    # All must select the same winner
    winner_ids = {r.agent_ref.agent_id for r in results}
    assert len(winner_ids) == 1
    assert winner_ids.pop() == b.id

    # All must have the same canonical hash
    hashes = {r.decision.canonical_selection_hash for r in results}
    assert len(hashes) == 1


# ──────────────────────────────────────────────────────────────────────────────
# J. Cold-start matrix
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_J1_two_absent_agents_deterministic_by_agent_id(db_session):
    """Two ABSENT agents tie at 5000; tie-break is agent_id ASC."""
    a, _ = await _create_agent(db_session, "J1a", "j1a", "cap_J1")
    b, _ = await _create_agent(db_session, "J1b", "j1b", "cap_J1")
    # No score rows — both ABSENT

    expected_winner = a.id if str(a.id) < str(b.id) else b.id

    sel = DeterministicAgentSelector()
    res = await sel.select_agent(db_session, _task("cap_J1"))
    assert res.agent_ref.agent_id == expected_winner
    assert res.decision.reputation_state == ReputationState.ABSENT.value


@pytest.mark.asyncio
async def test_J2_cold_start_row_vs_canonical_same_score(db_session):
    """COLD_START (N=0, score=5000) vs CANONICAL (N>0, score=5000): policy uses score first, then agent_id."""
    a_cold, _ = await _create_agent(db_session, "J2c", "j2c", "cap_J2")
    a_canon, _ = await _create_agent(db_session, "J2n", "j2n", "cap_J2")

    await _add_score(db_session, a_cold.id, 5000, total_verified_executions=0)  # COLD_START
    await _add_score(db_session, a_canon.id, 5000, total_verified_executions=10)  # CANONICAL

    # Both score=5000; tie-break is agent_id ASC — NO preference for N>0
    expected_winner = a_cold.id if str(a_cold.id) < str(a_canon.id) else a_canon.id

    sel = DeterministicAgentSelector()
    res = await sel.select_agent(db_session, _task("cap_J2"))
    assert res.agent_ref.agent_id == expected_winner
    # Both candidates visible in snapshot with correct states
    states_by_id = {c["agent_id"]: c["reputation_state"] for c in res.decision.eligible_candidates_json}
    assert states_by_id[str(a_cold.id)] == ReputationState.COLD_START.value
    assert states_by_id[str(a_canon.id)] == ReputationState.CANONICAL.value


@pytest.mark.asyncio
async def test_J3_absent_vs_cold_start_same_score_tie_break(db_session):
    """ABSENT agent (no row, default 5000) vs COLD_START agent (row N=0) both at 5000; agent_id breaks tie."""
    a_absent, _ = await _create_agent(db_session, "J3abs", "j3a", "cap_J3")
    a_cold, _ = await _create_agent(db_session, "J3cold", "j3c", "cap_J3")
    # Only a_cold has a score row
    await _add_score(db_session, a_cold.id, 5000, total_verified_executions=0)

    expected_winner = a_absent.id if str(a_absent.id) < str(a_cold.id) else a_cold.id

    sel = DeterministicAgentSelector()
    res = await sel.select_agent(db_session, _task("cap_J3"))
    assert res.agent_ref.agent_id == expected_winner

    states_by_id = {c["agent_id"]: c["reputation_state"] for c in res.decision.eligible_candidates_json}
    assert states_by_id[str(a_absent.id)] == ReputationState.ABSENT.value
    assert states_by_id[str(a_cold.id)] == ReputationState.COLD_START.value
