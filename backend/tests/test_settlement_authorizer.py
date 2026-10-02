"""Tests for SettlementAuthorizationEngine and policy evaluation."""

from datetime import datetime, timezone
import uuid
import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import AgentExecution, ExecutionStatus
from app.models.blockchain import EscrowChainState, EventStatus
from app.models.orchestration import (
    Orchestration,
    OrchestrationStatus,
    OrchestrationTask,
    OrchestrationTaskStatus,
)
from app.models.settlement import Settlement, SettlementAction, SettlementStatus
from app.models.user import User, UserRole
from app.services.blockchain.config import CHAIN_ID_ANVIL, get_chain_config
from app.services.settlement.authorizer import SettlementAuthorizationEngine
from app.services.settlement.errors import (
    SettlementAuthorizationError,
    SettlementOrchestrationMismatchError,
)
from app.services.settlement.verifier import (
    EscrowVerificationBoundary,
    STATE_CREATED,
    STATE_FUNDED,
    STATE_LOCKED,
    STATE_DISPUTED,
)


from eth_account import Account


@pytest.fixture
def test_addresses():
    return {
        "client": Account.create().address,
        "beneficiary": Account.create().address,
        "arbitrator": Account.create().address,
        "unauthorized": Account.create().address,
    }


@pytest.mark.asyncio
async def test_release_authorization_flow(db_session: AsyncSession, test_addresses):
    """Tests release authorization rules: client/arbitrator allowed, beneficiary unilaterally rejected."""
    cfg = get_chain_config(CHAIN_ID_ANVIL)
    escrow_id = "0x" + uuid.uuid4().hex + uuid.uuid4().hex

    # Escrow in LOCKED state
    record = EscrowChainState(
        chain_id=CHAIN_ID_ANVIL,
        escrow_id=escrow_id,
        contract_address=cfg.contract_addresses["Escrow"],
        client=test_addresses["client"],
        developer=test_addresses["beneficiary"],
        amount=5_000_000,
        reference_id="0x" + "01" * 32,
        current_chain_state=STATE_LOCKED,
        creation_tx_hash="0x" + "02" * 32,
        latest_tx_hash="0x" + "02" * 32,
        is_canonical=True,
        confirmation_status=EventStatus.CONFIRMED.value,
    )
    db_session.add(record)
    await db_session.flush()

    settlement = Settlement(
        idempotency_key="settlement:31337:c1:release:1",
        chain_id=CHAIN_ID_ANVIL,
        escrow_contract=cfg.contract_addresses["Escrow"].lower(),
        escrow_id=escrow_id,
        client_address=test_addresses["client"].lower(),
        beneficiary_address=test_addresses["beneficiary"].lower(),
        token_address=cfg.token_address.lower(),
        amount=5_000_000,
        action=SettlementAction.RELEASE.value,
        status=SettlementStatus.PENDING_AUTHORIZATION.value,
    )
    db_session.add(settlement)
    await db_session.flush()

    escrow_res = await EscrowVerificationBoundary.verify_escrow_for_settlement(
        session=db_session,
        chain_id=CHAIN_ID_ANVIL,
        escrow_id=escrow_id,
        action=SettlementAction.RELEASE,
    )

    # 1. Beneficiary cannot unilaterally release
    beneficiary_user = User(wallet_address=test_addresses["beneficiary"], primary_role=UserRole.DEVELOPER.value)
    with pytest.raises(SettlementAuthorizationError) as exc:
        await SettlementAuthorizationEngine.evaluate_authorization(
            session=db_session,
            settlement=settlement,
            escrow_result=escrow_res,
            caller_user=beneficiary_user,
        )
    assert "Beneficiary cannot unilaterally authorize" in str(exc.value)

    # 2. Unauthorized caller rejected
    rando_user = User(wallet_address=test_addresses["unauthorized"], primary_role=UserRole.CLIENT.value)
    with pytest.raises(SettlementAuthorizationError) as exc:
        await SettlementAuthorizationEngine.evaluate_authorization(
            session=db_session,
            settlement=settlement,
            escrow_result=escrow_res,
            caller_user=rando_user,
        )
    assert "not authorized to release" in str(exc.value)

    # 3. Client authorized
    client_user = User(wallet_address=test_addresses["client"], primary_role=UserRole.CLIENT.value)
    decision = await SettlementAuthorizationEngine.evaluate_authorization(
        session=db_session,
        settlement=settlement,
        escrow_result=escrow_res,
        caller_user=client_user,
    )
    assert decision.is_authorized is True
    assert decision.target_operation == "releaseEscrow"
    assert decision.parameters["escrowId"] == escrow_id

    # 4. Admin authorized
    admin_user = User(wallet_address=test_addresses["arbitrator"], primary_role=UserRole.ADMIN.value)
    decision_admin = await SettlementAuthorizationEngine.evaluate_authorization(
        session=db_session,
        settlement=settlement,
        escrow_result=escrow_res,
        caller_user=admin_user,
    )
    assert decision_admin.is_authorized is True


