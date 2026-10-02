"""Tests for SettlementReconciliationService: submission advancing, confirmation tracking, and reorg invalidation."""

import uuid
import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit import AuditEventType, AuditLog
from app.models.blockchain import (
    BlockchainEvent,
    BlockchainTransaction,
    BlockchainTransactionIntent,
    EventStatus,
    IntentStatus,
)
from app.models.settlement import (
    Settlement,
    SettlementAction,
    SettlementStatus,
)
from app.services.blockchain.config import CHAIN_ID_ANVIL, get_chain_config
from app.services.settlement.reconciliation import SettlementReconciliationService


@pytest.mark.asyncio
async def test_reconciliation_advances_authorized_to_submitted(db_session: AsyncSession):
    """Verifies that an AUTHORIZED settlement advances to SUBMITTED when an on-chain transaction is created."""
    cfg = get_chain_config(CHAIN_ID_ANVIL)
    escrow_id = "0x" + "e1" * 32

    # Create intent in SUBMITTED state
    intent = BlockchainTransactionIntent(
        chain_id=CHAIN_ID_ANVIL,
        idempotency_key="tx:settlement:31337:e1:release:1",
        target_contract=cfg.contract_addresses["Escrow"].lower(),
        operation="releaseEscrow",
        parameters={"escrowId": escrow_id},
        status=IntentStatus.SUBMITTED.value,
    )
    db_session.add(intent)
    await db_session.flush()

    # Create transaction
    tx = BlockchainTransaction(
        intent_id=intent.id,
        chain_id=CHAIN_ID_ANVIL,
        from_address="0x" + "11" * 20,
        to_address=cfg.contract_addresses["Escrow"],
        nonce=5,
        status="SUBMITTED",
        current_tx_hash="0x" + "aa" * 32,
    )
    db_session.add(tx)

    # Create settlement in AUTHORIZED state
    settlement = Settlement(
        idempotency_key="settlement:31337:e1:release:1",
        chain_id=CHAIN_ID_ANVIL,
        escrow_contract=cfg.contract_addresses["Escrow"].lower(),
        escrow_id=escrow_id,
        client_address="0x" + "11" * 20,
        beneficiary_address="0x" + "22" * 20,
        token_address=cfg.token_address.lower(),
        amount=5_000_000,
        action=SettlementAction.RELEASE.value,
        status=SettlementStatus.AUTHORIZED.value,
        transaction_intent_id=intent.id,
    )
    db_session.add(settlement)
    await db_session.flush()

    reconciler = SettlementReconciliationService(CHAIN_ID_ANVIL)
    stats = await reconciler.reconcile_settlements(db_session)

    assert stats["advanced_to_submitted"] == 1
    assert settlement.status == SettlementStatus.SUBMITTED.value
    assert settlement.submitted_at is not None
    assert settlement.settlement_tx_hash == "0x" + "aa" * 32


@pytest.mark.asyncio
async def test_reconciliation_advances_submitted_to_confirmed(db_session: AsyncSession):
    """Verifies that a SUBMITTED settlement advances to CONFIRMED when a canonical confirmed event exists."""
    cfg = get_chain_config(CHAIN_ID_ANVIL)
    escrow_id = "0x" + "e2" * 32
    tx_hash = "0x" + "bb" * 32
    block_hash = "0x" + "cc" * 32

    # Create canonical confirmed event
    event = BlockchainEvent(
        chain_id=CHAIN_ID_ANVIL,
        contract_address=cfg.contract_addresses["Escrow"].lower(),
        block_number=100,
        block_hash=block_hash,
        transaction_hash=tx_hash,
        transaction_index=0,
        log_index=1,
        event_name="EscrowReleased",
        decoded_data={"escrowId": escrow_id, "amount": 5_000_000},
        status=EventStatus.CONFIRMED.value,
        is_canonical=True,
    )
    db_session.add(event)

    # Create settlement in SUBMITTED state
    settlement = Settlement(
        idempotency_key="settlement:31337:e2:release:1",
        chain_id=CHAIN_ID_ANVIL,
        escrow_contract=cfg.contract_addresses["Escrow"].lower(),
        escrow_id=escrow_id,
        client_address="0x" + "11" * 20,
        beneficiary_address="0x" + "22" * 20,
        token_address=cfg.token_address.lower(),
        amount=5_000_000,
        action=SettlementAction.RELEASE.value,
        status=SettlementStatus.SUBMITTED.value,
        settlement_tx_hash=tx_hash,
    )
    db_session.add(settlement)
    await db_session.flush()

    reconciler = SettlementReconciliationService(CHAIN_ID_ANVIL)
    stats = await reconciler.reconcile_settlements(db_session)

    assert stats["advanced_to_confirmed"] == 1
    assert settlement.status == SettlementStatus.CONFIRMED.value
    assert settlement.confirmed_at is not None
    assert settlement.block_number == 100
    assert settlement.block_hash == block_hash


@pytest.mark.asyncio
async def test_reconciliation_reorg_invalidation(db_session: AsyncSession):
    """Verifies that if a confirmed event is orphaned by a reorg, the settlement confirmation is revoked."""
    cfg = get_chain_config(CHAIN_ID_ANVIL)
    escrow_id = "0x" + "e3" * 32
    tx_hash = "0x" + "ee" * 32

    # Event was previously confirmed, but reorg made it non-canonical
    event = BlockchainEvent(
        chain_id=CHAIN_ID_ANVIL,
        contract_address=cfg.contract_addresses["Escrow"].lower(),
        block_number=100,
        block_hash="0x" + "11" * 32,
        transaction_hash=tx_hash,
        transaction_index=0,
        log_index=0,
        event_name="EscrowReleased",
        decoded_data={"escrowId": escrow_id},
        status=EventStatus.ORPHANED.value,
        is_canonical=False,  # ORPHANED in reorg!
    )
    db_session.add(event)

    # Settlement currently marked CONFIRMED
    settlement = Settlement(
        idempotency_key="settlement:31337:e3:release:1",
        chain_id=CHAIN_ID_ANVIL,
        escrow_contract=cfg.contract_addresses["Escrow"].lower(),
        escrow_id=escrow_id,
        client_address="0x" + "11" * 20,
        beneficiary_address="0x" + "22" * 20,
        token_address=cfg.token_address.lower(),
        amount=5_000_000,
        action=SettlementAction.RELEASE.value,
        status=SettlementStatus.CONFIRMED.value,
        settlement_tx_hash=tx_hash,
        block_number=100,
        block_hash="0x" + "11" * 32,
    )
    db_session.add(settlement)
    await db_session.flush()

    reconciler = SettlementReconciliationService(CHAIN_ID_ANVIL)
    stats = await reconciler.reconcile_settlements(db_session)

    assert stats["reorg_invalidated"] == 1
    assert settlement.status == SettlementStatus.SUBMITTED.value  # Revoked to SUBMITTED
    assert settlement.confirmed_at is None
    assert "reorganization" in settlement.error_message.lower()

    # Verify audit log emitted
    audit_stmt = sa.select(AuditLog).where(
        AuditLog.event_type == AuditEventType.SETTLEMENT_REORG_INVALIDATED.value
    )
    audits = (await db_session.execute(audit_stmt)).scalars().all()
    assert len(audits) >= 1
    assert audits[0].metadata_json["settlement_id"] == str(settlement.id)
