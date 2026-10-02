"""Integration tests for BlockTracker, EventIndexer, Reorganization recovery, and Reconciliation."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from web3 import Web3

from app.models.blockchain import (
    BlockStatus,
    BlockchainEvent,
    ChainReorganization,
    EscrowChainState,
    EventStatus,
    IndexedBlock,
    ReorgStatus,
)
from app.services.blockchain.block_tracker import BlockTracker
from app.services.blockchain.config import CHAIN_ID_ANVIL, get_chain_config
from app.services.blockchain.escrow_state_projector import EscrowStateProjector
from app.services.blockchain.event_indexer import EventIndexer
from app.services.blockchain.reconciliation import BlockchainReconciliationService
from app.services.blockchain.reorg_handler import ReorgHandler
from app.services.blockchain.rpc_client import BlockchainRpcClient


class MockIndexerRpc(BlockchainRpcClient):
    """Mock RPC for testing block tracking, reorgs, and event logs."""

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


@pytest.mark.asyncio
async def test_block_ingestion_and_confirmations(db_session: AsyncSession):
    """Verify sequential block tracking and depth-based confirmation promotion."""
    cfg = get_chain_config(CHAIN_ID_ANVIL)
    mock_rpc = MockIndexerRpc(cfg)
    reorg_h = ReorgHandler(mock_rpc)
    tracker = BlockTracker(cfg, reorg_h)

    b0_hash = "0x" + "00" * 32
    b1_hash = "0x" + "01" * 32
    b2_hash = "0x" + "02" * 32

    # Ingest Block 0
    b0_data = {"number": "0x0", "hash": b0_hash, "parentHash": "0x" + "ff" * 32, "timestamp": "0x6000"}
    blk0, is_new0 = await tracker.track_block(db_session, b0_data)
    assert is_new0 is True
    assert blk0.status == BlockStatus.SEEN

    # Ingest Block 1 with parent pointing to Block 0
    b1_data = {"number": "0x1", "hash": b1_hash, "parentHash": b0_hash, "timestamp": "0x6001"}
    blk1, is_new1 = await tracker.track_block(db_session, b1_data)
    assert is_new1 is True

    # Advance confirmations at head = 1 (Anvil requires 1 confirmation)
    confirmed_count = await tracker.advance_block_confirmations(db_session, current_head_number=1)
    assert confirmed_count >= 1
    assert blk0.status == BlockStatus.CONFIRMED


@pytest.mark.asyncio
async def test_event_indexing_and_deduplication(db_session: AsyncSession):
    """Verify event log ingestion, deduplication, and derived EscrowChainState projection."""
    cfg = get_chain_config(CHAIN_ID_ANVIL)
    mock_rpc = MockIndexerRpc(cfg)
    reorg_h = ReorgHandler(mock_rpc)
    tracker = BlockTracker(cfg, reorg_h)
    projector = EscrowStateProjector(cfg.chain_id)
    indexer = EventIndexer(cfg, mock_rpc, tracker, projector)

    escrow_id = "0x" + "aa" * 32
    client_addr = "0x1111111111111111111111111111111111111111"
    ben_addr = "0x2222222222222222222222222222222222222222"
    ref_id = "0x" + "bb" * 32
    b10_hash = "0x" + "10" * 32

    mock_rpc.blocks[10] = {
        "number": "0xa",
        "hash": b10_hash,
        "parentHash": "0x" + "09" * 32,
        "timestamp": "0x7000",
    }

    # Construct EscrowCreated event
    sig = "EscrowCreated(bytes32,address,address,uint256,bytes32,uint256)"
    topic0 = "0x" + Web3.keccak(text=sig).hex()
    data = "0x" + hex(1_000_000)[2:].zfill(64) + ref_id.removeprefix("0x") + hex(1_900_000)[2:].zfill(64)

    mock_rpc.logs.append({
        "address": cfg.contract_addresses["Escrow"],
        "topics": [
            topic0,
            escrow_id,
            "0x" + ("00" * 12) + client_addr[2:].lower(),
            "0x" + ("00" * 12) + ben_addr[2:].lower(),
        ],
        "data": data,
        "blockNumber": "0xa",
        "blockHash": b10_hash,
        "transactionHash": "0x" + "ee" * 32,
        "transactionIndex": "0x0",
        "logIndex": "0x0",
    })

    # 1. Index range [10, 10]
    count1 = await indexer.index_block_range(db_session, 10, 10)
    assert count1 == 1

    # Verify EscrowChainState derived row
    state_stmt = EscrowChainState.__table__.select().where(EscrowChainState.escrow_id == escrow_id)
    escrow_state = (await db_session.execute(state_stmt)).one()
    assert escrow_state.current_chain_state == 0  # CREATED
    assert escrow_state.amount == 1_000_000
    assert escrow_state.client.lower() == client_addr.lower()
    assert escrow_state.developer.lower() == ben_addr.lower()
    assert escrow_state.is_canonical is True

    # 2. Re-index range [10, 10] (Deduplication test)
    count2 = await indexer.index_block_range(db_session, 10, 10)
    assert count2 == 0  # Deduplicated, harmless


@pytest.mark.asyncio
async def test_reorganization_detection_and_recovery(db_session: AsyncSession):
    """Verify chain reorganization detection, orphaning of blocks/events, and state replay."""
    cfg = get_chain_config(CHAIN_ID_ANVIL)
    mock_rpc = MockIndexerRpc(cfg)
    reorg_h = ReorgHandler(mock_rpc)
    tracker = BlockTracker(cfg, reorg_h)
    projector = EscrowStateProjector(cfg.chain_id)
    indexer = EventIndexer(cfg, mock_rpc, tracker, projector)

    # Setup initial canonical chain: Block 10 -> Block 11 -> Block 12
    h10 = "0x" + "10" * 32
    h11 = "0x" + "11" * 32
    h12 = "0x" + "12" * 32

    # Ingest Block 10
    await tracker.track_block(db_session, {"number": "0xa", "hash": h10, "parentHash": "0x" + "09" * 32, "timestamp": "0x1000"})
    # Ingest Block 11
    await tracker.track_block(db_session, {"number": "0xb", "hash": h11, "parentHash": h10, "timestamp": "0x1001"})
    # Ingest Block 12
    await tracker.track_block(db_session, {"number": "0xc", "hash": h12, "parentHash": h11, "timestamp": "0x1002"})

    # Setup an event in Block 12
    escrow_id = "0x" + "fe" * 32
    sig = "EscrowCreated(bytes32,address,address,uint256,bytes32,uint256)"
    topic0 = "0x" + Web3.keccak(text=sig).hex()
    data = "0x" + hex(500)[2:].zfill(64) + ("aa" * 32) + hex(2000)[2:].zfill(64)

    mock_rpc.blocks[12] = {"number": "0xc", "hash": h12, "parentHash": h11, "timestamp": "0x1002"}
    mock_rpc.logs.append({
        "address": cfg.contract_addresses["Escrow"],
        "topics": [topic0, escrow_id, "0x" + "00" * 12 + "11" * 20, "0x" + "00" * 12 + "22" * 20],
        "data": data,
        "blockNumber": "0xc",
        "blockHash": h12,
        "transactionHash": "0x" + "99" * 32,
        "transactionIndex": "0x0",
        "logIndex": "0x0",
    })
    await indexer.index_block_range(db_session, 12, 12)

    # Verify event is initially canonical
    evt = (await db_session.execute(
        BlockchainEvent.__table__.select().where(BlockchainEvent.block_number == 12)
    )).one()
    assert evt.is_canonical is True

    # Now a REORG occurs: Block 11 reorged!
    # Common ancestor is Block 10.
    new_h11 = "0x" + "ff" * 32
    mock_rpc.blocks[10] = {"number": "0xa", "hash": h10, "parentHash": "0x" + "09" * 32, "timestamp": "0x1000"}
    mock_rpc.blocks[11] = {"number": "0xb", "hash": new_h11, "parentHash": h10, "timestamp": "0x1001"}

    # Track new Block 11 with parent pointing to Block 10, but hash differs from old Block 11!
    reorg_record = await reorg_h.handle_reorganization(
        session=db_session,
        detection_block_number=11,
        old_block_hash=h11,
        new_block_hash=new_h11,
    )

    assert reorg_record.status == ReorgStatus.RESOLVED
    assert reorg_record.common_ancestor_number == 10
    assert reorg_record.depth == 1

    # Verify Block 12 and Block 11 are marked ORPHANED
    orphaned_blocks = (await db_session.execute(
        IndexedBlock.__table__.select().where(IndexedBlock.status == BlockStatus.ORPHANED)
    )).all()
    assert len(orphaned_blocks) >= 1

    # Verify Event in Block 12 is marked non-canonical and ORPHANED
    orphaned_evt = (await db_session.execute(
        BlockchainEvent.__table__.select().where(BlockchainEvent.block_number == 12)
    )).one()
    assert orphaned_evt.is_canonical is False
    assert orphaned_evt.status == EventStatus.ORPHANED


@pytest.mark.asyncio
async def test_reconciliation_detects_block_gaps(db_session: AsyncSession):
    """Verify reconciliation detects missing blocks in indexed_blocks."""
    cfg = get_chain_config(CHAIN_ID_ANVIL)
    mock_rpc = MockIndexerRpc(cfg)
    reconciler = BlockchainReconciliationService(cfg, mock_rpc, None)

    # Ingest block 1 and block 3 (block 2 is missing)
    db_session.add(IndexedBlock(
        chain_id=cfg.chain_id,
        block_number=1,
        block_hash="0x" + "01" * 32,
        parent_hash="0x" + "00" * 32,
        timestamp=1000,
        status=BlockStatus.SEEN,
    ))
    db_session.add(IndexedBlock(
        chain_id=cfg.chain_id,
        block_number=3,
        block_hash="0x" + "03" * 32,
        parent_hash="0x" + "02" * 32,
        timestamp=1002,
        status=BlockStatus.SEEN,
    ))
    await db_session.flush()

    gaps = await reconciler.detect_block_gaps(db_session, lookback_blocks=10)
    assert 2 in gaps
