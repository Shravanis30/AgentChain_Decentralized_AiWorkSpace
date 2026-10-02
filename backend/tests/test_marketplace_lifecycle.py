"""Phase 6.8 — Marketplace Execution & Escrow Lifecycle Integration Tests.

Comprehensive integration and verification suite for:
Client Goal
-> Candidate Discovery
-> Reputation-Aware Selection
-> Budget/Price Validation
-> Exact Agent + Version + Price Pinning
-> Escrow Authorization/Creation
-> Agent Execution
-> Result
-> Cryptographic Notarization
-> Verified Outcome
-> Settlement Decision
-> Escrow Release / Refund / Dispute
-> Revenue Distribution (85/10/5)
-> Reputation Evidence
"""

import asyncio
import random
import uuid
import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession
from web3 import Web3

from app.models.agent import Agent, AgentCapability, AgentExecution, AgentStatus, AgentVersion, ExecutionStatus
from app.models.agent_selection import SelectionDecision
from app.models.blockchain import (
    BlockchainTransactionIntent,
    EscrowChainState,
    EventStatus,
    IntentStatus,
)
from app.models.distribution import Distribution, DistributionStatus
from app.models.marketplace import MarketplaceLifecycleStatus, MarketplaceOrder
from app.models.notarization import NotarizationStatus, ResultNotarization
from app.models.reputation import ReputationEvent, ReputationOutcomeType, ReputationStatus
from app.models.reputation_scoring import ReputationPolicyVersion, ReputationScore
from app.models.settlement import Settlement, SettlementAction, SettlementStatus
from app.models.user import User, UserRole
from app.services.agent_selector import DeterministicAgentSelector
from app.services.blockchain.config import CHAIN_ID_ANVIL, get_chain_config
from app.services.distribution.config import (
    compute_85_10_5_split,
)
from app.services.hashing import compute_canonical_hash
from app.services.marketplace import (
    MarketplaceAuthorizationError,
    MarketplaceEscrowMismatchError,
    MarketplaceEscrowUnconfirmedError,
    MarketplaceOrderingViolationError,
    MarketplaceOrderNotFoundError,
    MarketplaceStateConflictError,
    compute_deterministic_escrow_id,
    marketplace_coordinator,
)


@pytest.fixture
async def seeded_marketplace_environment(db_session: AsyncSession):
    """Sets up a complete marketplace environment: users, agents, pricing, reputation."""
    # 1. Reputation Policy
    policy = (await db_session.execute(
        sa.select(ReputationPolicyVersion).where(
            ReputationPolicyVersion.policy_name == "ReputationPolicyV1",
            ReputationPolicyVersion.version_number == 1,
        )
    )).scalars().first()
    if not policy:
        policy = ReputationPolicyVersion(
            policy_name="ReputationPolicyV1",
            version_number=1,
            description="Phase 6.8 Environment",
            formula_json={},
            is_active=True,
        )
        db_session.add(policy)
        await db_session.flush()

    # 2. Client and Developer Users
    client_wallet = f"0x{uuid.uuid4().hex}{uuid.uuid4().hex[:8]}"
    dev_wallet = f"0x{uuid.uuid4().hex}{uuid.uuid4().hex[:8]}"
    arbitrator_wallet = f"0x{uuid.uuid4().hex}{uuid.uuid4().hex[:8]}"

    client_user = User(
        wallet_address=client_wallet,
        primary_role=UserRole.CLIENT.value,
    )
    dev_user = User(
        wallet_address=dev_wallet,
        primary_role=UserRole.DEVELOPER.value,
    )
    arbitrator_user = User(
        wallet_address=arbitrator_wallet,
        primary_role=UserRole.ADMIN.value,
    )
    db_session.add_all([client_user, dev_user, arbitrator_user])
    await db_session.flush()

    # 3. Create Published Agent with Version & Pricing
    agent = Agent(
        owner_user_id=dev_user.id,
        name="Alpha Research Agent",
        slug=f"alpha-agent-{uuid.uuid4().hex[:8]}",
        description="Autonomous data synthesis agent",
        status=AgentStatus.PUBLISHED.value,
        is_active=True,
    )
    db_session.add(agent)
    await db_session.flush()

    version = AgentVersion(
        agent_id=agent.id,
        version="1.0.0",
        manifest={"title": "Alpha Research v1", "capabilities": ["research"]},
        input_schema={"type": "object", "properties": {"query": {"type": "string"}}},
        output_schema={"type": "object", "properties": {"report": {"type": "string"}}},
        pricing_config={"model": "fixed", "price": "2.50", "currency": "USDC", "price_atomic": 2_500_000},
        verification_config={},
    )
    db_session.add(version)
    await db_session.flush()

    agent.current_version_id = version.id
    cap = AgentCapability(agent_id=agent.id, capability="research")
    db_session.add(cap)

    # 4. Canonical Reputation Score
    score = ReputationScore(
        agent_id=agent.id,
        chain_id=CHAIN_ID_ANVIL,
        policy_version_id=policy.id,
        policy_version="ReputationPolicyV1",
        score_scaled=8500,
        total_verified_executions=10,
        verified_successes=8,
        verified_failures=2,
        verified_timeouts=0,
        verified_cancellations=0,
        evidence_set_hash="a1" * 32,
    )
    db_session.add(score)
    await db_session.flush()

    return {
        "client": client_user,
        "developer": dev_user,
        "arbitrator": arbitrator_user,
        "agent": agent,
        "version": version,
        "policy": policy,
        "score": score,
    }