@pytest.mark.asyncio
async def test_refund_authorization_flow(db_session: AsyncSession, test_addresses):
    """Tests refund authorization rules under deadline constraints and forfeiture."""
    cfg = get_chain_config(CHAIN_ID_ANVIL)
    escrow_id = "0x" + uuid.uuid4().hex + uuid.uuid4().hex
    future_deadline = int(datetime.now(timezone.utc).timestamp()) + 3600
    past_deadline = int(datetime.now(timezone.utc).timestamp()) - 3600

    # Escrow in LOCKED state with future deadline
    record = EscrowChainState(
        chain_id=CHAIN_ID_ANVIL,
        escrow_id=escrow_id,
        contract_address=cfg.contract_addresses["Escrow"],
        client=test_addresses["client"],
        developer=test_addresses["beneficiary"],
        amount=5_000_000,
        reference_id="0x" + "01" * 32,
        current_chain_state=STATE_LOCKED,
        creation_tx_hash="0x" + "02" * 32,
        latest_tx_hash="0x" + "02" * 32,
        is_canonical=True,
        confirmation_status=EventStatus.CONFIRMED.value,
    )
    db_session.add(record)
    await db_session.flush()

    settlement = Settlement(
        idempotency_key="settlement:31337:c2:refund:1",
        chain_id=CHAIN_ID_ANVIL,
        escrow_contract=cfg.contract_addresses["Escrow"].lower(),
        escrow_id=escrow_id,
        client_address=test_addresses["client"].lower(),
        beneficiary_address=test_addresses["beneficiary"].lower(),
        token_address=cfg.token_address.lower(),
        amount=5_000_000,
        action=SettlementAction.REFUND.value,
        status=SettlementStatus.PENDING_AUTHORIZATION.value,
        metadata_json={"executionDeadline": future_deadline},
    )
    db_session.add(settlement)
    await db_session.flush()

    escrow_res = await EscrowVerificationBoundary.verify_escrow_for_settlement(
        session=db_session,
        chain_id=CHAIN_ID_ANVIL,
        escrow_id=escrow_id,
        action=SettlementAction.REFUND,
    )

    client_user = User(wallet_address=test_addresses["client"], primary_role=UserRole.CLIENT.value)
    beneficiary_user = User(wallet_address=test_addresses["beneficiary"], primary_role=UserRole.DEVELOPER.value)

    # 1. Client refund fails before deadline passes
    with pytest.raises(SettlementAuthorizationError) as exc:
        await SettlementAuthorizationEngine.evaluate_authorization(
            session=db_session,
            settlement=settlement,
            escrow_result=escrow_res,
            caller_user=client_user,
        )
    assert "deadline" in str(exc.value).lower()

    # 2. Beneficiary forfeiture succeeds even before deadline
    dec_forfeit = await SettlementAuthorizationEngine.evaluate_authorization(
        session=db_session,
        settlement=settlement,
        escrow_result=escrow_res,
        caller_user=beneficiary_user,
    )
    assert dec_forfeit.is_authorized is True
    assert dec_forfeit.target_operation == "refundEscrow"

    # 3. Client refund succeeds once deadline has passed
    settlement.metadata_json = {"executionDeadline": past_deadline}
    dec_passed = await SettlementAuthorizationEngine.evaluate_authorization(
        session=db_session,
        settlement=settlement,
        escrow_result=escrow_res,
        caller_user=client_user,
    )
    assert dec_passed.is_authorized is True


