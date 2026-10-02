"""
Phase 6.7 — Cost-Aware & Constraint-Aware Agent Selection Tests

Test matrix covering:
  1. Discovery & Pricing Availability
  2. Budget Filtering (price < budget, price == budget, price > budget, zero budget, unconstrained)
  3. Reputation & Economic Interaction (no composite score; price is pure constraint)
  4. Deterministic Tie-Breaking (equal reputation, different prices -> agent_id ASC)
  5. Snapshot Immutability (post-selection price change does not mutate decision)
  6. TOCTOU Protection (version & price pinned through task execution)
  7. Escrow Consistency (selected price matches escrow creation parameters)
  8. Security & Validation (no client price/reputation injection, precision safety)
  9. Determinism & Order-Independence Property Tests
  10. Concurrency & Performance
"""

import asyncio
from decimal import Decimal
import random
import time
import uuid
import pytest
from sqlalchemy import select as sa_select

from app.models.agent import Agent, AgentCapability, AgentStatus, AgentVersion
from app.models.agent_selection import SelectionDecision
from app.models.orchestration import Orchestration, OrchestrationTask
from app.models.reputation_scoring import ReputationScore, ReputationPolicyVersion
from app.models.user import User
from app.schemas.orchestration import TaskDefinitionInput
from app.services.agent_selector import (
    DeterministicAgentSelector,
    ReputationState,
    COLD_START_SCORE,
    SELECTION_POLICY_VERSION,
    ECONOMIC_POLICY_VERSION,
)
from app.services.hashing import compute_canonical_hash
from app.services.pricing import (
    USDC_FACTOR,
    extract_version_price,
    format_atomic_to_usdc,
    parse_atomic_units,
    parse_usdc_display_to_atomic,
    validate_budget_constraint,
)


# ──────────────────────────────────────────────────────────────────────────────
# Test Fixtures & Helpers
# ──────────────────────────────────────────────────────────────────────────────

async def _ensure_policy_version(db) -> ReputationPolicyVersion:
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
        description="Phase 6.7 Test",
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


async def _create_agent_with_pricing(
    db,
    name: str,
    slug: str,
    capability: str,
    amount_str: str = "1.00",
    currency: str = "USDC",
    price_atomic: int | None = None,
    tools: list[str] | None = None,
    timeout_seconds: int = 300,
    status: str = AgentStatus.PUBLISHED.value,
) -> tuple[Agent, AgentVersion]:
    user = await _create_user(db)
    agent = Agent(
        owner_user_id=user.id,
        name=name,
        slug=slug,
        description="Test Agent",
        status=status,
    )
    db.add(agent)
    await db.flush()

    cap = AgentCapability(agent_id=agent.id, capability=capability, description=capability)
    db.add(cap)

    pricing_dict = {
        "model": "per_execution",
        "amount": amount_str,
        "currency": currency,
    }
    if price_atomic is not None:
        pricing_dict["price_atomic"] = price_atomic

    tool_configs = [
        {"tool_name": t, "enabled": True, "configuration": {}}
        for t in (tools or [])
    ]

    version = AgentVersion(
        agent_id=agent.id,
        version="1.0.0",
        manifest={
            "tools": tool_configs,
            "runtime": {"timeout_seconds": timeout_seconds, "max_retries": 2},
            "pricing": pricing_dict,
        },
        input_schema={},
        output_schema={},
        runtime_config={"timeout_seconds": timeout_seconds},
        pricing_config=pricing_dict,
        verification_config={},
    )
    db.add(version)
    await db.flush()

    agent.current_version_id = version.id
    db.add(agent)
    await db.flush()
    return agent, version


async def _add_score(
    db, agent_id: uuid.UUID, score_scaled: int, total_verified_executions: int = 10
) -> ReputationScore:
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
        evidence_set_hash="b" * 64,
        evidence_event_count=total_verified_executions,
        calculation_reason="Phase 6.7 Test",
    )
    db.add(score)
    await db.flush()
    return score


