"""Phase 6.2A.1 Hardening Tests — Issue 1: Reorg-Compatible Block Persistence.

Invariants Verified:
1. Multiple historical block records can coexist at the same (chain_id, block_number).
2. ONLY ONE canonical block is permitted per (chain_id, block_number), enforced by DB partial unique index.
3. Attempting to insert two canonical blocks at the same height raises a database constraint failure.
4. Multiple orphaned blocks can coexist at the same height for audit / historical preservation.
5. Reorg followed by service restart preserves the correct canonical block.
6. Reorg concurrency between multiple workers resolves safely to one coherent canonical result.
"""

import asyncio
import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.base import utc_now
from app.models.blockchain import BlockStatus, IndexedBlock
from app.services.blockchain.block_tracker import BlockTracker
from app.services.blockchain.config import get_chain_config
from app.services.blockchain.reorg_handler import ReorgHandler
from app.services.blockchain.rpc_client import BlockchainRpcClient


class MockRpcForBlocks(BlockchainRpcClient):
    """Mock RPC for testing block reorg and ancestor retrieval."""

    def __init__(self, config):
        super().__init__(config)
        self.blocks_by_num = {}

    async def get_block_by_number(self, block_number: int, full_tx: bool = False):
        return self.blocks_by_num.get(block_number)


@pytest.mark.asyncio
async def test_a_same_height_block_reorg_preserves_both_rows(db_session: AsyncSession):
    """Test A:
    Insert chain=84532, height=100, hash=AAA, canonical.
    Then reorg to height=100, hash=BBB, canonical.
    Expected: AAA -> ORPHANED, BBB -> CANONICAL, both rows preserved in indexed_blocks.
    """
    chain_id = 84532
    cfg = get_chain_config(chain_id)
    mock_rpc = MockRpcForBlocks(cfg)
    reorg_h = ReorgHandler(mock_rpc)
    tracker = BlockTracker(cfg, reorg_h)

    hash_99 = "0x" + "99" * 32
    hash_aaa = "0x" + "aa" * 32
    hash_bbb = "0x" + "bb" * 32

    # Common ancestor block 99
    mock_rpc.blocks_by_num[99] = {
        "number": "0x63",
        "hash": hash_99,
        "parentHash": "0x" + "98" * 32,
        "timestamp": "0x1000",
    }
    await tracker.track_block(db_session, mock_rpc.blocks_by_num[99])

    # Canonical block 100 / AAA
    b100_aaa = {
        "number": "0x64",
        "hash": hash_aaa,
        "parentHash": hash_99,
        "timestamp": "0x1001",
    }
    blk_aaa, is_new_aaa = await tracker.track_block(db_session, b100_aaa)
    assert is_new_aaa is True
    assert blk_aaa.is_canonical is True
    assert blk_aaa.status == BlockStatus.SEEN

    # Reorg occurs: incoming block 100 / BBB with different hash at same height
    mock_rpc.blocks_by_num[100] = {
        "number": "0x64",
        "hash": hash_bbb,
        "parentHash": hash_99,
        "timestamp": "0x1002",
    }
    blk_bbb, is_new_bbb = await tracker.track_block(db_session, mock_rpc.blocks_by_num[100])
    assert is_new_bbb is True

    # Query all records at chain=84532, height=100
    stmt = (
        sa.select(IndexedBlock)
        .where(
            IndexedBlock.chain_id == chain_id,
            IndexedBlock.block_number == 100,
        )
        .order_by(IndexedBlock.created_at.asc())
    )
    rows = (await db_session.execute(stmt)).scalars().all()

    # BOTH rows preserved
    assert len(rows) == 2

    row_aaa = next(r for r in rows if r.block_hash == hash_aaa)
    row_bbb = next(r for r in rows if r.block_hash == hash_bbb)

    # AAA is ORPHANED and non-canonical
    assert row_aaa.status == BlockStatus.ORPHANED
    assert row_aaa.is_canonical is False

    # BBB is CANONICAL and SEEN
    assert row_bbb.status == BlockStatus.SEEN
    assert row_bbb.is_canonical is True