@pytest.mark.asyncio
async def test_dispute_resolution_authorization(db_session: AsyncSession, test_addresses):
    """Tests dispute resolution authorization: arbitrator only, state must be DISPUTED, amount sum check."""
    cfg = get_chain_config(CHAIN_ID_ANVIL)
    escrow_id = "0x" + uuid.uuid4().hex + uuid.uuid4().hex

    # Escrow in DISPUTED state
    record = EscrowChainState(
        chain_id=CHAIN_ID_ANVIL,
        escrow_id=escrow_id,
        contract_address=cfg.contract_addresses["Escrow"],
        client=test_addresses["client"],
        developer=test_addresses["beneficiary"],
        amount=10_000_000,
        reference_id="0x" + "01" * 32,
        current_chain_state=STATE_DISPUTED,
        creation_tx_hash="0x" + "02" * 32,
        latest_tx_hash="0x" + "02" * 32,
        is_canonical=True,
        confirmation_status=EventStatus.CONFIRMED.value,
    )
    db_session.add(record)
    await db_session.flush()

    settlement = Settlement(
        idempotency_key=f"settlement:31337:{escrow_id}:dispute_split:1",
        chain_id=CHAIN_ID_ANVIL,
        escrow_contract=cfg.contract_addresses["Escrow"].lower(),
        escrow_id=escrow_id,
        client_address=test_addresses["client"].lower(),
        beneficiary_address=test_addresses["beneficiary"].lower(),
        token_address=cfg.token_address.lower(),
        amount=10_000_000,
        action=SettlementAction.DISPUTE_RESOLVE_RELEASE.value,
        status=SettlementStatus.PENDING_AUTHORIZATION.value,
        beneficiary_amount=7_000_000,
        client_refund_amount=3_000_000,
    )
    db_session.add(settlement)
    await db_session.flush()

    escrow_res = await EscrowVerificationBoundary.verify_escrow_for_settlement(
        session=db_session,
        chain_id=CHAIN_ID_ANVIL,
        escrow_id=escrow_id,
        action=SettlementAction.DISPUTE_RESOLVE_RELEASE,
    )

    client_user = User(wallet_address=test_addresses["client"], primary_role=UserRole.CLIENT.value)
    admin_user = User(wallet_address=test_addresses["arbitrator"], primary_role=UserRole.ADMIN.value)

    # 1. Client cannot authorize dispute resolution
    with pytest.raises(SettlementAuthorizationError) as exc:
        await SettlementAuthorizationEngine.evaluate_authorization(
            session=db_session,
            settlement=settlement,
            escrow_result=escrow_res,
            caller_user=client_user,
        )
    assert "arbitrator" in str(exc.value).lower()

    # 2. Arbitrator authorizes split
    dec = await SettlementAuthorizationEngine.evaluate_authorization(
        session=db_session,
        settlement=settlement,
        escrow_result=escrow_res,
        caller_user=admin_user,
    )
    assert dec.is_authorized is True
    assert dec.target_operation == "resolveDispute"
    assert dec.parameters["beneficiaryAmount"] == 7_000_000
    assert dec.parameters["clientRefundAmount"] == 3_000_000

    # 3. Sum mismatch fails closed
    settlement.client_refund_amount = 4_000_000  # 7m + 4m = 11m != 10m
    with pytest.raises(SettlementAuthorizationError) as exc:
        await SettlementAuthorizationEngine.evaluate_authorization(
            session=db_session,
            settlement=settlement,
            escrow_result=escrow_res,
            caller_user=admin_user,
        )
    assert "does not equal total escrow amount" in str(exc.value)