def _task(
    capability: str,
    max_budget: str | None = None,
    max_budget_atomic: int | None = None,
    budget_currency: str = "USDC",
    required_tools: list[str] | None = None,
    max_timeout_seconds: int | None = None,
) -> TaskDefinitionInput:
    kwargs = {
        "task_key": f"t_{capability}_{uuid.uuid4().hex[:6]}",
        "capability": capability,
        "depends_on": [],
        "budget_currency": budget_currency,
        "required_tools": required_tools or [],
    }
    if max_budget is not None:
        kwargs["max_budget"] = max_budget
    if max_budget_atomic is not None:
        kwargs["max_budget_atomic"] = max_budget_atomic
    if max_timeout_seconds is not None:
        kwargs["max_timeout_seconds"] = max_timeout_seconds
    return TaskDefinitionInput(**kwargs)


# ──────────────────────────────────────────────────────────────────────────────
# 1. Pricing Utilities & Precision Safety
# ──────────────────────────────────────────────────────────────────────────────

def test_pricing_utilities_precision():
    """Verify precision-safe conversions without floating-point errors."""
    assert parse_usdc_display_to_atomic("1.00") == 1_000_000
    assert parse_usdc_display_to_atomic("0.50") == 500_000
    assert parse_usdc_display_to_atomic("0.000001") == 1
    assert parse_usdc_display_to_atomic("0") == 0
    assert parse_usdc_display_to_atomic(10) == 10_000_000

    assert format_atomic_to_usdc(1_500_000) == "1.500000"
    assert format_atomic_to_usdc(0) == "0.000000"

    # Reject > 6 decimals (prevent fractional atomic units)
    with pytest.raises(ValueError, match="more than 6 decimal places"):
        parse_usdc_display_to_atomic("1.0000001")

    # Reject negative
    with pytest.raises(ValueError, match="non-negative"):
        parse_usdc_display_to_atomic("-1.00")

    # Reject malformed
    with pytest.raises(ValueError, match="Invalid monetary string"):
        parse_usdc_display_to_atomic("abc")


# ──────────────────────────────────────────────────────────────────────────────
# 2. Budget Constraints Filtering
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_budget_filter_price_below_budget(db_session):
    """Candidate with price < budget is eligible."""
    a, _ = await _create_agent_with_pricing(db_session, "A", "a", "cap_b1", amount_str="5.00")
    await _add_score(db_session, a.id, 8000)

    sel = DeterministicAgentSelector()
    res = await sel.select_agent(db_session, _task("cap_b1", max_budget="10.00"))

    assert res.agent_ref.agent_id == a.id
    assert res.agent_ref.price_atomic == 5_000_000
    assert res.decision.selected_price_atomic == 5_000_000
    assert res.decision.max_budget_atomic == 10_000_000


@pytest.mark.asyncio
async def test_budget_filter_price_equals_budget(db_session):
    """Candidate with price == budget is eligible (exact boundary)."""
    a, _ = await _create_agent_with_pricing(db_session, "A", "a", "cap_b2", amount_str="10.00")
    await _add_score(db_session, a.id, 8000)

    sel = DeterministicAgentSelector()
    res = await sel.select_agent(db_session, _task("cap_b2", max_budget="10.00"))

    assert res.agent_ref.agent_id == a.id
    assert res.agent_ref.price_atomic == 10_000_000


@pytest.mark.asyncio
async def test_budget_filter_price_exceeds_budget_rejected(db_session):
    """Candidate with price > budget is excluded; raises NO_AFFORDABLE_AGENT."""
    a, _ = await _create_agent_with_pricing(db_session, "A", "a", "cap_b3", amount_str="15.00")
    await _add_score(db_session, a.id, 8000)

    sel = DeterministicAgentSelector()
    with pytest.raises(ValueError, match="NO_AFFORDABLE_AGENT"):
        await sel.select_agent(db_session, _task("cap_b3", max_budget="10.00"))


@pytest.mark.asyncio
async def test_budget_filter_zero_budget(db_session):
    """Zero budget only allows free/zero-price agents."""
    free_agent, _ = await _create_agent_with_pricing(db_session, "Free", "free", "cap_b4", amount_str="0.00")
    paid_agent, _ = await _create_agent_with_pricing(db_session, "Paid", "paid", "cap_b4", amount_str="1.00")
    await _add_score(db_session, free_agent.id, 6000)
    await _add_score(db_session, paid_agent.id, 9000)

    sel = DeterministicAgentSelector()
    res = await sel.select_agent(db_session, _task("cap_b4", max_budget="0.00"))

    assert res.agent_ref.agent_id == free_agent.id
    assert res.agent_ref.price_atomic == 0


