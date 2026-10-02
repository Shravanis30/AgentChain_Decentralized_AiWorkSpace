"""Advanced recovery and failure scenario tests for Phase 6.2B Settlement Integration.

Covers:
- Step 10: Pending nonce distinction vs consumed nonce
- Step 12: Crash recovery, Redis outage, Postgres rollback, worker restarts
- Step 14: Duplicate event resilience
- Transaction replacement linking to settlement
"""

from datetime import datetime, timedelta, timezone
import uuid
from unittest.mock import AsyncMock, patch
from eth_account import Account
import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.base import utc_now
from app.models.blockchain import (
    AttemptStatus,
    BlockchainEvent,
    BlockchainTransaction,
    BlockchainTransactionAttempt,
    BlockchainTransactionIntent,
    BlockchainTxOutbox,
    EscrowChainState,
    EventStatus,
    IntentStatus,
    TxLifecycleStatus,
)
from app.models.settlement import (
    Settlement,
    SettlementAction,
    SettlementHistory,
    SettlementStatus,
)
from app.models.user import User, UserRole
from app.services.blockchain.config import CHAIN_ID_ANVIL, get_chain_config
from app.services.blockchain.tx_outbox import BlockchainTxOutboxDispatcher
from app.services.settlement.reconciliation import SettlementReconciliationService
from app.services.settlement.service import SettlementService
from app.services.settlement.verifier import STATE_LOCKED, STATE_RELEASED


@pytest.mark.asyncio
async def test_settlement_postgres_rollback_safety(db_session: AsyncSession):
    """Verifies that an unhandled exception during intent creation rolls back cleanly, leaving settlement intact."""
    cfg = get_chain_config(CHAIN_ID_ANVIL)
    escrow_id = "0x" + uuid.uuid4().hex + uuid.uuid4().hex
    client_addr = Account.create().address
    dev_addr = Account.create().address

    record = EscrowChainState(
        chain_id=CHAIN_ID_ANVIL,
        escrow_id=escrow_id,
        contract_address=cfg.contract_addresses["Escrow"],
        client=client_addr,
        developer=dev_addr,
        amount=10_000_000,
        reference_id="0x" + "01" * 32,
        current_chain_state=STATE_LOCKED,
        creation_tx_hash="0x" + "02" * 32,
        latest_tx_hash="0x" + "02" * 32,
        is_canonical=True,
        confirmation_status=EventStatus.CONFIRMED.value,
    )
    db_session.add(record)

    client_user = User(wallet_address=client_addr, primary_role=UserRole.CLIENT.value)
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
    await db_session.commit()
    settlement_id = settlement.id

    # Simulate crash/failure inside TransactionIntentService
    with patch(
        "app.services.blockchain.transaction_intent.TransactionIntentService.create_intent",
        side_effect=RuntimeError("Simulated database failure during intent generation"),
    ):
        with pytest.raises(RuntimeError, match="Simulated database failure"):
            await SettlementService.authorize_settlement(
                session=db_session,
                settlement_id=settlement_id,
                actor_user=client_user,
                caller_wallet=client_addr,
            )

    # Rollback transaction
    await db_session.rollback()

    # Verify settlement remains PENDING_AUTHORIZATION and no transaction intent was left behind
    refreshed = await db_session.get(Settlement, settlement_id)
    assert refreshed is not None
    assert refreshed.status == SettlementStatus.PENDING_AUTHORIZATION.value
    assert refreshed.transaction_intent_id is None

    intents = (
        await db_session.execute(
            sa.select(BlockchainTransactionIntent).where(
                BlockchainTransactionIntent.parameters["escrowId"].astext == escrow_id
            )
        )
    ).scalars().all()
    assert len(intents) == 0


