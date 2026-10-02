"""Phase 6.2A.1 Hardening Tests — Issue 2: Reorg-Compatible Event Identity & Deduplication.

Invariants Verified:
1. Invariant 1 (duplicate polling): Repeated polling of the exact same blockchain log produces exactly one database event (idempotent).
2. Invariant 2 (reorg compatibility): An event from an orphaned block does NOT prevent the canonical fork's event from being indexed.
3. Invariant 3 (historical preservation): Orphaned events remain preserved in the database for audit/reorg analysis.
4. Invariant 4 (canonical uniqueness): Two conflicting canonical representations at the same (chain_id, tx_hash, log_index) violate the partial unique index.
5. Invariant 5 (deterministic projection): escrow_chain_state is derived exclusively from canonical events; orphaned events are cleanly un-projected.
"""

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from web3 import Web3

from app.models.base import utc_now
from app.models.blockchain import (
    BlockStatus,
    BlockchainEvent,
    EscrowChainState,
    EventStatus,
    IndexedBlock,
)
from app.services.blockchain.block_tracker import BlockTracker
from app.services.blockchain.config import CHAIN_ID_ANVIL, get_chain_config
from app.services.blockchain.escrow_state_projector import EscrowStateProjector
from app.services.blockchain.event_indexer import EventIndexer
from app.services.blockchain.reorg_handler import ReorgHandler
from app.services.blockchain.rpc_client import BlockchainRpcClient


class MockRpcForEvents(BlockchainRpcClient):
    """Mock RPC for testing block ranges and log fetching."""

    def __init__(self, config):
        super().__init__(config)
        self.blocks = {}
        self.logs = []

    async def get_block_by_number(self, block_number: int, full_tx: bool = False):
        return self.blocks.get(block_number)

    async def get_logs(self, from_block: int, to_block: int, address=None, topics=None):
        return [
            log for log in self.logs
            if from_block <= int(log["blockNumber"], 16) <= to_block
        ]


def _build_escrow_created_log(
    contract_addr: str,
    escrow_id: str,
    client: str,
    beneficiary: str,
    amount: int,
    block_num: int,
    block_hash: str,
    tx_hash: str,
    log_index: int = 0,
) -> dict:
    sig = "EscrowCreated(bytes32,address,address,uint256,bytes32,uint256)"
    topic0 = "0x" + Web3.keccak(text=sig).hex()
    padded_client = "0x" + "00" * 12 + client.lower().removeprefix("0x")
    padded_ben = "0x" + "00" * 12 + beneficiary.lower().removeprefix("0x")
    data = "0x" + hex(amount)[2:].zfill(64) + ("aa" * 32) + hex(1000)[2:].zfill(64)

    return {
        "address": contract_addr,
        "topics": [topic0, escrow_id, padded_client, padded_ben],
        "data": data,
        "blockNumber": hex(block_num),
        "blockHash": block_hash,
        "transactionHash": tx_hash,
        "transactionIndex": "0x0",
        "logIndex": hex(log_index),
    }