@pytest.mark.asyncio
async def test_b_two_canonical_blocks_same_height_raises_database_constraint(db_session: AsyncSession):
    """Test B:
    Attempt to create two canonical blocks at the same height.
    Expected: DATABASE CONSTRAINT FAILURE (IntegrityError) via partial unique index.
    """
    chain_id = 84532
    height = 200
    hash_1 = "0x" + "21" * 32
    hash_2 = "0x" + "22" * 32

    # Insert first canonical block
    blk1 = IndexedBlock(
        chain_id=chain_id,
        block_number=height,
        block_hash=hash_1,
        parent_hash="0x" + "20" * 32,
        timestamp=2000,
        status=BlockStatus.SEEN,
        is_canonical=True,
        first_seen_at=utc_now(),
    )
    db_session.add(blk1)
    await db_session.flush()

    # Attempt to insert second canonical block at the SAME chain and height
    blk2 = IndexedBlock(
        chain_id=chain_id,
        block_number=height,
        block_hash=hash_2,
        parent_hash="0x" + "20" * 32,
        timestamp=2001,
        status=BlockStatus.SEEN,
        is_canonical=True,
        first_seen_at=utc_now(),
    )
    db_session.add(blk2)

    with pytest.raises(IntegrityError):
        await db_session.flush()

    await db_session.rollback()


@pytest.mark.asyncio
async def test_c_multiple_orphaned_blocks_allowed_at_same_height(db_session: AsyncSession):
    """Test C:
    Allow multiple orphaned blocks at the same height for historical audit preservation.
    """
    chain_id = 84532
    height = 300
    hash_orphan_1 = "0x" + "31" * 32
    hash_orphan_2 = "0x" + "32" * 32
    hash_canonical = "0x" + "33" * 32

    # Add first orphaned block
    db_session.add(IndexedBlock(
        chain_id=chain_id,
        block_number=height,
        block_hash=hash_orphan_1,
        parent_hash="0x" + "30" * 32,
        timestamp=3000,
        status=BlockStatus.ORPHANED,
        is_canonical=False,
        first_seen_at=utc_now(),
    ))

    # Add second orphaned block at the SAME height
    db_session.add(IndexedBlock(
        chain_id=chain_id,
        block_number=height,
        block_hash=hash_orphan_2,
        parent_hash="0x" + "30" * 32,
        timestamp=3001,
        status=BlockStatus.ORPHANED,
        is_canonical=False,
        first_seen_at=utc_now(),
    ))

    # Add the current canonical block at the same height
    db_session.add(IndexedBlock(
        chain_id=chain_id,
        block_number=height,
        block_hash=hash_canonical,
        parent_hash="0x" + "30" * 32,
        timestamp=3002,
        status=BlockStatus.SEEN,
        is_canonical=True,
        first_seen_at=utc_now(),
    ))

    # All three must flush successfully without violating database constraints
    await db_session.flush()

    stmt = sa.select(IndexedBlock).where(
        IndexedBlock.chain_id == chain_id,
        IndexedBlock.block_number == height,
    )
    blocks = (await db_session.execute(stmt)).scalars().all()
    assert len(blocks) == 3

    canonical_blocks = [b for b in blocks if b.is_canonical]
    orphaned_blocks = [b for b in blocks if not b.is_canonical]

    assert len(canonical_blocks) == 1
    assert canonical_blocks[0].block_hash == hash_canonical
    assert len(orphaned_blocks) == 2