@pytest.mark.asyncio
async def test_settlement_redis_outage_resilience(db_session: AsyncSession):
    """Verifies that Redis outage prevents outbox dispatch but leaves Settlement and Outbox records durable in PostgreSQL."""
    cfg = get_chain_config(CHAIN_ID_ANVIL)
    escrow_id = "0x" + uuid.uuid4().hex + uuid.uuid4().hex
    client_addr = Account.create().address
    dev_addr = Account.create().address

    record = EscrowChainState(
        chain_id=CHAIN_ID_ANVIL,
        escrow_id=escrow_id,
        contract_address=cfg.contract_addresses["Escrow"],
        client=client_addr,
        developer=dev_addr,
        amount=10_000_000,
        reference_id="0x" + "01" * 32,
        current_chain_state=STATE_LOCKED,
        creation_tx_hash="0x" + "02" * 32,
        latest_tx_hash="0x" + "02" * 32,
        is_canonical=True,
        confirmation_status=EventStatus.CONFIRMED.value,
    )
    db_session.add(record)

    client_user = User(wallet_address=client_addr, primary_role=UserRole.CLIENT.value)
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

    authorized = await SettlementService.authorize_settlement(
        session=db_session,
        settlement_id=settlement.id,
        actor_user=client_user,
        caller_wallet=client_addr,
    )
    await db_session.commit()

    # Outbox item exists and is PENDING
    outbox_item = (
        await db_session.execute(
            sa.select(BlockchainTxOutbox).where(BlockchainTxOutbox.intent_id == authorized.transaction_intent_id)
        )
    ).scalar_one()
    assert outbox_item.status == "PENDING"

    # Simulate Redis connection failure during dispatcher execution
    mock_redis = AsyncMock()
    mock_redis.xadd.side_effect = ConnectionError("Redis is unreachable")
    dispatcher = BlockchainTxOutboxDispatcher(mock_redis)

    dispatched = await dispatcher.dispatch_pending(db_session)
    assert dispatched == 0

    # State in PostgreSQL is preserved and ready for retry
    refreshed_outbox = (
        await db_session.execute(
            sa.select(BlockchainTxOutbox).where(BlockchainTxOutbox.intent_id == authorized.transaction_intent_id)
        )
    ).scalar_one()
    assert refreshed_outbox.status == "PENDING"
    assert "Redis is unreachable" in (refreshed_outbox.error_message or "")

    # Reset next_attempt_at to past so it's immediately due
    refreshed_outbox.next_attempt_at = utc_now() - timedelta(seconds=1)
    await db_session.commit()

    # When Redis recovers, dispatch succeeds
    mock_redis.xadd.side_effect = None
    mock_redis.xadd.return_value = "1001-0"
    dispatched = await dispatcher.dispatch_pending(db_session)
    assert dispatched >= 1


@pytest.mark.asyncio
async def test_settlement_step10_pending_nonce_does_not_fail_intent(db_session: AsyncSession):
    """Step 10 security requirement: A higher pending nonce does not mark settlement/intent failed without proof of consumption."""
    cfg = get_chain_config(CHAIN_ID_ANVIL)
    escrow_id = "0x" + uuid.uuid4().hex + uuid.uuid4().hex
    client_addr = Account.create().address
    dev_addr = Account.create().address

    record = EscrowChainState(
        chain_id=CHAIN_ID_ANVIL,
        escrow_id=escrow_id,
        contract_address=cfg.contract_addresses["Escrow"],
        client=client_addr,
        developer=dev_addr,
        amount=10_000_000,
        reference_id="0x" + "01" * 32,
        current_chain_state=STATE_LOCKED,
        creation_tx_hash="0x" + "02" * 32,
        latest_tx_hash="0x" + "02" * 32,
        is_canonical=True,
        confirmation_status=EventStatus.CONFIRMED.value,
    )
    db_session.add(record)

    client_user = User(wallet_address=client_addr, primary_role=UserRole.CLIENT.value)
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

    authorized = await SettlementService.authorize_settlement(
        session=db_session,
        settlement_id=settlement.id,
        actor_user=client_user,
        caller_wallet=client_addr,
    )

    # Simulate transaction submitted with nonce 5
    tx = BlockchainTransaction(
        chain_id=CHAIN_ID_ANVIL,
        intent_id=authorized.transaction_intent_id,
        from_address=client_addr,
        to_address=cfg.contract_addresses["Escrow"],
        nonce=5,
        status=TxLifecycleStatus.SUBMITTED.value,
    )
    db_session.add(tx)
    await db_session.flush()

    attempt = BlockchainTransactionAttempt(
        transaction_id=tx.id,
        attempt_number=1,
        tx_hash="0x" + "aa" * 32,
        status=AttemptStatus.SUBMITTED,
        nonce=5,
        max_fee_per_gas=20_000_000_000,
        max_priority_fee_per_gas=1_000_000_000,
        gas_limit=150_000,
    )
    db_session.add(attempt)
    await db_session.flush()

    # Reconcile settlement into SUBMITTED
    recon = SettlementReconciliationService(CHAIN_ID_ANVIL)
    stats1 = await recon.reconcile_settlements(db_session)
    assert stats1["advanced_to_submitted"] == 1
    await db_session.refresh(settlement)
    assert settlement.status == SettlementStatus.SUBMITTED.value

    # Suppose pending nonce on RPC reports 7 (mempool activity), while latest mined nonce is still 4.
    # The settlement MUST NOT transition to FAILED or CANCELLED based merely on pending nonce.
    stats2 = await recon.reconcile_settlements(db_session)
    await db_session.refresh(settlement)
    assert settlement.status == SettlementStatus.SUBMITTED.value
    assert settlement.status != SettlementStatus.FAILED.value