# ==============================================================================
# 1. Happy Path: Complete Marketplace Lifecycle
# ==============================================================================

@pytest.mark.asyncio
async def test_marketplace_complete_happy_path(
    db_session: AsyncSession,
    seeded_marketplace_environment,
    monkeypatch,
):
    """Proves the full authoritative marketplace lifecycle:
    Client Goal -> Selection -> Price Lock -> Escrow Creation -> Confirmed Escrow ->
    Execution -> Result -> Notarization -> Verified Success -> Settlement Release ->
    85/10/5 Revenue Distribution -> Reputation Evidence.
    """
    env = seeded_marketplace_environment
    client = env["client"]
    dev = env["developer"]
    agent = env["agent"]

    # --- 1. Order Creation & Deterministic Selection ---
    idempotency_key = f"mkt-order-{uuid.uuid4()}"
    task_input = {"query": "Analyze market trends"}
    order, was_created = await marketplace_coordinator.create_order(
        db=db_session,
        client_user=client,
        goal="Produce comprehensive market analysis",
        task_input=task_input,
        chain_id=CHAIN_ID_ANVIL,
        idempotency_key=idempotency_key,
        preferred_agent_id=agent.id,
        required_capabilities=["research"],
        max_budget_atomic=5_000_000,
    )
    assert was_created is True
    assert order.status == MarketplaceLifecycleStatus.PRICE_LOCKED.value
    assert order.selected_agent_id == agent.id
    assert order.selected_agent_version == "1.0.0"
    assert order.pinned_price_atomic == 2_500_000
    assert order.total_escrow_atomic == 2_500_000
    assert order.escrow_id is not None

    # --- 2. Escrow Funding & Canonical Confirmation Simulation ---
    cfg = get_chain_config(CHAIN_ID_ANVIL)
    escrow_chain_state = EscrowChainState(
        chain_id=CHAIN_ID_ANVIL,
        escrow_id=order.escrow_id.lower(),
        contract_address=cfg.contract_addresses["Escrow"].lower(),
        client=client.wallet_address.lower(),
        developer=dev.wallet_address.lower(),
        amount=order.total_escrow_atomic,
        reference_id=order.escrow_reference_id,
        current_chain_state=1,  # FUNDED
        creation_tx_hash="0x" + "aa" * 32,
        latest_tx_hash="0x" + "aa" * 32,
        is_canonical=True,
        confirmation_status=EventStatus.CONFIRMED.value,  # Depth >= 32
    )
    db_session.add(escrow_chain_state)
    await db_session.flush()

    # --- 3. Verify Escrow ---
    verified_order = await marketplace_coordinator.verify_and_bind_escrow(db_session, order.id)
    assert verified_order.status == MarketplaceLifecycleStatus.ESCROW_FUNDED.value

    # --- 4. Enqueue Execution ---
    execution = await marketplace_coordinator.enqueue_execution(db_session, order.id)
    assert execution.agent_id == agent.id
    assert execution.status == ExecutionStatus.QUEUED.value
    assert order.status == MarketplaceLifecycleStatus.EXECUTION_QUEUED.value

    # --- 5. Simulate Worker Execution Deliverable ---
    output = {"report": "Market trends indicate upward momentum in verified AI agents"}
    execution.status = ExecutionStatus.SUCCEEDED.value
    execution.output_data = output
    execution.output_hash = compute_canonical_hash(output)
    await db_session.flush()

    await marketplace_coordinator.handle_execution_update(db_session, order.id)
    assert order.status == MarketplaceLifecycleStatus.RESULT_AVAILABLE.value

    # --- 6. Cryptographic Notarization & Verified Reputation ---
    # Mock transaction intent creation for Notarization and Reputation
    from app.services.blockchain.transaction_intent import TransactionIntentService
    async def mock_create_intent(*args, **kwargs):
        intent = BlockchainTransactionIntent(
            idempotency_key=f"intent-{uuid.uuid4()}",
            chain_id=CHAIN_ID_ANVIL,
            target_contract="0x" + "33" * 20,
            operation="notarizeResult",
            parameters={},
            status=IntentStatus.CONFIRMED.value,
        )
        db_session.add(intent)
        await db_session.flush()
        return intent, True

    monkeypatch.setattr(TransactionIntentService, "create_intent", mock_create_intent)

    notarization, rep_event = await marketplace_coordinator.notarize_and_verify_outcome(db_session, order.id)
    assert notarization is not None
    assert notarization.result_hash == execution.output_hash
    assert rep_event.outcome_type == ReputationOutcomeType.VERIFIED_SUCCESS.value
    assert order.status == MarketplaceLifecycleStatus.OUTCOME_VERIFIED.value

    # --- 7. Settlement Authorization (RELEASE) ---
    settlement = await marketplace_coordinator.initiate_settlement(
        db_session, order.id, actor_user=client
    )
    assert settlement.action == SettlementAction.RELEASE.value
    assert settlement.status == SettlementStatus.AUTHORIZED.value
    assert order.status == MarketplaceLifecycleStatus.SETTLEMENT_PENDING.value

    # --- 8. Confirmed Settlement & 85/10/5 Revenue Distribution ---
    settlement.status = SettlementStatus.CONFIRMED.value
    await db_session.flush()

    finalized_order = await marketplace_coordinator.finalize_settlement_and_distribution(
        db_session, order.id
    )
    assert finalized_order.status == MarketplaceLifecycleStatus.SETTLED.value
    assert finalized_order.distribution_id is not None
    assert finalized_order.completed_at is not None

    # Verify exact 85/10/5 economic split
    dist = await db_session.get(Distribution, finalized_order.distribution_id)
    assert dist is not None
    gross = order.total_escrow_atomic
    expected_dev, expected_staker, expected_dao = compute_85_10_5_split(int(gross))
    assert dist.gross_amount == gross
    assert dist.developer_amount == expected_dev
    assert dist.staker_amount == expected_staker
    assert dist.dao_amount == expected_dao
    assert dist.developer_amount + dist.staker_amount + dist.dao_amount == gross