@pytest.mark.asyncio
async def test_d_reorg_followed_by_restart_preserves_correct_canonical_block(db_session: AsyncSession):
    """Test D:
    Reorg followed by restart must preserve the correct canonical block.
    """
    chain_id = 84532
    cfg = get_chain_config(chain_id)
    mock_rpc = MockRpcForBlocks(cfg)
    reorg_h = ReorgHandler(mock_rpc)
    tracker = BlockTracker(cfg, reorg_h)

    h50 = "0x" + "50" * 32
    h51_old = "0x" + "51" * 32
    h51_new = "0x" + "99" * 32

    # Ingest block 50
    mock_rpc.blocks_by_num[50] = {"number": "0x32", "hash": h50, "parentHash": "0x" + "49" * 32, "timestamp": "0x500"}
    await tracker.track_block(db_session, mock_rpc.blocks_by_num[50])

    # Ingest block 51 old
    mock_rpc.blocks_by_num[51] = {"number": "0x33", "hash": h51_old, "parentHash": h50, "timestamp": "0x501"}
    await tracker.track_block(db_session, mock_rpc.blocks_by_num[51])

    # Reorg at block 51
    mock_rpc.blocks_by_num[51] = {"number": "0x33", "hash": h51_new, "parentHash": h50, "timestamp": "0x502"}
    await tracker.track_block(db_session, mock_rpc.blocks_by_num[51])

    await db_session.flush()

    # Simulate service restart: instantiate fresh BlockTracker
    new_rpc = MockRpcForBlocks(cfg)
    new_reorg_h = ReorgHandler(new_rpc)
    restarted_tracker = BlockTracker(cfg, new_reorg_h)

    latest_canonical = await restarted_tracker.get_latest_canonical_block(db_session)
    assert latest_canonical is not None
    assert latest_canonical.block_number == 51
    assert latest_canonical.block_hash == h51_new
    assert latest_canonical.is_canonical is True


@pytest.mark.asyncio
async def test_reorg_concurrency_workers_produce_single_coherent_canonical_result(db_session: AsyncSession):
    """Concurrency Test:
    Two indexer workers encounter the same reorg simultaneously.
    Expected: One coherent canonical result, exactly one canonical block, no database corruption.
    """
    chain_id = 84532
    cfg = get_chain_config(chain_id)
    mock_rpc = MockRpcForBlocks(cfg)
    reorg_h1 = ReorgHandler(mock_rpc)
    reorg_h2 = ReorgHandler(mock_rpc)
    tracker1 = BlockTracker(cfg, reorg_h1)
    tracker2 = BlockTracker(cfg, reorg_h2)

    h60 = "0x" + "60" * 32
    h61_old = "0x" + "61" * 32
    h61_new = "0x" + "77" * 32

    mock_rpc.blocks_by_num[60] = {"number": "0x3c", "hash": h60, "parentHash": "0x" + "59" * 32, "timestamp": "0x600"}
    mock_rpc.blocks_by_num[61] = {"number": "0x3d", "hash": h61_old, "parentHash": h60, "timestamp": "0x601"}

    await tracker1.track_block(db_session, mock_rpc.blocks_by_num[60])
    await tracker1.track_block(db_session, mock_rpc.blocks_by_num[61])
    await db_session.flush()

    # New fork block header
    new_block_data = {"number": "0x3d", "hash": h61_new, "parentHash": h60, "timestamp": "0x602"}
    mock_rpc.blocks_by_num[61] = new_block_data

    # Worker 1 encounters reorg and handles it
    res1, is_new1 = await tracker1.track_block(db_session, new_block_data)
    assert res1.is_canonical is True

    # Worker 2 encounters the exact same incoming block
    res2, is_new2 = await tracker2.track_block(db_session, new_block_data)
    assert res2.is_canonical is True
    assert is_new2 is False  # Recognized as already canonical

    # Invariant: exactly one canonical block at height 61
    canonical_stmt = sa.select(IndexedBlock).where(
        IndexedBlock.chain_id == chain_id,
        IndexedBlock.block_number == 61,
        IndexedBlock.is_canonical.is_(True),
    )
    canonicals = (await db_session.execute(canonical_stmt)).scalars().all()
    assert len(canonicals) == 1
    assert canonicals[0].block_hash == h61_new
