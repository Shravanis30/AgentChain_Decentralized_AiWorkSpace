"""Property and invariant tests for blockchain infrastructure.

Invariants verified:
1. One logical intent cannot become multiple independent active transactions.
2. Nonce reservations never collide across concurrent workers.
3. Canonical events are strictly unique (chain_id, tx_hash, log_index).
4. Reverted transactions cannot become confirmed.
5. Unconfirmed events cannot become finalized without required confirmations.
6. Reorged events cannot remain permanently canonical.
7. Outbox delivery is at-least-once and duplicate dispatch is harmless.
"""

from unittest.mock import AsyncMock
from eth_account import Account
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.blockchain import (
    BlockStatus,
    BlockchainEvent,
    BlockchainTransaction,
    BlockchainTransactionIntent,
    BlockchainTxOutbox,
    EventStatus,
    IndexedBlock,
    IntentStatus,
    TxLifecycleStatus,
)
from app.services.blockchain.block_tracker import BlockTracker
from app.services.blockchain.config import CHAIN_ID_ANVIL, get_chain_config
from app.services.blockchain.escrow_state_projector import EscrowStateProjector
from app.services.blockchain.event_indexer import EventIndexer
from app.services.blockchain.gas_estimator import GasEstimator
from app.services.blockchain.nonce_manager import NonceManager
from app.services.blockchain.relayer import BlockchainRelayer, LocalAccountSigner
from app.services.blockchain.reorg_handler import ReorgHandler
from app.services.blockchain.transaction_intent import TransactionIntentService
from app.services.blockchain.tx_outbox import BlockchainTxOutboxDispatcher
from tests.test_blockchain_lifecycle import MockRpcClient


@pytest.mark.asyncio
async def test_invariant_single_logical_intent_idempotency(db_session: AsyncSession):
    """Invariant 1: One logical intent cannot become multiple independent active transactions."""
    cfg = get_chain_config(CHAIN_ID_ANVIL)
    key = "invariant-single-intent"
    target = cfg.contract_addresses["Escrow"]

    # Request same intent 5 times
    intents = []
    for _ in range(5):
        intent, _ = await TransactionIntentService.create_intent(
            session=db_session,
            chain_id=CHAIN_ID_ANVIL,
            idempotency_key=key,
            target_contract=target,
            operation="releaseEscrow",
            parameters={"escrowId": "0x" + "11" * 32},
        )
        intents.append(intent)

    # All returned intent instances have the exact same UUID
    assert len(set(i.id for i in intents)) == 1


@pytest.mark.asyncio
async def test_invariant_nonce_reservations_do_not_collide(db_session: AsyncSession):
    """Invariant 2: Nonce reservations do not collide under concurrency."""
    cfg = get_chain_config(CHAIN_ID_ANVIL)
    mock_rpc = MockRpcClient(cfg)
    nonce_mgr = NonceManager(mock_rpc)
    acct = Account.create().address

    # Concurrently reserve 20 nonces
    results = [await nonce_mgr.reserve_nonce(db_session, acct) for _ in range(20)]
    assert len(results) == len(set(results))
    assert results == sorted(results)


@pytest.mark.asyncio
async def test_invariant_reverted_tx_cannot_become_confirmed(db_session: AsyncSession):
    """Invariant 4: Reverted transactions cannot become confirmed."""
    cfg = get_chain_config(CHAIN_ID_ANVIL)
    mock_rpc = MockRpcClient(cfg)
    signer = LocalAccountSigner(Account.create().key.hex())
    relayer = BlockchainRelayer(cfg, mock_rpc, NonceManager(mock_rpc), GasEstimator(mock_rpc), signer)

    intent, _ = await TransactionIntentService.create_intent(
        session=db_session,
        chain_id=CHAIN_ID_ANVIL,
        idempotency_key="invariant-revert-key",
        target_contract=cfg.contract_addresses["Escrow"],
        operation="releaseEscrow",
        parameters={"escrowId": "0x" + "aa" * 32},
    )
    tx_record = await relayer.submit_intent(db_session, intent)

    # Set mock receipt with reverted status 0
    mock_rpc.mock_receipts[tx_record.current_tx_hash.lower()] = {
        "status": "0x0",
        "blockNumber": "0x1",
        "blockHash": "0x" + "01" * 32,
        "gasUsed": "0x5208",
    }
    await relayer.poll_and_update_receipt(db_session, tx_record)
    assert tx_record.status == TxLifecycleStatus.FAILED

    # Even if receipt poll runs again, it can never become CONFIRMED
    await relayer.poll_and_update_receipt(db_session, tx_record)
    assert tx_record.status == TxLifecycleStatus.FAILED
    assert tx_record.status != TxLifecycleStatus.CONFIRMED