# ==============================================================================
# 2. Failure Path: Execution Failure -> Escrow Refund
# ==============================================================================

@pytest.mark.asyncio
async def test_marketplace_failure_refund_path(
    db_session: AsyncSession,
    seeded_marketplace_environment,
    monkeypatch,
):
    """Execution fails -> Verified failure -> Escrow refunded to client."""
    env = seeded_marketplace_environment
    client = env["client"]
    dev = env["developer"]
    agent = env["agent"]
    order, _ = await marketplace_coordinator.create_order(
        db=db_session,
        client_user=client,
        goal="Run complex task",
        task_input={"query": "test"},
        chain_id=CHAIN_ID_ANVIL,
        idempotency_key=f"mkt-fail-{uuid.uuid4()}",
        preferred_agent_id=agent.id,
    )

    # Seed confirmed funded escrow
    cfg = get_chain_config(CHAIN_ID_ANVIL)
    escrow = EscrowChainState(
        chain_id=CHAIN_ID_ANVIL,
        escrow_id=order.escrow_id.lower(),
        contract_address=cfg.contract_addresses["Escrow"].lower(),
        client=client.wallet_address.lower(),
        developer=dev.wallet_address.lower(),
        amount=order.total_escrow_atomic,
        reference_id=order.escrow_reference_id,
        current_chain_state=1,
        creation_tx_hash="0x" + "bb" * 32,
        latest_tx_hash="0x" + "bb" * 32,
        is_canonical=True,
        confirmation_status=EventStatus.CONFIRMED.value,
    )
    db_session.add(escrow)
    await db_session.flush()

    await marketplace_coordinator.verify_and_bind_escrow(db_session, order.id)
    execution = await marketplace_coordinator.enqueue_execution(db_session, order.id)

    # Worker marks FAILED
    execution.status = ExecutionStatus.FAILED.value
    execution.error_code = "TASK_EXECUTION_ERROR"
    execution.error_message = "Out of memory"
    await db_session.flush()

    await marketplace_coordinator.handle_execution_update(db_session, order.id)
    assert order.status == MarketplaceLifecycleStatus.FAILED.value

    # Outcome verification creates VERIFIED_FAILURE reputation event
    from app.services.blockchain.transaction_intent import TransactionIntentService
    async def mock_create_intent(*args, **kwargs):
        intent = BlockchainTransactionIntent(
            idempotency_key=f"intent-{uuid.uuid4()}",
            chain_id=CHAIN_ID_ANVIL,
            target_contract="0x" + "33" * 20,
            operation="refundEscrow",
            parameters={},
            status=IntentStatus.CONFIRMED.value,
        )
        db_session.add(intent)
        await db_session.flush()
        return intent, True

    monkeypatch.setattr(TransactionIntentService, "create_intent", mock_create_intent)

    _, rep_event = await marketplace_coordinator.notarize_and_verify_outcome(db_session, order.id)
    assert rep_event.outcome_type == ReputationOutcomeType.VERIFIED_FAILURE.value
    assert order.status == MarketplaceLifecycleStatus.OUTCOME_VERIFIED.value

    # Settlement initiates REFUND
    settlement = await marketplace_coordinator.initiate_settlement(db_session, order.id, actor_user=client)
    assert settlement.action == SettlementAction.REFUND.value

    settlement.status = SettlementStatus.CONFIRMED.value
    await db_session.flush()

    finalized = await marketplace_coordinator.finalize_settlement_and_distribution(db_session, order.id)
    assert finalized.status == MarketplaceLifecycleStatus.REFUNDED.value
    assert finalized.distribution_id is None  # No revenue distribution on refund


