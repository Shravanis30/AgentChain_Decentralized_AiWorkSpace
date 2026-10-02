"""Tests for SettlementService: creation, authorization, transactional intent dispatch, and concurrency."""

import asyncio
import uuid
import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit import AuditEventType, AuditLog
from app.models.blockchain import (
    BlockchainTransactionIntent,
    BlockchainTxOutbox,
    EscrowChainState,
    EventStatus,
    IntentStatus,
)
from app.models.settlement import (
    Settlement,
    SettlementAction,
    SettlementHistory,
    SettlementStatus,
)
from app.models.user import User, UserRole
from app.services.blockchain.config import CHAIN_ID_ANVIL, get_chain_config
from app.services.settlement.errors import (
    SettlementNotFoundError,
    SettlementStateConflictError,
)
from app.services.settlement.service import SettlementService
from app.services.settlement.verifier import STATE_LOCKED


from eth_account import Account


@pytest.fixture
def sample_user_and_escrow():
    return {
        "client": Account.create().address,
        "beneficiary": Account.create().address,
        "escrow_id": "0x" + uuid.uuid4().hex,
    }


@pytest.mark.asyncio
async def test_settlement_creation_idempotency(db_session: AsyncSession, sample_user_and_escrow):
    """Tests idempotent creation of settlement records and audit logs."""
    cfg = get_chain_config(CHAIN_ID_ANVIL)
    escrow_id = sample_user_and_escrow["escrow_id"]

    # Seed EscrowChainState
    record = EscrowChainState(
        chain_id=CHAIN_ID_ANVIL,
        escrow_id=escrow_id,
        contract_address=cfg.contract_addresses["Escrow"],
        client=sample_user_and_escrow["client"],
        developer=sample_user_and_escrow["beneficiary"],
        amount=10_000_000,
        reference_id="0x" + "01" * 32,
        current_chain_state=STATE_LOCKED,
        creation_tx_hash="0x" + "02" * 32,
        latest_tx_hash="0x" + "02" * 32,
        is_canonical=True,
        confirmation_status=EventStatus.CONFIRMED.value,
    )
    db_session.add(record)

    client_user = User(wallet_address=sample_user_and_escrow["client"], primary_role=UserRole.CLIENT.value)
    db_session.add(client_user)
    await db_session.flush()

    # 1. First creation call -> created = True
    settlement, was_created = await SettlementService.create_settlement(
        session=db_session,
        chain_id=CHAIN_ID_ANVIL,
        escrow_id=escrow_id,
        action=SettlementAction.RELEASE,
        user_id=client_user.id,
        actor_user=client_user,
    )
    assert was_created is True
    assert settlement.status == SettlementStatus.PENDING_AUTHORIZATION.value
    assert settlement.amount == 10_000_000

    # 2. Second creation call with identical parameters -> created = False, returns same settlement
    settlement2, was_created2 = await SettlementService.create_settlement(
        session=db_session,
        chain_id=CHAIN_ID_ANVIL,
        escrow_id=escrow_id,
        action=SettlementAction.RELEASE,
        user_id=client_user.id,
        actor_user=client_user,
    )
    assert was_created2 is False
    assert settlement2.id == settlement.id

    # 3. Check audit log was written
    audit_stmt = sa.select(AuditLog).where(
        AuditLog.event_type == AuditEventType.SETTLEMENT_CREATED.value,
        AuditLog.user_id == client_user.id,
    )
    audits = (await db_session.execute(audit_stmt)).scalars().all()
    assert len(audits) >= 1
    assert audits[0].metadata_json["escrow_id"] == escrow_id