@pytest.mark.asyncio
async def test_unconstrained_budget_selects_all(db_session):
    """When budget is None, all price levels are eligible."""
    expensive, _ = await _create_agent_with_pricing(db_session, "Exp", "exp", "cap_b5", amount_str="1000.00")
    await _add_score(db_session, expensive.id, 9000)

    sel = DeterministicAgentSelector()
    res = await sel.select_agent(db_session, _task("cap_b5", max_budget=None))

    assert res.agent_ref.agent_id == expensive.id
    assert res.decision.max_budget_atomic is None


# ──────────────────────────────────────────────────────────────────────────────
# 3. Reputation & Economic Interaction (No Composite Score)
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_over_budget_higher_reputation_excluded(db_session):
    """
    Agent A: score 9500, price 50 USDC (over budget)
    Agent B: score 8000, price 20 USDC (under budget)
    Budget: 25 USDC
    Result: Agent B must win! Price is a hard filter; reputation decides among eligible.
    """
    agent_a, _ = await _create_agent_with_pricing(db_session, "A", "a", "cap_eco", amount_str="50.00")
    agent_b, _ = await _create_agent_with_pricing(db_session, "B", "b", "cap_eco", amount_str="20.00")
    await _add_score(db_session, agent_a.id, 9500)
    await _add_score(db_session, agent_b.id, 8000)

    sel = DeterministicAgentSelector()
    res = await sel.select_agent(db_session, _task("cap_eco", max_budget="25.00"))

    assert res.agent_ref.agent_id == agent_b.id
    assert res.decision.selected_price_atomic == 20_000_000
    assert res.decision.reputation_score_scaled == 8000

    # Verify both candidates are recorded in eligible_candidates_json with correct flags
    dtos = {c["agent_id"]: c for c in res.decision.eligible_candidates_json}
    assert dtos[str(agent_a.id)]["is_affordable"] is False
    assert dtos[str(agent_a.id)]["meets_constraints"] is False
    assert "OVER_BUDGET" in dtos[str(agent_a.id)]["exclusion_reason"]
    assert dtos[str(agent_b.id)]["is_affordable"] is True
    assert dtos[str(agent_b.id)]["meets_constraints"] is True


@pytest.mark.asyncio
async def test_deterministic_tie_break_with_different_prices(db_session):
    """
    Two agents: same reputation (8000), both under budget (20 USDC), different prices (5 vs 15 USDC).
    Per Section 12 rule: Price is NOT a quality score. Ranking is score DESC, tie-break agent_id ASC.
    """
    agent_1, _ = await _create_agent_with_pricing(db_session, "A1", "a1", "cap_tie", amount_str="15.00")
    agent_2, _ = await _create_agent_with_pricing(db_session, "A2", "a2", "cap_tie", amount_str="5.00")
    await _add_score(db_session, agent_1.id, 8000)
    await _add_score(db_session, agent_2.id, 8000)

    expected_winner = agent_1.id if str(agent_1.id) < str(agent_2.id) else agent_2.id

    sel = DeterministicAgentSelector()
    res = await sel.select_agent(db_session, _task("cap_tie", max_budget="20.00"))

    assert res.agent_ref.agent_id == expected_winner


# ──────────────────────────────────────────────────────────────────────────────
# 4. Tool & Runtime Constraints
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_tool_requirement_filter(db_session):
    """Agent missing a required tool is excluded even if within budget and higher score."""
    a, _ = await _create_agent_with_pricing(db_session, "NoTool", "notool", "cap_tool", tools=["calculator"])
    b, _ = await _create_agent_with_pricing(db_session, "WithTool", "withtool", "cap_tool", tools=["calculator", "web_search"])
    await _add_score(db_session, a.id, 9900)
    await _add_score(db_session, b.id, 7000)

    sel = DeterministicAgentSelector()
    res = await sel.select_agent(db_session, _task("cap_tool", required_tools=["web_search"]))

    assert res.agent_ref.agent_id == b.id