# ==============================================================================
# 3. Timeout Path: Neutral Reputation & Refund
# ==============================================================================

@pytest.mark.asyncio
async def test_marketplace_timeout_neutral_reputation_refund(
    db_session: AsyncSession,
    seeded_marketplace_environment,
    monkeypatch,
):
    """Timeout -> VERIFIED_TIMEOUT (neutral score) -> Escrow Refund."""
    env = seeded_marketplace_environment
    client = env["client"]
    dev = env["developer"]
    agent = env["agent"]
    order, _ = await marketplace_coordinator.create_order(
        db=db_session,
        client_user=client,
        goal="Long task",
        task_input={"query": "test"},
        chain_id=CHAIN_ID_ANVIL,
        idempotency_key=f"mkt-timeout-{uuid.uuid4()}",
        preferred_agent_id=agent.id,
    )

    cfg = get_chain_config(CHAIN_ID_ANVIL)
    escrow = EscrowChainState(
        chain_id=CHAIN_ID_ANVIL,
        escrow_id=order.escrow_id.lower(),
        contract_address=cfg.contract_addresses["Escrow"].lower(),
        client=client.wallet_address.lower(),
        developer=dev.wallet_address.lower(),
        amount=order.total_escrow_atomic,
        reference_id=order.escrow_reference_id,
        current_chain_state=1,
        creation_tx_hash="0x" + "cc" * 32,
        latest_tx_hash="0x" + "cc" * 32,
        is_canonical=True,
        confirmation_status=EventStatus.CONFIRMED.value,
    )
    db_session.add(escrow)
    await db_session.flush()

    await marketplace_coordinator.verify_and_bind_escrow(db_session, order.id)
    execution = await marketplace_coordinator.enqueue_execution(db_session, order.id)

    # Worker marks TIMED_OUT
    execution.status = ExecutionStatus.TIMED_OUT.value
    await db_session.flush()

    await marketplace_coordinator.handle_execution_update(db_session, order.id)
    assert order.status == MarketplaceLifecycleStatus.TIMED_OUT.value

    from app.services.blockchain.transaction_intent import TransactionIntentService
    async def mock_create_intent(*args, **kwargs):
        intent = BlockchainTransactionIntent(
            idempotency_key=f"intent-{uuid.uuid4()}",
            chain_id=CHAIN_ID_ANVIL,
            target_contract="0x" + "33" * 20,
            operation="refundEscrow",
            parameters={},
            status=IntentStatus.CONFIRMED.value,
        )
        db_session.add(intent)
        await db_session.flush()
        return intent, True

    monkeypatch.setattr(TransactionIntentService, "create_intent", mock_create_intent)

    _, rep_event = await marketplace_coordinator.notarize_and_verify_outcome(db_session, order.id)
    assert rep_event.outcome_type == ReputationOutcomeType.VERIFIED_TIMEOUT.value

    settlement = await marketplace_coordinator.initiate_settlement(db_session, order.id, actor_user=client)
    assert settlement.action == SettlementAction.REFUND.value

    settlement.status = SettlementStatus.CONFIRMED.value
    await db_session.flush()

    finalized = await marketplace_coordinator.finalize_settlement_and_distribution(db_session, order.id)
    assert finalized.status == MarketplaceLifecycleStatus.REFUNDED.value


# ==============================================================================
# 4. Cancellation Path: Neutral Reputation & Refund
# ==============================================================================

