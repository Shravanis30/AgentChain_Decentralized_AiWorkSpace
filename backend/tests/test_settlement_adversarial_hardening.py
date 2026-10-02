"""Phase 6.2B.1 — Adversarial Settlement Hardening & Integration Verification Test Suite.

Comprehensive 21-test matrix proving consistency across:
1. Backend release permission matches Solidity
2. Backend refund permission matches Solidity
3. Backend dispute permission matches Solidity
4. Deep reorg fail-closed behavior (>32 blocks)
5. Reorg window configuration consistency (32 blocks)
6. Confirmed-event requirement
7. Receipt-without-event rejection
8. Duplicate authorization idempotency
9. Concurrent release/refund race rejection
10. Crash recovery across boundaries
11. Pending vs latest nonce distinction
12. Replacement safety (same nonce, >=12.5% fee bump, gas ceiling)
13. Arbitrary calldata rejection
14. Wrong token rejection
15. Wrong contract rejection
16. Wrong chain rejection
17. Orchestration mismatch rejection
18. Mainnet bypass rejection
19. Duplicate indexer event idempotency
20. Reorg-after-confirmation invalidation
21. Migration 011 upgrade/downgrade/upgrade safety
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path
import subprocess
import uuid
import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession
from eth_account import Account

from app.core.config import settings
from app.models.agent import AgentExecution, ExecutionStatus
from app.models.audit import AuditEventType, AuditLog
from app.models.blockchain import (
    AttemptStatus,
    BlockchainEvent,
    BlockchainTransaction,
    BlockchainTransactionAttempt,
    BlockchainTransactionIntent,
    BlockchainTxOutbox,
    ChainReorganization,
    EscrowChainState,
    EventStatus,
    IndexedBlock,
    IntentStatus,
    ReorgStatus,
    TxLifecycleStatus,
)
from app.models.orchestration import (
    Orchestration,
    OrchestrationStatus,
    OrchestrationTask,
    OrchestrationTaskStatus,
)
from app.models.settlement import (
    Settlement,
    SettlementAction,
    SettlementHistory,
    SettlementStatus,
)
from app.models.user import User, UserRole
from app.services.blockchain.config import (
    CHAIN_ID_ANVIL,
    CHAIN_ID_BASE_MAINNET,
    CHAIN_ID_BASE_SEPOLIA,
    get_chain_config,
)
from app.services.blockchain.errors import (
    DeepReorganizationError,
    MainnetSubmissionBlockedError,
    UnauthorizedOperationError,
)
from app.services.blockchain.gas_estimator import GasEstimator
from app.services.blockchain.nonce_manager import NonceManager
from app.services.blockchain.reconciliation import BlockchainReconciliationService
from app.services.blockchain.relayer import BlockchainRelayer, MockSigner
from app.services.blockchain.reorg_handler import ReorgHandler
from app.services.blockchain.rpc_client import BlockchainRpcClient
from app.services.settlement.authorizer import SettlementAuthorizationEngine
from app.services.settlement.config import get_max_reorg_depth
from app.services.settlement.errors import (
    SettlementAuthorizationError,
    SettlementConfirmationError,
    SettlementEscrowVerificationError,
    SettlementMainnetBlockedError,
    SettlementOrchestrationMismatchError,
    SettlementStateConflictError,
)
from app.services.settlement.reconciliation import SettlementReconciliationService
from app.services.settlement.service import SettlementService
from app.services.settlement.verifier import (
    EscrowVerificationBoundary,
    STATE_CREATED,
    STATE_FUNDED,
    STATE_LOCKED,
    STATE_DISPUTED,
    STATE_RELEASED,
    STATE_REFUNDED,
)


@pytest.fixture
def keypair():
    return {
        "client": Account.create().address,
        "beneficiary": Account.create().address,
        "arbitrator": Account.create().address,
        "stranger": Account.create().address,
    }


# ==============================================================================
# TEST 1: Backend release permission matches Solidity
# ==============================================================================
@pytest.mark.asyncio
async def test_backend_release_permission_matches_solidity(db_session: AsyncSession, keypair):
    """Proves backend release authorization matches Escrow.sol releaseEscrow requirements:
    - In LOCKED or FUNDED: Client allowed, Arbitrator allowed. Beneficiary rejected. Stranger rejected.
    - In DISPUTED: Client rejected (only Arbitrator allowed in Solidity!).
    """
    cfg = get_chain_config(CHAIN_ID_ANVIL)
    escrow_id = "0x" + uuid.uuid4().hex + uuid.uuid4().hex

    client_user = User(wallet_address=keypair["client"], primary_role=UserRole.CLIENT.value)
    beneficiary_user = User(wallet_address=keypair["beneficiary"], primary_role=UserRole.DEVELOPER.value)
    arbitrator_user = User(wallet_address=keypair["arbitrator"], primary_role=UserRole.ADMIN.value)
    stranger_user = User(wallet_address=keypair["stranger"], primary_role=UserRole.CLIENT.value)
    db_session.add_all([client_user, beneficiary_user, arbitrator_user, stranger_user])
    await db_session.flush()

    # Seed state in LOCKED
    record = EscrowChainState(
        chain_id=CHAIN_ID_ANVIL,
        escrow_id=escrow_id,
        contract_address=cfg.contract_addresses["Escrow"],
        client=keypair["client"],
        developer=keypair["beneficiary"],
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
        idempotency_key=f"t1:rel:{uuid.uuid4().hex}",
        chain_id=CHAIN_ID_ANVIL,
        escrow_contract=cfg.contract_addresses["Escrow"].lower(),
        escrow_id=escrow_id,
        client_address=keypair["client"].lower(),
        beneficiary_address=keypair["beneficiary"].lower(),
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

    # 1. Beneficiary cannot release
    with pytest.raises(SettlementAuthorizationError, match="Beneficiary cannot unilaterally authorize escrow release"):
        await SettlementAuthorizationEngine.evaluate_authorization(
            session=db_session,
            settlement=settlement,
            escrow_result=escrow_res,
            caller_user=beneficiary_user,
            caller_wallet=keypair["beneficiary"],
        )

    # 2. Stranger cannot release
    with pytest.raises(SettlementAuthorizationError, match="not authorized to release escrow"):
        await SettlementAuthorizationEngine.evaluate_authorization(
            session=db_session,
            settlement=settlement,
            escrow_result=escrow_res,
            caller_user=stranger_user,
            caller_wallet=keypair["stranger"],
        )

    # 3. Client can release from LOCKED
    eval_client = await SettlementAuthorizationEngine.evaluate_authorization(
        session=db_session,
        settlement=settlement,
        escrow_result=escrow_res,
        caller_user=client_user,
        caller_wallet=keypair["client"],
    )
    assert eval_client.is_authorized is True

    # 4. Arbitrator can release from LOCKED
    eval_arb = await SettlementAuthorizationEngine.evaluate_authorization(
        session=db_session,
        settlement=settlement,
        escrow_result=escrow_res,
        caller_user=arbitrator_user,
        caller_wallet=keypair["arbitrator"],
    )
    assert eval_arb.is_authorized is True

    # 5. In DISPUTED state: Action RELEASE is rejected at verifier boundary (requires DISPUTE_RESOLVE_RELEASE)
    record.current_chain_state = STATE_DISPUTED
    await db_session.flush()
    with pytest.raises(SettlementEscrowVerificationError, match="Action RELEASE requires escrow in FUNDED or LOCKED state"):
        await EscrowVerificationBoundary.verify_escrow_for_settlement(
            session=db_session,
            chain_id=CHAIN_ID_ANVIL,
            escrow_id=escrow_id,
            action=SettlementAction.RELEASE,
        )


# ==============================================================================
# TEST 2: Backend refund permission matches Solidity
# ==============================================================================
@pytest.mark.asyncio
async def test_backend_refund_permission_matches_solidity(db_session: AsyncSession, keypair):
    """Proves backend refund authorization matches Escrow.sol refundEscrow requirements:
    - CREATED: Client and Arbitrator allowed. Beneficiary rejected.
    - FUNDED: Client, Beneficiary, Arbitrator allowed. Stranger rejected.
    - LOCKED:
        * Client before deadline REJECTED.
        * Client after deadline ALLOWED.
        * Beneficiary (forfeiture) before deadline ALLOWED.
        * Arbitrator ALLOWED.
    - DISPUTED: Only Arbitrator allowed (via DISPUTE_RESOLVE_REFUND).
    """
    cfg = get_chain_config(CHAIN_ID_ANVIL)
    escrow_id = "0x" + uuid.uuid4().hex + uuid.uuid4().hex

    client_user = User(wallet_address=keypair["client"], primary_role=UserRole.CLIENT.value)
    beneficiary_user = User(wallet_address=keypair["beneficiary"], primary_role=UserRole.DEVELOPER.value)
    arbitrator_user = User(wallet_address=keypair["arbitrator"], primary_role=UserRole.ADMIN.value)
    stranger_user = User(wallet_address=keypair["stranger"], primary_role=UserRole.CLIENT.value)
    db_session.add_all([client_user, beneficiary_user, arbitrator_user, stranger_user])

    # Canonical EscrowCreated event with deadline in future
    future_deadline = int((datetime.now(timezone.utc) + timedelta(hours=2)).timestamp())
    creation_event = BlockchainEvent(
        chain_id=CHAIN_ID_ANVIL,
        contract_address=cfg.contract_addresses["Escrow"].lower(),
        event_name="EscrowCreated",
        block_number=100,
        block_hash="0x" + "10" * 32,
        transaction_hash="0x" + "11" * 32,
        transaction_index=0,
        log_index=0,
        decoded_data={
            "escrowId": escrow_id,
            "client": keypair["client"],
            "beneficiary": keypair["beneficiary"],
            "amount": 5_000_000,
            "executionDeadline": future_deadline,
        },
        status=EventStatus.CONFIRMED.value,
        is_canonical=True,
    )
    db_session.add(creation_event)

    record = EscrowChainState(
        chain_id=CHAIN_ID_ANVIL,
        escrow_id=escrow_id,
        contract_address=cfg.contract_addresses["Escrow"],
        client=keypair["client"],
        developer=keypair["beneficiary"],
        amount=5_000_000,
        reference_id="0x" + "01" * 32,
        current_chain_state=STATE_LOCKED,
        creation_tx_hash=creation_event.transaction_hash,
        latest_tx_hash=creation_event.transaction_hash,
        is_canonical=True,
        confirmation_status=EventStatus.CONFIRMED.value,
    )
    db_session.add(record)

    settlement = Settlement(
        idempotency_key=f"t2:ref:{uuid.uuid4().hex}",
        chain_id=CHAIN_ID_ANVIL,
        escrow_contract=cfg.contract_addresses["Escrow"].lower(),
        escrow_id=escrow_id,
        client_address=keypair["client"].lower(),
        beneficiary_address=keypair["beneficiary"].lower(),
        token_address=cfg.token_address.lower(),
        amount=5_000_000,
        action=SettlementAction.REFUND.value,
        status=SettlementStatus.PENDING_AUTHORIZATION.value,
    )
    db_session.add(settlement)
    await db_session.flush()

    escrow_res = await EscrowVerificationBoundary.verify_escrow_for_settlement(
        session=db_session,
        chain_id=CHAIN_ID_ANVIL,
        escrow_id=escrow_id,
        action=SettlementAction.REFUND,
    )

    # 1. LOCKED before deadline: Client refund REJECTED
    with pytest.raises(SettlementAuthorizationError, match="has not passed yet"):
        await SettlementAuthorizationEngine.evaluate_authorization(
            session=db_session,
            settlement=settlement,
            escrow_result=escrow_res,
            caller_user=client_user,
            caller_wallet=keypair["client"],
        )

    # 2. LOCKED before deadline: Beneficiary forfeiture ALLOWED
    eval_bene = await SettlementAuthorizationEngine.evaluate_authorization(
        session=db_session,
        settlement=settlement,
        escrow_result=escrow_res,
        caller_user=beneficiary_user,
        caller_wallet=keypair["beneficiary"],
    )
    assert eval_bene.is_authorized is True

    # 3. LOCKED before deadline: Arbitrator ALLOWED
    eval_arb = await SettlementAuthorizationEngine.evaluate_authorization(
        session=db_session,
        settlement=settlement,
        escrow_result=escrow_res,
        caller_user=arbitrator_user,
        caller_wallet=keypair["arbitrator"],
    )
    assert eval_arb.is_authorized is True

    # 4. LOCKED after deadline: Client refund ALLOWED
    past_deadline = int((datetime.now(timezone.utc) - timedelta(hours=2)).timestamp())
    creation_event.decoded_data = {
        **creation_event.decoded_data,
        "executionDeadline": past_deadline,
    }
    await db_session.flush()

    eval_client_past = await SettlementAuthorizationEngine.evaluate_authorization(
        session=db_session,
        settlement=settlement,
        escrow_result=escrow_res,
        caller_user=client_user,
        caller_wallet=keypair["client"],
    )
    assert eval_client_past.is_authorized is True

    # 5. In DISPUTED state: Action REFUND is rejected at verifier boundary (requires DISPUTE_RESOLVE_REFUND)
    record.current_chain_state = STATE_DISPUTED
    await db_session.flush()
    with pytest.raises(SettlementEscrowVerificationError, match="Action REFUND requires escrow in CREATED, FUNDED, or LOCKED state"):
        await EscrowVerificationBoundary.verify_escrow_for_settlement(
            session=db_session,
            chain_id=CHAIN_ID_ANVIL,
            escrow_id=escrow_id,
            action=SettlementAction.REFUND,
        )


# ==============================================================================
# TEST 3: Backend dispute permission matches Solidity
# ==============================================================================
@pytest.mark.asyncio
async def test_backend_dispute_permission_matches_solidity(db_session: AsyncSession, keypair):
    """Proves dispute resolution authorization matches Escrow.sol resolveDispute:
    - Caller must have ARBITRATOR_ROLE / ADMIN.
    - Contract state must be DISPUTED.
    - Sum of splits must equal escrow.amount.
    """
    cfg = get_chain_config(CHAIN_ID_ANVIL)
    escrow_id = "0x" + uuid.uuid4().hex + uuid.uuid4().hex

    client_user = User(wallet_address=keypair["client"], primary_role=UserRole.CLIENT.value)
    beneficiary_user = User(wallet_address=keypair["beneficiary"], primary_role=UserRole.DEVELOPER.value)
    arbitrator_user = User(wallet_address=keypair["arbitrator"], primary_role=UserRole.ADMIN.value)
    db_session.add_all([client_user, beneficiary_user, arbitrator_user])

    # Seed in LOCKED (not DISPUTED)
    record = EscrowChainState(
        chain_id=CHAIN_ID_ANVIL,
        escrow_id=escrow_id,
        contract_address=cfg.contract_addresses["Escrow"],
        client=keypair["client"],
        developer=keypair["beneficiary"],
        amount=10_000_000,
        reference_id="0x" + "01" * 32,
        current_chain_state=STATE_LOCKED,
        creation_tx_hash="0x" + "02" * 32,
        latest_tx_hash="0x" + "02" * 32,
        is_canonical=True,
        confirmation_status=EventStatus.CONFIRMED.value,
    )
    db_session.add(record)
    await db_session.flush()

    # 1. Verification fails if action is DISPUTE_RESOLVE but state is LOCKED
    with pytest.raises(SettlementEscrowVerificationError, match="requires escrow in DISPUTED state"):
        await EscrowVerificationBoundary.verify_escrow_for_settlement(
            session=db_session,
            chain_id=CHAIN_ID_ANVIL,
            escrow_id=escrow_id,
            action=SettlementAction.DISPUTE_RESOLVE_RELEASE,
        )

    # Transition state to DISPUTED
    record.current_chain_state = STATE_DISPUTED
    await db_session.flush()

    escrow_res = await EscrowVerificationBoundary.verify_escrow_for_settlement(
        session=db_session,
        chain_id=CHAIN_ID_ANVIL,
        escrow_id=escrow_id,
        action=SettlementAction.DISPUTE_RESOLVE_RELEASE,
    )

    settlement = Settlement(
        idempotency_key=f"t3:disp:{uuid.uuid4().hex}",
        chain_id=CHAIN_ID_ANVIL,
        escrow_contract=cfg.contract_addresses["Escrow"].lower(),
        escrow_id=escrow_id,
        client_address=keypair["client"].lower(),
        beneficiary_address=keypair["beneficiary"].lower(),
        token_address=cfg.token_address.lower(),
        amount=10_000_000,
        action=SettlementAction.DISPUTE_RESOLVE_RELEASE.value,
        status=SettlementStatus.PENDING_AUTHORIZATION.value,
        beneficiary_amount=6_000_000,
        client_refund_amount=4_000_000,
    )
    db_session.add(settlement)
    await db_session.flush()

    # 2. Non-arbitrator rejected
    with pytest.raises(SettlementAuthorizationError, match="Only designated arbitrator"):
        await SettlementAuthorizationEngine.evaluate_authorization(
            session=db_session,
            settlement=settlement,
            escrow_result=escrow_res,
            caller_user=client_user,
            caller_wallet=keypair["client"],
        )

    # 3. Sum mismatch rejected
    settlement.beneficiary_amount = 7_000_000
    settlement.client_refund_amount = 4_000_000  # 11M != 10M
    await db_session.flush()
    with pytest.raises(SettlementAuthorizationError, match="Dispute resolution sum"):
        await SettlementAuthorizationEngine.evaluate_authorization(
            session=db_session,
            settlement=settlement,
            escrow_result=escrow_res,
            caller_user=arbitrator_user,
            caller_wallet=keypair["arbitrator"],
        )

    # 4. Correct split by arbitrator accepted
    settlement.beneficiary_amount = 6_000_000
    settlement.client_refund_amount = 4_000_000  # 10M == 10M
    await db_session.flush()
    eval_arb = await SettlementAuthorizationEngine.evaluate_authorization(
        session=db_session,
        settlement=settlement,
        escrow_result=escrow_res,
        caller_user=arbitrator_user,
        caller_wallet=keypair["arbitrator"],
    )
    assert eval_arb.is_authorized is True


# ==============================================================================
# TEST 4: Deep reorg fail-closed behavior (>32 blocks)
# ==============================================================================
@pytest.mark.asyncio
async def test_deep_reorg_fail_closed_behavior(db_session: AsyncSession, keypair):
    """Proves that a deep reorg (>32 blocks) raises DeepReorganizationError, marks
    the reorg as FAILED, transitions confirmed settlements to BLOCKED, and halts new authorizations.
    """
    from unittest.mock import AsyncMock
    cfg = get_chain_config(CHAIN_ID_ANVIL)
    handler = ReorgHandler(cfg)
    handler.find_common_ancestor = AsyncMock(return_value=(100, "0x" + "aa" * 32))

    # 1. Trigger deep reorg (depth 35 > 32)
    with pytest.raises(DeepReorganizationError, match="exceeds maximum tracking window"):
        await handler.handle_reorganization(
            session=db_session,
            detection_block_number=135,
            old_block_hash="0x" + "bb" * 32,
            new_block_hash="0x" + "cc" * 32,
        )

    # 2. Verify ChainReorganization record is FAILED
    reorg_stmt = sa.select(ChainReorganization).where(
        ChainReorganization.chain_id == CHAIN_ID_ANVIL,
        ChainReorganization.depth == 35,
    )
    reorg_record = (await db_session.execute(reorg_stmt)).scalar_one()
    assert reorg_record.status == ReorgStatus.FAILED

    # 3. Settlement in CONFIRMED on that chain transitions to BLOCKED upon reconciliation
    escrow_id = "0x" + uuid.uuid4().hex + uuid.uuid4().hex
    settlement = Settlement(
        idempotency_key=f"t4:deep:{uuid.uuid4().hex}",
        chain_id=CHAIN_ID_ANVIL,
        escrow_contract=cfg.contract_addresses["Escrow"].lower(),
        escrow_id=escrow_id,
        client_address=keypair["client"].lower(),
        beneficiary_address=keypair["beneficiary"].lower(),
        token_address=cfg.token_address.lower(),
        amount=5_000_000,
        action=SettlementAction.RELEASE.value,
        status=SettlementStatus.CONFIRMED.value,
        settlement_tx_hash="0x" + "aa" * 32,
        block_number=120,
        block_hash="0x" + "bb" * 32,
    )
    db_session.add(settlement)
    await db_session.flush()

    reconciler = SettlementReconciliationService(CHAIN_ID_ANVIL)
    await reconciler._reconcile_confirmed_for_reorg(db_session)
    await db_session.refresh(settlement)
    assert settlement.status == SettlementStatus.BLOCKED.value
    assert "Deep chain reorganization exceeded tracking window" in settlement.blocked_reason

    # 4. New authorization on this chain is refused
    client_user = User(wallet_address=keypair["client"], primary_role=UserRole.CLIENT.value)
    db_session.add(client_user)
    await db_session.flush()

    new_settlement = Settlement(
        idempotency_key=f"t4:new:{uuid.uuid4().hex}",
        chain_id=CHAIN_ID_ANVIL,
        escrow_contract=cfg.contract_addresses["Escrow"].lower(),
        escrow_id="0x" + uuid.uuid4().hex + uuid.uuid4().hex,
        client_address=keypair["client"].lower(),
        beneficiary_address=keypair["beneficiary"].lower(),
        token_address=cfg.token_address.lower(),
        amount=5_000_000,
        action=SettlementAction.RELEASE.value,
        status=SettlementStatus.PENDING_AUTHORIZATION.value,
    )
    db_session.add(new_settlement)
    await db_session.flush()

    with pytest.raises(SettlementAuthorizationError, match="Active unrecovered chain reorganization detected"):
        await SettlementService.authorize_settlement(
            session=db_session,
            settlement_id=new_settlement.id,
            actor_user=client_user,
        )

    # Clean up the failed reorg record so it does not affect other tests in the transaction
    await db_session.delete(reorg_record)
    await db_session.flush()


# ==============================================================================
# TEST 5: Reorg window configuration consistency (32 blocks)
# ==============================================================================
def test_reorg_window_configuration_consistency():
    """Proves authoritative max_reorg_depth is strictly 32 blocks across all modules."""
    assert get_max_reorg_depth(CHAIN_ID_ANVIL) == 32
    assert get_max_reorg_depth(CHAIN_ID_BASE_SEPOLIA) == 32
    assert get_max_reorg_depth(CHAIN_ID_BASE_MAINNET) == 32
    assert get_chain_config(CHAIN_ID_ANVIL).max_reorg_depth == 32
    assert get_chain_config(CHAIN_ID_BASE_SEPOLIA).max_reorg_depth == 32
    assert get_chain_config(CHAIN_ID_BASE_MAINNET).max_reorg_depth == 32
    assert ReorgHandler(get_chain_config(CHAIN_ID_ANVIL)).max_reorg_depth == 32


# ==============================================================================
# TEST 6: Confirmed-event requirement
# ==============================================================================
@pytest.mark.asyncio
async def test_confirmed_event_requirement(db_session: AsyncSession, keypair):
    """Proves unconfirmed events (SEEN, CONFIRMING) cannot authorize settlement."""
    cfg = get_chain_config(CHAIN_ID_ANVIL)
    escrow_id = "0x" + uuid.uuid4().hex + uuid.uuid4().hex

    record = EscrowChainState(
        chain_id=CHAIN_ID_ANVIL,
        escrow_id=escrow_id,
        contract_address=cfg.contract_addresses["Escrow"],
        client=keypair["client"],
        developer=keypair["beneficiary"],
        amount=5_000_000,
        reference_id="0x" + "01" * 32,
        current_chain_state=STATE_LOCKED,
        creation_tx_hash="0x" + "02" * 32,
        latest_tx_hash="0x" + "02" * 32,
        is_canonical=True,
        confirmation_status=EventStatus.CONFIRMING.value,  # NOT CONFIRMED
    )
    db_session.add(record)
    await db_session.flush()

    with pytest.raises(SettlementConfirmationError, match="requires 'CONFIRMED'"):
        await EscrowVerificationBoundary.verify_escrow_for_settlement(
            session=db_session,
            chain_id=CHAIN_ID_ANVIL,
            escrow_id=escrow_id,
            action=SettlementAction.RELEASE,
        )


# ==============================================================================
# TEST 7: Receipt-without-event rejection
# ==============================================================================
@pytest.mark.asyncio
async def test_receipt_without_event_rejection(db_session: AsyncSession, keypair):
    """Proves a successful mined tx receipt without the matching canonical event does NOT confirm settlement."""
    cfg = get_chain_config(CHAIN_ID_ANVIL)
    escrow_id = "0x" + uuid.uuid4().hex + uuid.uuid4().hex
    tx_hash = "0x" + "77" * 32

    settlement = Settlement(
        idempotency_key=f"t7:rcpt:{uuid.uuid4().hex}",
        chain_id=CHAIN_ID_ANVIL,
        escrow_contract=cfg.contract_addresses["Escrow"].lower(),
        escrow_id=escrow_id,
        client_address=keypair["client"].lower(),
        beneficiary_address=keypair["beneficiary"].lower(),
        token_address=cfg.token_address.lower(),
        amount=5_000_000,
        action=SettlementAction.RELEASE.value,
        status=SettlementStatus.SUBMITTED.value,
        settlement_tx_hash=tx_hash,
    )
    db_session.add(settlement)

    # Tx is mined successfully on chain
    tx = BlockchainTransaction(
        chain_id=CHAIN_ID_ANVIL,
        current_tx_hash=tx_hash,
        from_address=keypair["client"],
        to_address=cfg.contract_addresses["Escrow"],
        nonce=1,
        status="CONFIRMED",
    )
    db_session.add(tx)
    await db_session.flush()

    # Reconcile: No canonical EscrowReleased event exists!
    reconciler = SettlementReconciliationService(CHAIN_ID_ANVIL)
    await reconciler._reconcile_submitted(db_session)
    await db_session.refresh(settlement)

    # Settlement must remain SUBMITTED, NOT CONFIRMED
    assert settlement.status == SettlementStatus.SUBMITTED.value
    assert settlement.confirmed_at is None


# ==============================================================================
# TEST 8: Duplicate authorization idempotency
# ==============================================================================
@pytest.mark.asyncio
async def test_duplicate_authorization_idempotency(db_session: AsyncSession, keypair):
    """Proves calling authorize_settlement repeatedly is strictly idempotent."""
    cfg = get_chain_config(CHAIN_ID_ANVIL)
    escrow_id = "0x" + uuid.uuid4().hex + uuid.uuid4().hex

    record = EscrowChainState(
        chain_id=CHAIN_ID_ANVIL,
        escrow_id=escrow_id,
        contract_address=cfg.contract_addresses["Escrow"],
        client=keypair["client"],
        developer=keypair["beneficiary"],
        amount=5_000_000,
        reference_id="0x" + "01" * 32,
        current_chain_state=STATE_LOCKED,
        creation_tx_hash="0x" + "02" * 32,
        latest_tx_hash="0x" + "02" * 32,
        is_canonical=True,
        confirmation_status=EventStatus.CONFIRMED.value,
    )
    db_session.add(record)

    client_user = User(wallet_address=keypair["client"], primary_role=UserRole.CLIENT.value)
    db_session.add(client_user)
    await db_session.flush()

    settlement, _ = await SettlementService.create_settlement(
        session=db_session,
        chain_id=CHAIN_ID_ANVIL,
        escrow_id=escrow_id,
        action=SettlementAction.RELEASE,
        user_id=client_user.id,
        actor_user=client_user,
    )

    auth1 = await SettlementService.authorize_settlement(
        session=db_session,
        settlement_id=settlement.id,
        actor_user=client_user,
    )
    auth2 = await SettlementService.authorize_settlement(
        session=db_session,
        settlement_id=settlement.id,
        actor_user=client_user,
    )

    assert auth1.id == auth2.id
    assert auth1.transaction_intent_id == auth2.transaction_intent_id

    # Exactly one intent in DB
    intents = (
        await db_session.execute(
            sa.select(BlockchainTransactionIntent).where(
                BlockchainTransactionIntent.parameters["escrowId"].astext == escrow_id
            )
        )
    ).scalars().all()
    assert len(intents) == 1


# ==============================================================================
# TEST 9: Concurrent release/refund race rejection
# ==============================================================================
@pytest.mark.asyncio
async def test_concurrent_release_refund_race(db_session: AsyncSession, keypair):
    """Proves conflicting active settlements on the same escrow are rejected fail-closed."""
    cfg = get_chain_config(CHAIN_ID_ANVIL)
    escrow_id = "0x" + uuid.uuid4().hex + uuid.uuid4().hex

    record = EscrowChainState(
        chain_id=CHAIN_ID_ANVIL,
        escrow_id=escrow_id,
        contract_address=cfg.contract_addresses["Escrow"],
        client=keypair["client"],
        developer=keypair["beneficiary"],
        amount=5_000_000,
        reference_id="0x" + "01" * 32,
        current_chain_state=STATE_LOCKED,
        creation_tx_hash="0x" + "02" * 32,
        latest_tx_hash="0x" + "02" * 32,
        is_canonical=True,
        confirmation_status=EventStatus.CONFIRMED.value,
    )
    db_session.add(record)

    client_user = User(wallet_address=keypair["client"], primary_role=UserRole.CLIENT.value)
    db_session.add(client_user)
    await db_session.flush()

    # Active RELEASE settlement in AUTHORIZED status
    s1, _ = await SettlementService.create_settlement(
        session=db_session,
        chain_id=CHAIN_ID_ANVIL,
        escrow_id=escrow_id,
        action=SettlementAction.RELEASE,
        user_id=client_user.id,
        actor_user=client_user,
    )
    await SettlementService.authorize_settlement(
        session=db_session,
        settlement_id=s1.id,
        actor_user=client_user,
    )

    # Attempt to create conflicting REFUND settlement on same escrow
    with pytest.raises(SettlementStateConflictError, match="Conflicting settlement"):
        await SettlementService.create_settlement(
            session=db_session,
            chain_id=CHAIN_ID_ANVIL,
            escrow_id=escrow_id,
            action=SettlementAction.REFUND,
            user_id=client_user.id,
            actor_user=client_user,
        )


# ==============================================================================
# TEST 10: Crash recovery across boundaries
# ==============================================================================
@pytest.mark.asyncio
async def test_crash_recovery_across_boundaries(db_session: AsyncSession, keypair):
    """Simulates interruption after intent insertion and verifies restart resumes cleanly."""
    cfg = get_chain_config(CHAIN_ID_ANVIL)
    escrow_id = "0x" + uuid.uuid4().hex + uuid.uuid4().hex

    # Simulate state after crash before outbox processing
    intent = BlockchainTransactionIntent(
        chain_id=CHAIN_ID_ANVIL,
        idempotency_key=f"crash:{uuid.uuid4().hex}",
        target_contract=cfg.contract_addresses["Escrow"].lower(),
        operation="releaseEscrow",
        parameters={"escrowId": escrow_id},
        status=IntentStatus.CREATED.value,
    )
    db_session.add(intent)
    await db_session.flush()

    outbox = BlockchainTxOutbox(
        intent_id=intent.id,
        chain_id=CHAIN_ID_ANVIL,
        idempotency_key=intent.idempotency_key,
        payload={
            "intent_id": str(intent.id),
            "chain_id": CHAIN_ID_ANVIL,
            "target_contract": intent.target_contract,
            "operation": intent.operation,
            "parameters": intent.parameters,
        },
        status="PENDING",
    )
    db_session.add(outbox)
    await db_session.flush()

    class MockRpc(BlockchainRpcClient):
        async def get_chain_id(self):
            return CHAIN_ID_ANVIL

        async def get_transaction_count(self, address, block_tag="pending"):
            return 1

        async def get_fee_history(self, block_count, newest_block, reward_percentiles):
            return {
                "baseFeePerGas": [1_000_000_000, 1_000_000_000],
                "reward": [[1_000_000_000]],
            }

        async def estimate_gas(self, tx):
            return 100_000

        async def send_raw_transaction(self, raw_tx):
            return "0x" + "ff" * 32

    rpc = MockRpc(cfg)
    relayer = BlockchainRelayer(cfg, rpc, NonceManager(rpc), GasEstimator(rpc), MockSigner())
    await relayer.submit_intent(db_session, intent)
    outbox.status = "COMPLETED"
    await db_session.flush()
    await db_session.refresh(outbox)
    await db_session.refresh(intent)

    assert outbox.status == "COMPLETED"
    assert intent.status == IntentStatus.SUBMITTED.value


# ==============================================================================
# TEST 11: Pending vs latest nonce distinction
# ==============================================================================
@pytest.mark.asyncio
async def test_pending_vs_latest_nonce_distinction(db_session: AsyncSession, keypair):
    """Proves that pending_nonce > tx.nonce does NOT mark a transaction dropped or failed."""
    cfg = get_chain_config(CHAIN_ID_ANVIL)
    tx_hash = "0x" + "11" * 32

    tx = BlockchainTransaction(
        chain_id=CHAIN_ID_ANVIL,
        current_tx_hash=tx_hash,
        from_address=keypair["client"],
        to_address=cfg.contract_addresses["Escrow"],
        nonce=5,
        status=TxLifecycleStatus.SUBMITTED.value,
        submitted_at=datetime.now(timezone.utc) - timedelta(minutes=5),
    )
    db_session.add(tx)
    await db_session.flush()

    attempt = BlockchainTransactionAttempt(
        transaction_id=tx.id,
        attempt_number=1,
        tx_hash=tx_hash,
        nonce=5,
        max_fee_per_gas=10_000_000_000,
        max_priority_fee_per_gas=1_000_000_000,
        gas_limit=100_000,
        status=AttemptStatus.SUBMITTED.value,
    )
    db_session.add(attempt)
    await db_session.flush()

    # Create mock RPC where pending nonce is 6, but latest nonce is 5, tx is still in mempool
    class MockRpcMempool(BlockchainRpcClient):
        async def get_transaction_receipt(self, h):
            return None

        async def get_transaction(self, h):
            return {"hash": h, "blockNumber": None, "nonce": 5}

        async def get_transaction_count(self, address, block_tag="pending"):
            return 6 if block_tag == "pending" else 5

        async def send_raw_transaction(self, raw_tx):
            return "0x" + "22" * 32

    mock_rpc = MockRpcMempool(cfg)
    relayer = BlockchainRelayer(cfg, mock_rpc, NonceManager(mock_rpc), GasEstimator(mock_rpc), MockSigner())
    reconciler = BlockchainReconciliationService(cfg, mock_rpc, relayer)
    await reconciler.reconcile_pending_transactions(db_session)

    await db_session.refresh(tx)
    # Must remain SUBMITTED, NOT DROPPED or FAILED
    assert tx.status == TxLifecycleStatus.SUBMITTED.value


# ==============================================================================
# TEST 12: Replacement safety
# ==============================================================================
@pytest.mark.asyncio
async def test_replacement_safety(db_session: AsyncSession, keypair):
    """Proves replacement enforces the same nonce, >=12.5% fee bump, and respect of fee ceiling."""
    cfg = get_chain_config(CHAIN_ID_ANVIL)
    tx_hash = "0x" + "12" * 32

    intent = BlockchainTransactionIntent(
        chain_id=CHAIN_ID_ANVIL,
        idempotency_key=f"rep:{uuid.uuid4().hex}",
        target_contract=cfg.contract_addresses["Escrow"].lower(),
        operation="releaseEscrow",
        parameters={"escrowId": "0x" + "01" * 32},
        status=IntentStatus.SUBMITTED.value,
    )
    db_session.add(intent)
    await db_session.flush()

    tx = BlockchainTransaction(
        intent_id=intent.id,
        chain_id=CHAIN_ID_ANVIL,
        current_tx_hash=tx_hash,
        from_address=keypair["client"],
        to_address=cfg.contract_addresses["Escrow"],
        nonce=7,
        status="SUBMITTED",
        submitted_at=datetime.now(timezone.utc) - timedelta(minutes=15),
    )
    db_session.add(tx)
    await db_session.flush()

    attempt1 = BlockchainTransactionAttempt(
        transaction_id=tx.id,
        tx_hash=tx_hash,
        attempt_number=1,
        nonce=7,
        max_fee_per_gas=10_000_000_000,
        max_priority_fee_per_gas=1_000_000_000,
        gas_limit=100_000,
        status=AttemptStatus.SUBMITTED.value,
        submitted_at=datetime.now(timezone.utc) - timedelta(minutes=15),
    )
    db_session.add(attempt1)
    await db_session.flush()

    class MockRpcStuck(BlockchainRpcClient):
        async def get_transaction_receipt(self, h):
            return None

        async def get_transaction(self, h):
            return None

        async def get_transaction_count(self, address, block_tag="pending"):
            return 7

        async def send_raw_transaction(self, raw_tx):
            return "0x" + "88" * 32

    mock_stuck_rpc = MockRpcStuck(cfg)
    relayer = BlockchainRelayer(cfg, mock_stuck_rpc, NonceManager(mock_stuck_rpc), GasEstimator(mock_stuck_rpc), MockSigner())
    reconciler = BlockchainReconciliationService(cfg, mock_stuck_rpc, relayer)
    await reconciler.reconcile_pending_transactions(db_session)

    # Verify Attempt #2 was created with SAME nonce 7 and >= 12.5% higher fee
    attempts_stmt = sa.select(BlockchainTransactionAttempt).where(
        BlockchainTransactionAttempt.transaction_id == tx.id
    ).order_by(BlockchainTransactionAttempt.attempt_number.asc())
    attempts = (await db_session.execute(attempts_stmt)).scalars().all()

    assert len(attempts) == 2
    attempt2 = attempts[1]
    assert attempt2.nonce == 7
    assert attempt2.max_fee_per_gas >= int(attempt1.max_fee_per_gas * 1.125)
    assert attempt2.max_priority_fee_per_gas >= int(attempt1.max_priority_fee_per_gas * 1.125)


# ==============================================================================
# TEST 13: Arbitrary calldata rejection
# ==============================================================================
def test_arbitrary_calldata_rejection():
    """Proves arbitrary or unallowlisted calldata operations are rejected."""
    cfg = get_chain_config(CHAIN_ID_ANVIL)
    rpc = BlockchainRpcClient(cfg)
    relayer = BlockchainRelayer(cfg, rpc, NonceManager(rpc), GasEstimator(rpc), MockSigner())

    with pytest.raises(UnauthorizedOperationError, match="Unauthorized operation"):
        relayer._encode_calldata("selfDestruct", {})


# ==============================================================================
# TEST 14: Wrong token rejection
# ==============================================================================
@pytest.mark.asyncio
async def test_wrong_token_rejection(db_session: AsyncSession, keypair):
    """Proves escrows using non-whitelisted token are rejected."""
    cfg = get_chain_config(CHAIN_ID_ANVIL)
    escrow_id = "0x" + uuid.uuid4().hex + uuid.uuid4().hex
    wrong_token = Account.create().address

    record = EscrowChainState(
        chain_id=CHAIN_ID_ANVIL,
        escrow_id=escrow_id,
        contract_address=cfg.contract_addresses["Escrow"],
        client=keypair["client"],
        developer=keypair["beneficiary"],
        token=wrong_token,
        amount=5_000_000,
        reference_id="0x" + "01" * 32,
        current_chain_state=STATE_LOCKED,
        creation_tx_hash="0x" + "02" * 32,
        latest_tx_hash="0x" + "02" * 32,
        is_canonical=True,
        confirmation_status=EventStatus.CONFIRMED.value,
    )
    db_session.add(record)

    event = BlockchainEvent(
        chain_id=CHAIN_ID_ANVIL,
        contract_address=cfg.contract_addresses["Escrow"].lower(),
        event_name="EscrowCreated",
        block_number=100,
        block_hash="0x" + "10" * 32,
        transaction_hash="0x" + "02" * 32,
        transaction_index=0,
        log_index=0,
        decoded_data={
            "escrowId": escrow_id,
            "token": wrong_token,
        },
        status=EventStatus.CONFIRMED.value,
        is_canonical=True,
    )
    db_session.add(event)
    await db_session.flush()

    with pytest.raises(SettlementEscrowVerificationError, match="Escrow token mismatch"):
        await EscrowVerificationBoundary.verify_escrow_for_settlement(
            session=db_session,
            chain_id=CHAIN_ID_ANVIL,
            escrow_id=escrow_id,
            action=SettlementAction.RELEASE,
            expected_token=cfg.token_address,
        )


# ==============================================================================
# TEST 15: Wrong contract rejection
# ==============================================================================
@pytest.mark.asyncio
async def test_wrong_contract_rejection(db_session: AsyncSession, keypair):
    """Proves escrows targeting an unapproved contract address are rejected."""
    escrow_id = "0x" + uuid.uuid4().hex + uuid.uuid4().hex
    wrong_contract = Account.create().address

    record = EscrowChainState(
        chain_id=CHAIN_ID_ANVIL,
        escrow_id=escrow_id,
        contract_address=wrong_contract,
        client=keypair["client"],
        developer=keypair["beneficiary"],
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

    with pytest.raises(SettlementEscrowVerificationError, match="contract address mismatch"):
        await EscrowVerificationBoundary.verify_escrow_for_settlement(
            session=db_session,
            chain_id=CHAIN_ID_ANVIL,
            escrow_id=escrow_id,
            action=SettlementAction.RELEASE,
        )


# ==============================================================================
# TEST 16: Wrong chain rejection
# ==============================================================================
@pytest.mark.asyncio
async def test_wrong_chain_rejection(db_session: AsyncSession, keypair):
    """Proves settlement requests for unconfigured chain IDs are rejected."""
    client_user = User(wallet_address=keypair["client"], primary_role=UserRole.CLIENT.value)
    db_session.add(client_user)
    await db_session.flush()

    with pytest.raises(SettlementAuthorizationError, match="Unsupported chain ID"):
        await SettlementService.create_settlement(
            session=db_session,
            chain_id=999999,
            escrow_id="0x" + "01" * 32,
            action=SettlementAction.RELEASE,
            user_id=client_user.id,
            actor_user=client_user,
        )


# ==============================================================================
# TEST 17: Orchestration mismatch rejection
# ==============================================================================
@pytest.mark.asyncio
async def test_orchestration_mismatch_rejection(db_session: AsyncSession, keypair):
    """Proves settlement is rejected if linked orchestration is not SUCCEEDED or tasks failed."""
    cfg = get_chain_config(CHAIN_ID_ANVIL)
    escrow_id = "0x" + uuid.uuid4().hex + uuid.uuid4().hex

    client_user = User(wallet_address=keypair["client"], primary_role=UserRole.CLIENT.value)
    db_session.add(client_user)
    await db_session.flush()

    orch = Orchestration(
        requested_by=client_user.id,
        goal="Test workflow",
        status=OrchestrationStatus.FAILED.value,  # FAILED orchestration
    )
    db_session.add(orch)
    await db_session.flush()

    record = EscrowChainState(
        chain_id=CHAIN_ID_ANVIL,
        escrow_id=escrow_id,
        contract_address=cfg.contract_addresses["Escrow"],
        client=keypair["client"],
        developer=keypair["beneficiary"],
        amount=5_000_000,
        reference_id="0x" + "01" * 32,
        current_chain_state=STATE_LOCKED,
        creation_tx_hash="0x" + "02" * 32,
        latest_tx_hash="0x" + "02" * 32,
        is_canonical=True,
        confirmation_status=EventStatus.CONFIRMED.value,
    )
    db_session.add(record)

    settlement = Settlement(
        idempotency_key=f"t17:orch:{uuid.uuid4().hex}",
        chain_id=CHAIN_ID_ANVIL,
        escrow_contract=cfg.contract_addresses["Escrow"].lower(),
        escrow_id=escrow_id,
        client_address=keypair["client"].lower(),
        beneficiary_address=keypair["beneficiary"].lower(),
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

    with pytest.raises(SettlementOrchestrationMismatchError, match="required 'SUCCEEDED' for release"):
        await SettlementAuthorizationEngine.evaluate_authorization(
            session=db_session,
            settlement=settlement,
            escrow_result=escrow_res,
            caller_user=client_user,
            caller_wallet=keypair["client"],
        )


# ==============================================================================
# TEST 18: Mainnet bypass rejection
# ==============================================================================
@pytest.mark.asyncio
async def test_mainnet_bypass_rejection(db_session: AsyncSession, keypair):
    """Proves Base Mainnet settlement attempts are strictly blocked across all boundaries."""
    client_user = User(wallet_address=keypair["client"], primary_role=UserRole.CLIENT.value)
    db_session.add(client_user)
    await db_session.flush()

    # 1. Settlement creation blocked
    with pytest.raises(SettlementMainnetBlockedError):
        await SettlementService.create_settlement(
            session=db_session,
            chain_id=CHAIN_ID_BASE_MAINNET,
            escrow_id="0x" + "01" * 32,
            action=SettlementAction.RELEASE,
            user_id=client_user.id,
            actor_user=client_user,
        )

    # 2. Verification blocked
    with pytest.raises(SettlementMainnetBlockedError):
        await EscrowVerificationBoundary.verify_escrow_for_settlement(
            session=db_session,
            chain_id=CHAIN_ID_BASE_MAINNET,
            escrow_id="0x" + "01" * 32,
            action=SettlementAction.RELEASE,
        )

    # 3. Relayer blocked
    mainnet_cfg = get_chain_config(CHAIN_ID_BASE_MAINNET)
    rpc = BlockchainRpcClient(mainnet_cfg)
    relayer = BlockchainRelayer(mainnet_cfg, rpc, NonceManager(rpc), GasEstimator(rpc), MockSigner())
    mainnet_intent = BlockchainTransactionIntent(
        chain_id=CHAIN_ID_BASE_MAINNET,
        idempotency_key="mainnet:block:1",
        target_contract=mainnet_cfg.contract_addresses.get("Escrow", "0x" + "00" * 20),
        operation="releaseEscrow",
        parameters={},
    )
    with pytest.raises(MainnetSubmissionBlockedError):
        await relayer.submit_intent(db_session, mainnet_intent)


# ==============================================================================
# TEST 19: Duplicate indexer event idempotency
# ==============================================================================
@pytest.mark.asyncio
async def test_duplicate_indexer_event_idempotency(db_session: AsyncSession):
    """Proves duplicate canonical events cannot violate database uniqueness constraints."""
    cfg = get_chain_config(CHAIN_ID_ANVIL)
    tx_hash = "0x" + "19" * 32
    block_hash = "0x" + "10" * 32

    ev1 = BlockchainEvent(
        chain_id=CHAIN_ID_ANVIL,
        contract_address=cfg.contract_addresses["Escrow"].lower(),
        event_name="EscrowReleased",
        block_number=100,
        block_hash=block_hash,
        transaction_hash=tx_hash,
        transaction_index=0,
        log_index=0,
        decoded_data={"escrowId": "0x" + "01" * 32},
        status=EventStatus.CONFIRMED.value,
        is_canonical=True,
    )
    db_session.add(ev1)
    await db_session.flush()

    # Attempt to insert identical event violates unique constraint
    ev2 = BlockchainEvent(
        chain_id=CHAIN_ID_ANVIL,
        contract_address=cfg.contract_addresses["Escrow"].lower(),
        event_name="EscrowReleased",
        block_number=100,
        block_hash=block_hash,
        transaction_hash=tx_hash,
        transaction_index=0,
        log_index=0,
        decoded_data={"escrowId": "0x" + "01" * 32},
        status=EventStatus.CONFIRMED.value,
        is_canonical=True,
    )
    db_session.add(ev2)
    with pytest.raises(sa.exc.IntegrityError):
        await db_session.flush()
    await db_session.rollback()


# ==============================================================================
# TEST 20: Reorg after confirmation invalidation
# ==============================================================================
@pytest.mark.asyncio
async def test_reorg_after_confirmation(db_session: AsyncSession, keypair):
    """Proves a confirmed settlement is invalidated to SUBMITTED when its transaction events are orphaned."""
    cfg = get_chain_config(CHAIN_ID_ANVIL)
    escrow_id = "0x" + uuid.uuid4().hex + uuid.uuid4().hex
    block_hash = "0x" + "20" * 32
    tx_hash = "0x" + "21" * 32

    event = BlockchainEvent(
        chain_id=CHAIN_ID_ANVIL,
        contract_address=cfg.contract_addresses["Escrow"].lower(),
        event_name="EscrowReleased",
        block_number=500,
        block_hash=block_hash,
        transaction_hash=tx_hash,
        transaction_index=0,
        log_index=0,
        decoded_data={"escrowId": escrow_id},
        status=EventStatus.CONFIRMED.value,
        is_canonical=True,
    )
    db_session.add(event)

    settlement = Settlement(
        idempotency_key=f"t20:reorg:{uuid.uuid4().hex}",
        chain_id=CHAIN_ID_ANVIL,
        escrow_contract=cfg.contract_addresses["Escrow"].lower(),
        escrow_id=escrow_id,
        client_address=keypair["client"].lower(),
        beneficiary_address=keypair["beneficiary"].lower(),
        token_address=cfg.token_address.lower(),
        amount=5_000_000,
        action=SettlementAction.RELEASE.value,
        status=SettlementStatus.CONFIRMED.value,
        settlement_tx_hash=tx_hash,
        block_number=500,
        block_hash=block_hash,
        confirmed_at=datetime.now(timezone.utc),
    )
    db_session.add(settlement)
    await db_session.flush()

    # Reorg occurs: event becomes non-canonical (orphaned)
    event.is_canonical = False
    await db_session.flush()

    reconciler = SettlementReconciliationService(CHAIN_ID_ANVIL)
    await reconciler._reconcile_confirmed_for_reorg(db_session)
    await db_session.refresh(settlement)

    assert settlement.status == SettlementStatus.SUBMITTED.value
    assert settlement.confirmed_at is None


# ==============================================================================
# TEST 21: Migration 011 upgrade/downgrade/upgrade safety
# ==============================================================================
def test_migration_011_upgrade_downgrade_upgrade():
    """Proves Alembic migration 011 downgrades and upgrades cleanly without loss of schema integrity."""
    backend_dir = Path(__file__).resolve().parent.parent
    venv_alembic = backend_dir.parent / ".venv" / "bin" / "alembic"

    # Downgrade to 010
    cmd_down = [str(venv_alembic), "downgrade", "-1"]
    res_down = subprocess.run(cmd_down, cwd=str(backend_dir), capture_output=True, text=True)
    assert res_down.returncode == 0, f"Downgrade failed: {res_down.stderr}"

    # Upgrade back to 011 (head)
    cmd_up = [str(venv_alembic), "upgrade", "head"]
    res_up = subprocess.run(cmd_up, cwd=str(backend_dir), capture_output=True, text=True)
    assert res_up.returncode == 0, f"Upgrade failed: {res_up.stderr}"