@pytest.mark.asyncio
async def test_invariant_1_duplicate_polling_is_strictly_idempotent(db_session: AsyncSession):
    """Invariant 1: Repeated polling of the exact same blockchain log produces exactly one database event."""
    cfg = get_chain_config(CHAIN_ID_ANVIL)
    mock_rpc = MockRpcForEvents(cfg)
    reorg_h = ReorgHandler(mock_rpc)
    tracker = BlockTracker(cfg, reorg_h)
    projector = EscrowStateProjector(cfg.chain_id)
    indexer = EventIndexer(cfg, mock_rpc, tracker, projector)

    b_hash = "0x" + "11" * 32
    tx_hash = "0x" + "aa" * 32
    escrow_id = "0x" + "ee" * 32
    client_addr = "0x" + "11" * 20
    ben_addr = "0x" + "22" * 20

    mock_rpc.blocks[10] = {
        "number": "0xa",
        "hash": b_hash,
        "parentHash": "0x" + "09" * 32,
        "timestamp": "0x1000",
    }
    log_item = _build_escrow_created_log(
        cfg.contract_addresses["Escrow"],
        escrow_id,
        client_addr,
        ben_addr,
        amount=1_000_000,
        block_num=10,
        block_hash=b_hash,
        tx_hash=tx_hash,
        log_index=0,
    )
    mock_rpc.logs.append(log_item)

    # First polling pass: indexes 1 event
    count1 = await indexer.index_block_range(db_session, 10, 10)
    assert count1 == 1

    # Second polling pass with identical logs: indexes 0 events (idempotent)
    count2 = await indexer.index_block_range(db_session, 10, 10)
    assert count2 == 0

    # Third polling pass: still 0
    count3 = await indexer.index_block_range(db_session, 10, 10)
    assert count3 == 0

    # Exactly 1 event row exists in database
    evt_stmt = sa.select(BlockchainEvent).where(
        BlockchainEvent.chain_id == cfg.chain_id,
        BlockchainEvent.block_hash == b_hash,
        BlockchainEvent.transaction_hash == tx_hash,
        BlockchainEvent.log_index == 0,
    )
    events = (await db_session.execute(evt_stmt)).scalars().all()
    assert len(events) == 1


@pytest.mark.asyncio
async def test_reorg_fork_a_orphaned_and_fork_b_canonical_projection(db_session: AsyncSession):
    """Invariants 2, 3, 5:
    Fork A has Block 100 with EscrowCreated (escrow_id = E1).
    Reorg occurs: Fork B has Block 100' (different hash) with EscrowCreated (escrow_id = E2).
    Verify:
    - Fork A event is marked ORPHANED and non-canonical (preserved historically).
    - Fork B event is indexed as CANONICAL without DB conflict.
    - Derived escrow_chain_state reflects ONLY Fork B (E1 is non-canonical, E2 is canonical).
    """
    cfg = get_chain_config(CHAIN_ID_ANVIL)
    mock_rpc = MockRpcForEvents(cfg)
    reorg_h = ReorgHandler(mock_rpc)
    tracker = BlockTracker(cfg, reorg_h)
    projector = EscrowStateProjector(cfg.chain_id)
    indexer = EventIndexer(cfg, mock_rpc, tracker, projector)

    h99 = "0x" + "99" * 32
    h100_fork_a = "0x" + "fa" * 32
    h100_fork_b = "0x" + "fb" * 32

    tx_fork_a = "0x" + "a1" * 32
    tx_fork_b = "0x" + "b1" * 32

    e1_id = "0x" + "01" * 32
    e2_id = "0x" + "02" * 32

    client_addr = "0x" + "11" * 20
    ben_addr = "0x" + "22" * 20

    # Common ancestor 99
    mock_rpc.blocks[99] = {"number": "0x63", "hash": h99, "parentHash": "0x" + "98" * 32, "timestamp": "0x900"}
    await tracker.track_block(db_session, mock_rpc.blocks[99])

    # === FORK A ===
    mock_rpc.blocks[100] = {"number": "0x64", "hash": h100_fork_a, "parentHash": h99, "timestamp": "0x1000"}
    mock_rpc.logs = [
        _build_escrow_created_log(
            cfg.contract_addresses["Escrow"],
            e1_id,
            client_addr,
            ben_addr,
            amount=500_000,
            block_num=100,
            block_hash=h100_fork_a,
            tx_hash=tx_fork_a,
            log_index=0,
        )
    ]
    indexed_a = await indexer.index_block_range(db_session, 100, 100)
    assert indexed_a == 1

    # Verify initial projection of E1
    state_e1_stmt = sa.select(EscrowChainState).where(EscrowChainState.escrow_id == e1_id)
    state_e1 = (await db_session.execute(state_e1_stmt)).scalar_one_or_none()
    assert state_e1 is not None
    assert state_e1.is_canonical is True
    assert state_e1.amount == 500_000

    # === REORG TO FORK B ===
    # Node now returns Fork B block at height 100 with different hash and event E2
    mock_rpc.blocks[100] = {"number": "0x64", "hash": h100_fork_b, "parentHash": h99, "timestamp": "0x1001"}
    mock_rpc.logs = [
        _build_escrow_created_log(
            cfg.contract_addresses["Escrow"],
            e2_id,
            client_addr,
            ben_addr,
            amount=750_000,
            block_num=100,
            block_hash=h100_fork_b,
            tx_hash=tx_fork_b,
            log_index=0,
        )
    ]

    # Index Fork B: BlockTracker will detect hash mismatch at height 100, trigger reorg handler,
    # mark Fork A block and events as ORPHANED, then insert Fork B block and index Fork B event!
    indexed_b = await indexer.index_block_range(db_session, 100, 100)
    assert indexed_b == 1

    # --- Verify Invariant 3: Historical Preservation ---
    # Both events exist in the database!
    all_events_stmt = sa.select(BlockchainEvent).order_by(BlockchainEvent.created_at.asc())
    all_events = (await db_session.execute(all_events_stmt)).scalars().all()
    assert len(all_events) == 2

    evt_fork_a = next(e for e in all_events if e.block_hash == h100_fork_a)
    evt_fork_b = next(e for e in all_events if e.block_hash == h100_fork_b)

    # Fork A event is preserved as ORPHANED and non-canonical
    assert evt_fork_a.status == EventStatus.ORPHANED
    assert evt_fork_a.is_canonical is False
    assert evt_fork_a.decoded_data["escrowId"] == e1_id

    # Fork B event is indexed as CANONICAL
    assert evt_fork_b.is_canonical is True
    assert evt_fork_b.status == EventStatus.SEEN
    assert evt_fork_b.decoded_data["escrowId"] == e2_id

    # --- Verify Invariant 5: Deterministic Canonical Projection ---
    # E1 derived state was marked non-canonical during reorg
    await db_session.refresh(state_e1)
    assert state_e1.is_canonical is False

    # E2 derived state is canonical and correctly projected
    state_e2_stmt = sa.select(EscrowChainState).where(EscrowChainState.escrow_id == e2_id)
    state_e2 = (await db_session.execute(state_e2_stmt)).scalar_one_or_none()
    assert state_e2 is not None
    assert state_e2.is_canonical is True
    assert state_e2.amount == 750_000