@pytest.mark.asyncio
async def test_marketplace_cancellation_neutral_reputation_refund(
    db_session: AsyncSession,
    seeded_marketplace_environment,
    monkeypatch,
):
    """User cancellation -> VERIFIED_CANCELLATION (neutral) -> Escrow Refund."""
    env = seeded_marketplace_environment
    client = env["client"]
    dev = env["developer"]
    agent = env["agent"]
    order, _ = await marketplace_coordinator.create_order(
        db=db_session,
        client_user=client,
        goal="Cancel task",
        task_input={"query": "test"},
        chain_id=CHAIN_ID_ANVIL,
        idempotency_key=f"mkt-cancel-{uuid.uuid4()}",
        preferred_agent_id=agent.id,
    )

    cfg = get_chain_config(CHAIN_ID_ANVIL)
    escrow = EscrowChainState(
        chain_id=CHAIN_ID_ANVIL,
        escrow_id=order.escrow_id.lower(),
        contract_address=cfg.contract_addresses["Escrow"].lower(),
        client=client.wallet_address.lower(),
        developer=dev.wallet_address.lower(),
        amount=order.total_escrow_atomic,
        reference_id=order.escrow_reference_id,
        current_chain_state=1,
        creation_tx_hash="0x" + "dd" * 32,
        latest_tx_hash="0x" + "dd" * 32,
        is_canonical=True,
        confirmation_status=EventStatus.CONFIRMED.value,
    )
    db_session.add(escrow)
    await db_session.flush()

    await marketplace_coordinator.verify_and_bind_escrow(db_session, order.id)
    execution = await marketplace_coordinator.enqueue_execution(db_session, order.id)

    # Cancel execution
    execution.status = ExecutionStatus.CANCELLED.value
    execution.cancellation_requested = True
    await db_session.flush()

    await marketplace_coordinator.handle_execution_update(db_session, order.id)
    assert order.status == MarketplaceLifecycleStatus.CANCELLED.value

    from app.services.blockchain.transaction_intent import TransactionIntentService
    async def mock_create_intent(*args, **kwargs):
        intent = BlockchainTransactionIntent(
            idempotency_key=f"intent-{uuid.uuid4()}",
            chain_id=CHAIN_ID_ANVIL,
            target_contract="0x" + "33" * 20,
            operation="refundEscrow",
            parameters={},
            status=IntentStatus.CONFIRMED.value,
        )
        db_session.add(intent)
        await db_session.flush()
        return intent, True

    monkeypatch.setattr(TransactionIntentService, "create_intent", mock_create_intent)

    _, rep_event = await marketplace_coordinator.notarize_and_verify_outcome(db_session, order.id)
    assert rep_event.outcome_type == ReputationOutcomeType.VERIFIED_CANCELLATION.value

    settlement = await marketplace_coordinator.initiate_settlement(db_session, order.id, actor_user=client)
    assert settlement.action == SettlementAction.REFUND.value


# ==============================================================================
# 5. Dispute Path: Multisig Arbitration Resolution
# ==============================================================================

@pytest.mark.asyncio
async def test_marketplace_dispute_and_arbitration_resolution(
    db_session: AsyncSession,
    seeded_marketplace_environment,
    monkeypatch,
):
    """Client opens dispute -> Arbitrator awards split -> Conservation holds."""
    env = seeded_marketplace_environment
    client = env["client"]
    dev = env["developer"]
    arbitrator = env["arbitrator"]
    agent = env["agent"]
    order, _ = await marketplace_coordinator.create_order(
        db=db_session,
        client_user=client,
        goal="Dispute test",
        task_input={"query": "test"},
        chain_id=CHAIN_ID_ANVIL,
        idempotency_key=f"mkt-disp-{uuid.uuid4()}",
        preferred_agent_id=agent.id,
    )

    cfg = get_chain_config(CHAIN_ID_ANVIL)
    escrow = EscrowChainState(
        chain_id=CHAIN_ID_ANVIL,
        escrow_id=order.escrow_id.lower(),
        contract_address=cfg.contract_addresses["Escrow"].lower(),
        client=client.wallet_address.lower(),
        developer=dev.wallet_address.lower(),
        amount=order.total_escrow_atomic,
        reference_id=order.escrow_reference_id,
        current_chain_state=1,
        creation_tx_hash="0x" + "ee" * 32,
        latest_tx_hash="0x" + "ee" * 32,
        is_canonical=True,
        confirmation_status=EventStatus.CONFIRMED.value,
    )
    db_session.add(escrow)
    await db_session.flush()

    await marketplace_coordinator.verify_and_bind_escrow(db_session, order.id)

    # Client opens dispute
    disputed_order = await marketplace_coordinator.open_dispute(
        db_session, order.id, dispute_reason="Deliverable quality was non-conforming", claimant_user=client
    )
    assert disputed_order.status == MarketplaceLifecycleStatus.DISPUTED.value
    assert "dispute_reason_hash" in disputed_order.metadata_json

    from app.services.blockchain.transaction_intent import TransactionIntentService
    async def mock_create_intent(*args, **kwargs):
        intent = BlockchainTransactionIntent(
            idempotency_key=f"intent-{uuid.uuid4()}",
            chain_id=CHAIN_ID_ANVIL,
            target_contract="0x" + "33" * 20,
            operation="resolveDispute",
            parameters={},
            status=IntentStatus.CONFIRMED.value,
        )
        db_session.add(intent)
        await db_session.flush()
        return intent, True

    monkeypatch.setattr(TransactionIntentService, "create_intent", mock_create_intent)

    # Escrow is marked disputed on chain
    escrow.current_chain_state = 5  # DISPUTED
    await db_session.flush()

    # Arbitrator resolves dispute with 60% developer, 40% client
    total = int(order.total_escrow_atomic)
    ben_amt = int(total * 0.6)
    client_amt = total - ben_amt

    settlement = await marketplace_coordinator.resolve_dispute(
        db_session,
        order_id=order.id,
        beneficiary_amount=ben_amt,
        client_refund_amount=client_amt,
        arbitrator_user=arbitrator,
        reason="Partial fulfillment substantiated",
    )
    assert settlement.status == SettlementStatus.AUTHORIZED.value
    assert settlement.beneficiary_amount == ben_amt
    assert settlement.client_refund_amount == client_amt