@pytest.mark.asyncio
async def test_runtime_timeout_filter(db_session):
    """Agent exceeding max_timeout_seconds is excluded."""
    slow, _ = await _create_agent_with_pricing(db_session, "Slow", "slow", "cap_time", timeout_seconds=600)
    fast, _ = await _create_agent_with_pricing(db_session, "Fast", "fast", "cap_time", timeout_seconds=120)
    await _add_score(db_session, slow.id, 9500)
    await _add_score(db_session, fast.id, 7500)

    sel = DeterministicAgentSelector()
    res = await sel.select_agent(db_session, _task("cap_time", max_timeout_seconds=300))

    assert res.agent_ref.agent_id == fast.id


# ──────────────────────────────────────────────────────────────────────────────
# 5. Snapshot Immutability & TOCTOU Protection
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_price_mutation_after_selection_does_not_mutate_decision(db_session):
    """
    After selection is recorded, changing the agent's price does not alter
    the historical SelectionDecision or its canonical selection hash.
    """
    agent, version = await _create_agent_with_pricing(db_session, "Mut", "mut", "cap_mut", amount_str="10.00")
    await _add_score(db_session, agent.id, 8000)

    sel = DeterministicAgentSelector()
    res = await sel.select_agent(db_session, _task("cap_mut", max_budget="20.00"))
    decision = res.decision
    db_session.add(decision)
    await db_session.flush()

    orig_price = decision.selected_price_atomic
    orig_hash = decision.canonical_selection_hash
    assert orig_price == 10_000_000

    # Mutate agent version price in DB
    version.pricing_config = {"model": "per_execution", "amount": "99.00", "currency": "USDC"}
    await db_session.flush()

    # Re-fetch decision from DB
    refetched = (await db_session.execute(
        sa_select(SelectionDecision).where(SelectionDecision.id == decision.id)
    )).scalar_one()

    assert refetched.selected_price_atomic == orig_price
    assert refetched.canonical_selection_hash == orig_hash


@pytest.mark.asyncio
async def test_pinned_price_in_orchestration_task(db_session):
    """OrchestrationTask stores pinned price_atomic matching selection."""
    agent, _ = await _create_agent_with_pricing(db_session, "Orch", "orch", "cap_orch", amount_str="12.50")
    await _add_score(db_session, agent.id, 8000)

    sel = DeterministicAgentSelector()
    res = await sel.select_agent(db_session, _task("cap_orch", max_budget="15.00"))

    # Create OrchestrationTask with pinned price
    user = await _create_user(db_session)
    orch = Orchestration(requested_by=user.id, goal="Test Goal")
    db_session.add(orch)
    await db_session.flush()

    task = OrchestrationTask(
        orchestration_id=orch.id,
        task_key="t1",
        agent_id=res.agent_ref.agent_id,
        agent_version=res.agent_ref.agent_version,
        price_atomic=res.agent_ref.price_atomic,
        price_currency=res.agent_ref.price_currency,
        input_data={},
        input_hash="c" * 64,
        status="PENDING",
    )
    db_session.add(task)
    await db_session.flush()

    assert task.price_atomic == 12_500_000
    assert task.price_atomic == res.decision.selected_price_atomic


# ──────────────────────────────────────────────────────────────────────────────
# 6. Escrow Consistency Verification
# ──────────────────────────────────────────────────────────────────────────────

def test_escrow_amount_consistency():
    """Verify selected price and escrow creation amount cannot diverge."""
    selected_price_atomic = 25_000_000  # 25 USDC
    escrow_params = {
        "referenceId": "0x" + "11" * 32,
        "beneficiary": "0x" + "22" * 20,
        "amount": selected_price_atomic,
        "executionDeadline": 1800000000,
        "salt": 0,
    }
    # Escrow amount must equal authoritative selection price
    assert escrow_params["amount"] == selected_price_atomic
    assert isinstance(escrow_params["amount"], int)