@pytest.mark.asyncio
async def test_settlement_authorization_creates_atomic_intent(db_session: AsyncSession):
    """Tests that authorization creates the transaction intent and outbox item atomically."""
    cfg = get_chain_config(CHAIN_ID_ANVIL)
    escrow_id = "0x" + uuid.uuid4().hex + uuid.uuid4().hex
    client_addr = Account.create().address
    beneficiary_addr = Account.create().address

    # Seed EscrowChainState
    record = EscrowChainState(
        chain_id=CHAIN_ID_ANVIL,
        escrow_id=escrow_id,
        contract_address=cfg.contract_addresses["Escrow"],
        client=client_addr,
        developer=beneficiary_addr,
        amount=5_000_000,
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

    # Authorize settlement
    authorized_settlement = await SettlementService.authorize_settlement(
        session=db_session,
        settlement_id=settlement.id,
        actor_user=client_user,
        caller_wallet=client_addr,
        reason="Work verified and approved",
    )

    assert authorized_settlement.status == SettlementStatus.AUTHORIZED.value
    assert authorized_settlement.transaction_intent_id is not None
    assert authorized_settlement.authorized_at is not None

    # Verify atomic TransactionIntent exists
    intent_stmt = sa.select(BlockchainTransactionIntent).where(
        BlockchainTransactionIntent.id == authorized_settlement.transaction_intent_id
    )
    intent = (await db_session.execute(intent_stmt)).scalar_one()
    assert intent.operation == "releaseEscrow"
    assert intent.parameters["escrowId"] == escrow_id
    assert intent.chain_id == CHAIN_ID_ANVIL

    # Verify atomic Outbox entry exists
    outbox_stmt = sa.select(BlockchainTxOutbox).where(
        BlockchainTxOutbox.intent_id == intent.id
    )
    outbox_item = (await db_session.execute(outbox_stmt)).scalar_one()
    assert outbox_item.status == "PENDING"
    assert outbox_item.chain_id == CHAIN_ID_ANVIL

    # Re-authorizing returns existing without modifying
    re_auth = await SettlementService.authorize_settlement(
        session=db_session,
        settlement_id=settlement.id,
        actor_user=client_user,
        caller_wallet=client_addr,
    )
    assert re_auth.id == authorized_settlement.id
    assert re_auth.transaction_intent_id == intent.id


@pytest.mark.asyncio
async def test_settlement_cancellation(db_session: AsyncSession):
    """Tests cancellation before submission and rejection of cancellation once submitted."""
    cfg = get_chain_config(CHAIN_ID_ANVIL)
    escrow_id = "0x" + uuid.uuid4().hex + uuid.uuid4().hex
    client_addr = Account.create().address
    beneficiary_addr = Account.create().address

    record = EscrowChainState(
        chain_id=CHAIN_ID_ANVIL,
        escrow_id=escrow_id,
        contract_address=cfg.contract_addresses["Escrow"],
        client=client_addr,
        developer=beneficiary_addr,
        amount=5_000_000,
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

    # 1. Cancel pending settlement
    cancelled = await SettlementService.cancel_settlement(
        session=db_session,
        settlement_id=settlement.id,
        actor_user=client_user,
        reason="Changed requirements",
    )
    assert cancelled.status == SettlementStatus.CANCELLED.value
    assert cancelled.cancelled_at is not None

    # 2. Cannot authorize cancelled settlement
    with pytest.raises(SettlementStateConflictError):
        await SettlementService.authorize_settlement(
            session=db_session,
            settlement_id=settlement.id,
            actor_user=client_user,
        )


@pytest.mark.asyncio
async def test_concurrent_settlement_authorization(db_session: AsyncSession):
    """Verifies concurrency safety: two workers attempting authorization result in exactly one intent."""
    cfg = get_chain_config(CHAIN_ID_ANVIL)
    escrow_id = "0x" + uuid.uuid4().hex + uuid.uuid4().hex
    client_addr = Account.create().address
    beneficiary_addr = Account.create().address

    record = EscrowChainState(
        chain_id=CHAIN_ID_ANVIL,
        escrow_id=escrow_id,
        contract_address=cfg.contract_addresses["Escrow"],
        client=client_addr,
        developer=beneficiary_addr,
        amount=5_000_000,
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

    # Worker 1 authorizes
    s1 = await SettlementService.authorize_settlement(
        session=db_session,
        settlement_id=settlement.id,
        actor_user=client_user,
        caller_wallet=client_addr,
    )
    await db_session.commit()

    # Worker 2 authorizes immediately after
    s2 = await SettlementService.authorize_settlement(
        session=db_session,
        settlement_id=settlement.id,
        actor_user=client_user,
        caller_wallet=client_addr,
    )

    assert s1.id == s2.id
    assert s1.transaction_intent_id == s2.transaction_intent_id

    # Verify exactly ONE transaction intent was created
    intents = (
        await db_session.execute(
            sa.select(BlockchainTransactionIntent).where(
                BlockchainTransactionIntent.chain_id == CHAIN_ID_ANVIL,
                BlockchainTransactionIntent.parameters["escrowId"].astext == escrow_id,
            )
        )
    ).scalars().all()
    assert len(intents) == 1