# ==============================================================================
# 6. Safety: Execution Cannot Precede Confirmed Escrow Funding
# ==============================================================================

@pytest.mark.asyncio
async def test_marketplace_ordering_safety_blocks_premature_execution(
    db_session: AsyncSession,
    seeded_marketplace_environment,
):
    """Execution MUST NOT be dispatched before escrow is verified and funded."""
    env = seeded_marketplace_environment
    client = env["client"]

    order, _ = await marketplace_coordinator.create_order(
        db=db_session,
        client_user=client,
        goal="Order before escrow test",
        task_input={"query": "test"},
        chain_id=CHAIN_ID_ANVIL,
        idempotency_key=f"mkt-order-guard-{uuid.uuid4()}",
    )
    assert order.status == MarketplaceLifecycleStatus.PRICE_LOCKED.value

    # Attempting to enqueue execution immediately must fail
    with pytest.raises(MarketplaceOrderingViolationError) as exc_info:
        await marketplace_coordinator.enqueue_execution(db_session, order.id)
    assert "Requires ESCROW_FUNDED" in str(exc_info.value)


# ==============================================================================
# 7. Safety: Escrow Amount Mismatch Fails Verification
# ==============================================================================

@pytest.mark.asyncio
async def test_marketplace_escrow_amount_mismatch_rejected(
    db_session: AsyncSession,
    seeded_marketplace_environment,
):
    """If on-chain escrow amount does not equal pinned price, verification is rejected."""
    env = seeded_marketplace_environment
    client = env["client"]
    dev = env["developer"]
    agent = env["agent"]

    order, _ = await marketplace_coordinator.create_order(
        db=db_session,
        client_user=client,
        goal="Mismatch test",
        task_input={"query": "test"},
        chain_id=CHAIN_ID_ANVIL,
        idempotency_key=f"mkt-mismatch-{uuid.uuid4()}",
        preferred_agent_id=agent.id,
    )

    # Seed escrow with different amount (e.g. 1,000,000 instead of 2,500,000)
    cfg = get_chain_config(CHAIN_ID_ANVIL)
    escrow = EscrowChainState(
        chain_id=CHAIN_ID_ANVIL,
        escrow_id=order.escrow_id.lower(),
        contract_address=cfg.contract_addresses["Escrow"].lower(),
        client=client.wallet_address.lower(),
        developer=dev.wallet_address.lower(),
        amount=1_000_000,  # Incorrect amount!
        reference_id=order.escrow_reference_id,
        current_chain_state=1,
        creation_tx_hash="0x" + "11" * 32,
        latest_tx_hash="0x" + "11" * 32,
        is_canonical=True,
        confirmation_status=EventStatus.CONFIRMED.value,
    )
    db_session.add(escrow)
    await db_session.flush()

    with pytest.raises(MarketplaceEscrowMismatchError) as exc_info:
        await marketplace_coordinator.verify_and_bind_escrow(db_session, order.id)
    assert "does not match pinned order amount" in str(exc_info.value)


# ==============================================================================
# 8. Safety: Escrow Unconfirmed (<32 blocks) Rejected
# ==============================================================================

@pytest.mark.asyncio
async def test_marketplace_escrow_unconfirmed_rejected(
    db_session: AsyncSession,
    seeded_marketplace_environment,
):
    """Escrow with depth < 32 blocks (status != CONFIRMED) cannot be verified."""
    env = seeded_marketplace_environment
    client = env["client"]
    dev = env["developer"]
    agent = env["agent"]

    order, _ = await marketplace_coordinator.create_order(
        db=db_session,
        client_user=client,
        goal="Unconfirmed test",
        task_input={"query": "test"},
        chain_id=CHAIN_ID_ANVIL,
        idempotency_key=f"mkt-unconf-{uuid.uuid4()}",
        preferred_agent_id=agent.id,
    )

    cfg = get_chain_config(CHAIN_ID_ANVIL)
    escrow = EscrowChainState(
        chain_id=CHAIN_ID_ANVIL,
        escrow_id=order.escrow_id.lower(),
        contract_address=cfg.contract_addresses["Escrow"].lower(),
        client=client.wallet_address.lower(),
        developer=dev.wallet_address.lower(),
        amount=order.total_escrow_atomic,
        reference_id=order.escrow_reference_id,
        current_chain_state=1,
        creation_tx_hash="0x" + "22" * 32,
        latest_tx_hash="0x" + "22" * 32,
        is_canonical=True,
        confirmation_status=EventStatus.CONFIRMING.value,  # Not CONFIRMED yet!
    )
    db_session.add(escrow)
    await db_session.flush()

    with pytest.raises(MarketplaceEscrowUnconfirmedError) as exc_info:
        await marketplace_coordinator.verify_and_bind_escrow(db_session, order.id)
    assert "requires 'CONFIRMED'" in str(exc_info.value)