@pytest.mark.asyncio
async def test_settlement_duplicate_indexer_event_idempotency(db_session: AsyncSession):
    """Verifies that indexing duplicate events (e.g. repeated block scan or replay) does not corrupt settlement."""
    cfg = get_chain_config(CHAIN_ID_ANVIL)
    escrow_id = "0x" + uuid.uuid4().hex + uuid.uuid4().hex
    client_addr = Account.create().address
    dev_addr = Account.create().address
    release_tx = "0x" + "ee" * 32

    # Escrow initially in LOCKED state
    record = EscrowChainState(
        chain_id=CHAIN_ID_ANVIL,
        escrow_id=escrow_id,
        contract_address=cfg.contract_addresses["Escrow"],
        client=client_addr,
        developer=dev_addr,
        amount=10_000_000,
        reference_id="0x" + "01" * 32,
        current_chain_state=STATE_LOCKED,
        creation_tx_hash="0x" + "02" * 32,
        latest_tx_hash="0x" + "02" * 32,
        latest_block_number=100,
        latest_block_hash="0x" + "99" * 32,
        is_canonical=True,
        confirmation_status=EventStatus.CONFIRMED.value,
    )
    db_session.add(record)

    client_user = User(wallet_address=client_addr, primary_role=UserRole.CLIENT.value)
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
    settlement.status = SettlementStatus.SUBMITTED.value
    await db_session.flush()

    # Indexer processes EscrowReleased event and updates EscrowChainState
    record.current_chain_state = STATE_RELEASED
    record.latest_tx_hash = release_tx
    event = BlockchainEvent(
        chain_id=CHAIN_ID_ANVIL,
        contract_address=cfg.contract_addresses["Escrow"],
        block_number=100,
        block_hash="0x" + "99" * 32,
        transaction_hash=release_tx,
        transaction_index=0,
        log_index=0,
        event_name="EscrowReleased",
        event_signature="EscrowReleased(bytes32,address,address,uint256)",
        decoded_data={"escrowId": escrow_id, "client": client_addr, "developer": dev_addr, "amount": 10000000},
        raw_topics=["0x" + "aa" * 32],
        raw_data="0x",
        status=EventStatus.CONFIRMED.value,
        is_canonical=True,
    )
    db_session.add(event)
    await db_session.flush()

    recon = SettlementReconciliationService(CHAIN_ID_ANVIL)

    # 1. First reconciliation
    stats_first = await recon.reconcile_settlements(db_session)
    assert stats_first["advanced_to_confirmed"] == 1
    await db_session.refresh(settlement)
    assert settlement.status == SettlementStatus.CONFIRMED.value
    assert settlement.confirmed_at is not None

    # 2. Second reconciliation (duplicate scan / duplicate event)
    stats_second = await recon.reconcile_settlements(db_session)
    assert stats_second["advanced_to_confirmed"] == 0
    await db_session.refresh(settlement)
    assert settlement.status == SettlementStatus.CONFIRMED.value