@pytest.mark.asyncio
async def test_invariant_4_canonical_event_uniqueness_db_constraint(db_session: AsyncSession):
    """Invariant 4:
    The database must enforce that there cannot be two canonical events with the same
    (chain_id, transaction_hash, log_index).
    """
    chain_id = CHAIN_ID_ANVIL
    tx_hash = "0x" + "cc" * 32
    log_index = 0

    # Insert first canonical event
    evt1 = BlockchainEvent(
        chain_id=chain_id,
        contract_address="0x" + "11" * 20,
        block_number=50,
        block_hash="0x" + "50" * 32,
        transaction_hash=tx_hash,
        transaction_index=0,
        log_index=log_index,
        event_name="EscrowCreated",
        decoded_data={"escrowId": "0x01"},
        status=EventStatus.SEEN,
        is_canonical=True,
        first_seen_at=utc_now(),
    )
    db_session.add(evt1)
    await db_session.flush()

    # Attempt to insert second canonical event with the same (chain_id, tx_hash, log_index)
    evt2 = BlockchainEvent(
        chain_id=chain_id,
        contract_address="0x" + "11" * 20,
        block_number=50,
        block_hash="0x" + "51" * 32,  # different block hash
        transaction_hash=tx_hash,      # SAME tx_hash
        transaction_index=0,
        log_index=log_index,          # SAME log_index
        event_name="EscrowCreated",
        decoded_data={"escrowId": "0x01"},
        status=EventStatus.SEEN,
        is_canonical=True,             # ALSO CANONICAL!
        first_seen_at=utc_now(),
    )
    db_session.add(evt2)

    with pytest.raises(IntegrityError):
        await db_session.flush()

    await db_session.rollback()