# ==============================================================================
# 9. Safety: Reorg-Orphaned Escrow Rejected
# ==============================================================================

@pytest.mark.asyncio
async def test_marketplace_escrow_reorg_orphaned_rejected(
    db_session: AsyncSession,
    seeded_marketplace_environment,
):
    """Escrow orphaned by chain reorganization (is_canonical=False) is rejected."""
    env = seeded_marketplace_environment
    client = env["client"]
    dev = env["developer"]
    agent = env["agent"]

    order, _ = await marketplace_coordinator.create_order(
        db=db_session,
        client_user=client,
        goal="Reorg test",
        task_input={"query": "test"},
        chain_id=CHAIN_ID_ANVIL,
        idempotency_key=f"mkt-reorg-{uuid.uuid4()}",
        preferred_agent_id=agent.id,
    )

    cfg = get_chain_config(CHAIN_ID_ANVIL)
    escrow = EscrowChainState(
        chain_id=CHAIN_ID_ANVIL,
        escrow_id=order.escrow_id.lower(),
        contract_address=cfg.contract_addresses["Escrow"].lower(),
        client=client.wallet_address.lower(),
        developer=dev.wallet_address.lower(),
        amount=order.total_escrow_atomic,
        reference_id=order.escrow_reference_id,
        current_chain_state=1,
        creation_tx_hash="0x" + "33" * 32,
        latest_tx_hash="0x" + "33" * 32,
        is_canonical=False,  # Orphaned!
        confirmation_status=EventStatus.ORPHANED.value,
    )
    db_session.add(escrow)
    await db_session.flush()

    with pytest.raises(MarketplaceEscrowUnconfirmedError) as exc_info:
        await marketplace_coordinator.verify_and_bind_escrow(db_session, order.id)
    assert "non-canonical or orphaned" in str(exc_info.value)


# ==============================================================================
# 10. Immutability: Post-Selection Price Mutation Does Not Affect Order
# ==============================================================================

@pytest.mark.asyncio
async def test_marketplace_historical_price_pinned_against_mutations(
    db_session: AsyncSession,
    seeded_marketplace_environment,
):
    """If agent updates price in manifest after selection, order retains locked price."""
    env = seeded_marketplace_environment
    client = env["client"]
    version = env["version"]
    agent = env["agent"]

    order, _ = await marketplace_coordinator.create_order(
        db=db_session,
        client_user=client,
        goal="Price pinning test",
        task_input={"query": "test"},
        chain_id=CHAIN_ID_ANVIL,
        idempotency_key=f"mkt-price-pin-{uuid.uuid4()}",
        preferred_agent_id=agent.id,
    )
    original_price = order.pinned_price_atomic

    # Mutate agent version price to 10.00 USDC
    version.pricing_config = {"model": "fixed", "price": "10.00", "currency": "USDC", "price_atomic": 10_000_000}
    await db_session.flush()

    # Re-fetch order: pinned price must NOT change
    refreshed_order = await marketplace_coordinator.get_order(db_session, order.id)
    assert refreshed_order.pinned_price_atomic == original_price
    assert refreshed_order.total_escrow_atomic == original_price


# ==============================================================================
# 11. Immutability: Post-Selection Version Mutation Does Not Affect Execution
# ==============================================================================

@pytest.mark.asyncio
async def test_marketplace_historical_version_pinned_against_mutations(
    db_session: AsyncSession,
    seeded_marketplace_environment,
):
    """If agent publishes v2.0.0 after selection, execution strictly executes pinned v1.0.0."""
    env = seeded_marketplace_environment
    client = env["client"]
    dev = env["developer"]
    agent = env["agent"]

    order, _ = await marketplace_coordinator.create_order(
        db=db_session,
        client_user=client,
        goal="Version pinning test",
        task_input={"query": "test"},
        chain_id=CHAIN_ID_ANVIL,
        idempotency_key=f"mkt-ver-pin-{uuid.uuid4()}",
        preferred_agent_id=agent.id,
    )
    assert order.selected_agent_version == "1.0.0"

    # Agent publishes version 2.0.0
    v2 = AgentVersion(
        agent_id=agent.id,
        version="2.0.0",
        manifest={"title": "Alpha Research v2", "capabilities": ["research"]},
        input_schema={"type": "object"},
        output_schema={"type": "object"},
        pricing_config={"model": "fixed", "price": "5.00", "currency": "USDC", "price_atomic": 5_000_000},
        verification_config={},
    )
    db_session.add(v2)
    agent.current_version_id = v2.id
    await db_session.flush()

    # Fund escrow
    cfg = get_chain_config(CHAIN_ID_ANVIL)
    escrow = EscrowChainState(
        chain_id=CHAIN_ID_ANVIL,
        escrow_id=order.escrow_id.lower(),
        contract_address=cfg.contract_addresses["Escrow"].lower(),
        client=client.wallet_address.lower(),
        developer=dev.wallet_address.lower(),
        amount=order.total_escrow_atomic,
        reference_id=order.escrow_reference_id,
        current_chain_state=1,
        creation_tx_hash="0x" + "44" * 32,
        latest_tx_hash="0x" + "44" * 32,
        is_canonical=True,
        confirmation_status=EventStatus.CONFIRMED.value,
    )
    db_session.add(escrow)
    await db_session.flush()

    await marketplace_coordinator.verify_and_bind_escrow(db_session, order.id)
    execution = await marketplace_coordinator.enqueue_execution(db_session, order.id)

    # Execution must use v1.0.0, NOT v2.0.0
    v1 = env["version"]
    assert execution.agent_version_id == v1.id