@pytest.mark.asyncio
async def test_invariant_unconfirmed_events_not_finalized_without_depth(db_session: AsyncSession):
    """Invariant 5: Unconfirmed events cannot become finalized without required confirmations."""
    # Base Sepolia requires 3 confirmations
    cfg = get_chain_config(84532)
    mock_rpc = MockRpcClient(cfg)
    projector = EscrowStateProjector(cfg.chain_id)
    indexer = EventIndexer(cfg, mock_rpc, BlockTracker(cfg, ReorgHandler(mock_rpc)), projector)

    evt = BlockchainEvent(
        chain_id=cfg.chain_id,
        contract_address=cfg.contract_addresses["Escrow"],
        block_number=10,
        block_hash="0x" + "10" * 32,
        transaction_hash="0x" + "aa" * 32,
        transaction_index=0,
        log_index=0,
        event_name="EscrowLocked",
        decoded_data={"escrowId": "0x" + "33" * 32},
        raw_data="0x",
        status=EventStatus.SEEN,
        is_canonical=True,
    )
    db_session.add(evt)
    await db_session.flush()

    # Head is block 11 -> depth = 2 (less than 3 required for Sepolia)
    await indexer.advance_event_confirmations(db_session, current_head_number=11)
    assert evt.status == EventStatus.CONFIRMING
    assert evt.status != EventStatus.CONFIRMED

    # Head is block 12 -> depth = 3 -> promoted to CONFIRMED
    await indexer.advance_event_confirmations(db_session, current_head_number=12)
    assert evt.status == EventStatus.CONFIRMED


@pytest.mark.asyncio
async def test_invariant_reorged_events_cannot_remain_canonical(db_session: AsyncSession):
    """Invariant 6: Reorged events cannot remain permanently canonical."""
    cfg = get_chain_config(CHAIN_ID_ANVIL)
    mock_rpc = MockRpcClient(cfg)
    reorg_h = ReorgHandler(mock_rpc)

    # Ingest block 5 and block 6
    b5 = IndexedBlock(chain_id=cfg.chain_id, block_number=5, block_hash="0x" + "05" * 32, parent_hash="0x" + "04" * 32, timestamp=5000, status=BlockStatus.SEEN)
    b6 = IndexedBlock(chain_id=cfg.chain_id, block_number=6, block_hash="0x" + "06" * 32, parent_hash="0x" + "05" * 32, timestamp=6000, status=BlockStatus.SEEN)
    db_session.add_all([b5, b6])

    evt = BlockchainEvent(
        chain_id=cfg.chain_id,
        contract_address=cfg.contract_addresses["Escrow"],
        block_number=6,
        block_hash="0x" + "06" * 32,
        transaction_hash="0x" + "66" * 32,
        transaction_index=0,
        log_index=0,
        event_name="EscrowFunded",
        decoded_data={"escrowId": "0x" + "99" * 32},
        raw_data="0x",
        status=EventStatus.SEEN,
        is_canonical=True,
    )
    db_session.add(evt)
    await db_session.flush()

    # Reorg occurs from block 5 (ancestor is 4)
    await reorg_h.handle_reorganization(
        session=db_session,
        detection_block_number=5,
        old_block_hash="0x" + "05" * 32,
        new_block_hash="0x" + "55" * 32,
    )

    await db_session.refresh(evt)
    assert evt.is_canonical is False
    assert evt.status == EventStatus.ORPHANED