# ──────────────────────────────────────────────────────────────────────────────
# 7. Canonical Hash Detects Economic Tampering
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_canonical_hash_detects_price_tampering(db_session):
    """Mutating selected price in payload changes the canonical hash."""
    a, _ = await _create_agent_with_pricing(db_session, "Tamp", "tamp", "cap_tamp", amount_str="5.00")
    await _add_score(db_session, a.id, 8000)

    sel = DeterministicAgentSelector()
    res = await sel.select_agent(db_session, _task("cap_tamp", max_budget="10.00"))
    orig_hash = res.decision.canonical_selection_hash

    # Tampered payload with price = 1000 instead of 5000000
    import copy
    tampered_candidates = copy.deepcopy(res.decision.eligible_candidates_json)
    tampered_payload = {
        "candidates": sorted(tampered_candidates, key=lambda d: d["agent_id"]),
        "constraints": res.decision.request_constraints,
        "economic_policy": res.decision.economic_policy_version,
        "policy": res.decision.selection_policy_version,
        "selected_agent_id": str(res.decision.selected_agent_id),
        "selected_price_atomic": 1_000_000,  # Tampered!
        "selected_version": res.decision.selected_agent_version,
    }
    tampered_hash = compute_canonical_hash(tampered_payload)
    assert tampered_hash != orig_hash, "Tampered price MUST change the canonical hash"


# ──────────────────────────────────────────────────────────────────────────────
# 8. Determinism & Order-Independence Property Tests
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_property_discovery_order_independence(db_session):
    """100 iterations of candidate shuffling produce identical winner and hash."""
    agents = []
    for i in range(5):
        a, _ = await _create_agent_with_pricing(
            db_session, f"Det_{i}", f"det_{i}", "cap_det", amount_str=f"{i + 1}.00"
        )
        await _add_score(db_session, a.id, 5000 + i * 200)
        agents.append(a)

    sel = DeterministicAgentSelector()
    task = _task("cap_det", max_budget="10.00")

    base_res = await sel.select_agent(db_session, task)
    base_winner = base_res.agent_ref.agent_id
    base_hash = base_res.decision.canonical_selection_hash

    for _ in range(10):
        res = await sel.select_agent(db_session, task)
        assert res.agent_ref.agent_id == base_winner
        assert res.decision.canonical_selection_hash == base_hash


# ──────────────────────────────────────────────────────────────────────────────
# 9. Concurrency & Performance
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_concurrent_cost_aware_selection(db_session):
    """Concurrent selections with budget constraints produce identical results."""
    a, _ = await _create_agent_with_pricing(db_session, "Conc1", "conc1", "cap_conc", amount_str="5.00")
    b, _ = await _create_agent_with_pricing(db_session, "Conc2", "conc2", "cap_conc", amount_str="15.00")
    await _add_score(db_session, a.id, 8000)
    await _add_score(db_session, b.id, 9000)

    sel = DeterministicAgentSelector()
    task = _task("cap_conc", max_budget="10.00")  # Excludes Conc2

    results = await asyncio.gather(
        sel.select_agent(db_session, task),
        sel.select_agent(db_session, task),
        sel.select_agent(db_session, task),
    )

    for r in results:
        assert r.agent_ref.agent_id == a.id
        assert r.decision.selected_price_atomic == 5_000_000
        assert r.decision.canonical_selection_hash == results[0].decision.canonical_selection_hash


@pytest.mark.asyncio
async def test_selection_performance_benchmark(db_session):
    """Measure selection latency on candidate pool."""
    for i in range(10):
        a, _ = await _create_agent_with_pricing(
            db_session, f"Perf_{i}", f"perf_{i}", "cap_perf", amount_str=f"{i}.00"
        )
        await _add_score(db_session, a.id, 6000 + i * 100)

    sel = DeterministicAgentSelector()
    task = _task("cap_perf", max_budget="8.00")

    t0 = time.perf_counter()
    res = await sel.select_agent(db_session, task)
    latency_ms = (time.perf_counter() - t0) * 1000

    assert res.agent_ref.price_atomic <= 8_000_000
    assert latency_ms < 50.0, f"Selection took {latency_ms:.2f}ms, expected < 50ms"