# ==============================================================================
# 12. Idempotency: Duplicate Order Creation Returns Existing
# ==============================================================================

@pytest.mark.asyncio
async def test_marketplace_idempotent_order_creation(
    db_session: AsyncSession,
    seeded_marketplace_environment,
):
    """Repeated order requests with identical idempotency key do not duplicate orders."""
    env = seeded_marketplace_environment
    client = env["client"]
    idemp_key = f"mkt-idemp-{uuid.uuid4()}"

    agent = env["agent"]
    order1, was_created1 = await marketplace_coordinator.create_order(
        db=db_session,
        client_user=client,
        goal="Idempotency test",
        task_input={"query": "test"},
        chain_id=CHAIN_ID_ANVIL,
        idempotency_key=idemp_key,
        preferred_agent_id=agent.id,
    )
    assert was_created1 is True

    order2, was_created2 = await marketplace_coordinator.create_order(
        db=db_session,
        client_user=client,
        goal="Idempotency test",
        task_input={"query": "test"},
        chain_id=CHAIN_ID_ANVIL,
        idempotency_key=idemp_key,
        preferred_agent_id=agent.id,
    )
    assert was_created2 is False
    assert order1.id == order2.id


# ==============================================================================
# 13. Security: Unauthorized Dispute Resolution Blocked
# ==============================================================================

@pytest.mark.asyncio
async def test_marketplace_unauthorized_dispute_resolution_rejected(
    db_session: AsyncSession,
    seeded_marketplace_environment,
):
    """Non-arbitrators cannot resolve disputes."""
    env = seeded_marketplace_environment
    client = env["client"]
    dev = env["developer"]

    order, _ = await marketplace_coordinator.create_order(
        db=db_session,
        client_user=client,
        goal="Auth test",
        task_input={"query": "test"},
        chain_id=CHAIN_ID_ANVIL,
        idempotency_key=f"mkt-unauth-{uuid.uuid4()}",
    )
    order.status = MarketplaceLifecycleStatus.DISPUTED.value
    await db_session.flush()

    # Developer (non-arbitrator) attempts to resolve dispute
    with pytest.raises(MarketplaceAuthorizationError) as exc_info:
        await marketplace_coordinator.resolve_dispute(
            db_session,
            order_id=order.id,
            beneficiary_amount=int(order.total_escrow_atomic),
            client_refund_amount=0,
            arbitrator_user=dev,
        )
    assert "Only designated arbitrators" in str(exc_info.value)


# ==============================================================================
# 14. Property / Fuzz Tests (10,000 cases)
# ==============================================================================

def test_property_fuzz_10000_revenue_distribution_conservation():
    """10,000 property fuzz runs verifying exact 85/10/5 integer financial conservation."""
    rng = random.Random(42)
    for _ in range(10_000):
        # Random gross amount up to 100,000 USDC in atomic units
        gross = rng.randint(1, 100_000_000_000)
        dev, staker, dao = compute_85_10_5_split(gross)
        assert dev + staker + dao == gross, f"Invariant violated for gross={gross}"
        assert dev == (gross * 85) // 100
        assert staker == (gross * 10) // 100
        assert dao == gross - dev - staker


def test_property_fuzz_10000_deterministic_escrow_id_collision_resistance():
    """10,000 property fuzz runs verifying deterministic escrow ID collision resistance."""
    rng = random.Random(1337)
    seen_ids = set()
    chain_id = 31337
    contract = "0x5FbDB2315678afecb367f032d93F642f64180aa3"

    for i in range(10_000):
        client = f"0x{rng.randint(1, 2**160 - 1):040x}"
        ref = f"0x{rng.randint(1, 2**256 - 1):064x}"
        salt = rng.randint(0, 100)

        escrow_id = compute_deterministic_escrow_id(
            chain_id=chain_id,
            escrow_contract=contract,
            client_address=client,
            reference_id_hex=ref,
            salt=salt,
        )
        assert escrow_id.startswith("0x")
        assert len(escrow_id) == 66
        assert escrow_id not in seen_ids
        seen_ids.add(escrow_id)