@pytest.mark.asyncio
async def test_orchestration_verification_integration(db_session: AsyncSession, test_addresses):
    """Verifies that release requires linked orchestration and tasks to be in terminal SUCCEEDED status."""
    cfg = get_chain_config(CHAIN_ID_ANVIL)
    escrow_id = "0x" + uuid.uuid4().hex + uuid.uuid4().hex
    orch_wallet = Account.create().address

    # Create user and orchestration
    client_user = User(wallet_address=orch_wallet, primary_role=UserRole.CLIENT.value)
    db_session.add(client_user)
    await db_session.flush()

    orch = Orchestration(
        requested_by=client_user.id,
        goal="Process AI Workflow",
        status=OrchestrationStatus.RUNNING.value,  # Still running!
    )
    db_session.add(orch)
    await db_session.flush()

    # Escrow in LOCKED state
    record = EscrowChainState(
        chain_id=CHAIN_ID_ANVIL,
        escrow_id=escrow_id,
        contract_address=cfg.contract_addresses["Escrow"],
        client=orch_wallet,
        developer=test_addresses["beneficiary"],
        amount=5_000_000,
        reference_id="0x" + "01" * 32,
        current_chain_state=STATE_LOCKED,
        creation_tx_hash="0x" + "02" * 32,
        latest_tx_hash="0x" + "02" * 32,
        is_canonical=True,
        confirmation_status=EventStatus.CONFIRMED.value,
    )
    db_session.add(record)
    await db_session.flush()

    settlement = Settlement(
        idempotency_key=f"settlement:31337:{escrow_id}:release:1",
        chain_id=CHAIN_ID_ANVIL,
        escrow_contract=cfg.contract_addresses["Escrow"].lower(),
        escrow_id=escrow_id,
        client_address=orch_wallet.lower(),
        beneficiary_address=test_addresses["beneficiary"].lower(),
        token_address=cfg.token_address.lower(),
        amount=5_000_000,
        action=SettlementAction.RELEASE.value,
        status=SettlementStatus.PENDING_AUTHORIZATION.value,
        orchestration_id=orch.id,
    )
    db_session.add(settlement)
    await db_session.flush()

    escrow_res = await EscrowVerificationBoundary.verify_escrow_for_settlement(
        session=db_session,
        chain_id=CHAIN_ID_ANVIL,
        escrow_id=escrow_id,
        action=SettlementAction.RELEASE,
    )

    # 1. Release fails while orchestration is RUNNING
    with pytest.raises(SettlementOrchestrationMismatchError) as exc:
        await SettlementAuthorizationEngine.evaluate_authorization(
            session=db_session,
            settlement=settlement,
            escrow_result=escrow_res,
            caller_user=client_user,
        )
    assert "required 'SUCCEEDED'" in str(exc.value)

    # 2. Advance orchestration to SUCCEEDED with pending task -> fails
    from app.models.agent import Agent
    agent = Agent(
        name="Test Agent",
        slug=f"test-agent-{uuid.uuid4().hex[:8]}",
        owner_user_id=client_user.id,
    )
    db_session.add(agent)
    await db_session.flush()

    orch.status = OrchestrationStatus.SUCCEEDED.value
    task = OrchestrationTask(
        orchestration_id=orch.id,
        task_key="step-1",
        agent_id=agent.id,
        agent_version="1.0.0",
        input_hash="hash_123",
        status=OrchestrationTaskStatus.RUNNING.value,
    )
    db_session.add(task)
    await db_session.flush()

    with pytest.raises(SettlementOrchestrationMismatchError) as exc:
        await SettlementAuthorizationEngine.evaluate_authorization(
            session=db_session,
            settlement=settlement,
            escrow_result=escrow_res,
            caller_user=client_user,
        )
    assert "not SUCCEEDED/SKIPPED" in str(exc.value)

    # 3. Advance task to SUCCEEDED -> release succeeds!
    task.status = OrchestrationTaskStatus.SUCCEEDED.value
    await db_session.flush()

    dec = await SettlementAuthorizationEngine.evaluate_authorization(
        session=db_session,
        settlement=settlement,
        escrow_result=escrow_res,
        caller_user=client_user,
    )
    assert dec.is_authorized is True
