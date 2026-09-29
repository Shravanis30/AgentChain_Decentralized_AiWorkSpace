"""Persistent block tracking with parent-hash validation and confirmation progression.

Guarantees:
- Every block is linked to its parent hash.
- Reorgs are trapped immediately before processing events.
- Blocks transition SEEN -> CONFIRMING -> CONFIRMED based on chain confirmation requirements.
"""

import logging
from typing import Any
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.base import utc_now
from app.models.blockchain import BlockStatus, IndexedBlock
from app.services.blockchain.config import ChainConfig
from app.services.blockchain.reorg_handler import ReorgHandler

logger = logging.getLogger(__name__)


class BlockTracker:
    """Tracks and validates block headers, detecting reorganizations."""

    def __init__(self, config: ChainConfig, reorg_handler: ReorgHandler) -> None:
        self.config = config
        self.chain_id = config.chain_id
        self.confirmations_required = config.confirmations_required
        self.reorg_handler = reorg_handler

    async def get_latest_canonical_block(self, session: AsyncSession) -> IndexedBlock | None:
        """Returns the highest canonical indexed block."""
        stmt = (
            sa.select(IndexedBlock)
            .where(
                IndexedBlock.chain_id == self.chain_id,
                IndexedBlock.is_canonical.is_(True),
            )
            .order_by(IndexedBlock.block_number.desc())
            .limit(1)
        )
        return (await session.execute(stmt)).scalar_one_or_none()

    async def track_block(
        self,
        session: AsyncSession,
        block_dict: dict[str, Any],
    ) -> tuple[IndexedBlock, bool]:
        """Ingests a block header from RPC into the database.
        
        Validates parent_hash against previous block. Triggers reorg recovery if broken.
        Returns:
            (indexed_block, is_newly_inserted)
        """
        block_number = int(block_dict["number"], 16) if isinstance(block_dict["number"], str) else int(block_dict["number"])
        block_hash = str(block_dict["hash"]).lower()
        parent_hash = str(block_dict.get("parentHash", "")).lower()
        timestamp = int(block_dict["timestamp"], 16) if isinstance(block_dict["timestamp"], str) else int(block_dict["timestamp"])

        # 1. Check if exact block already exists by hash (chain_id, block_hash)
        exact_stmt = sa.select(IndexedBlock).where(
            IndexedBlock.chain_id == self.chain_id,
            IndexedBlock.block_hash == block_hash,
        )
        exact_block = (await session.execute(exact_stmt)).scalar_one_or_none()
        if exact_block:
            if exact_block.is_canonical:
                return exact_block, False
            # If it was previously orphaned and is now restored canonical:
            canonical_stmt = sa.select(IndexedBlock).where(
                IndexedBlock.chain_id == self.chain_id,
                IndexedBlock.block_number == block_number,
                IndexedBlock.is_canonical.is_(True),
            )
            canonical_block = (await session.execute(canonical_stmt)).scalar_one_or_none()
            if canonical_block and canonical_block.block_hash.lower() != block_hash:
                await self.reorg_handler.handle_reorganization(
                    session=session,
                    detection_block_number=block_number,
                    old_block_hash=canonical_block.block_hash,
                    new_block_hash=block_hash,
                )
            exact_block.is_canonical = True
            exact_block.status = BlockStatus.SEEN
            exact_block.updated_at = utc_now()
            await session.flush()
            return exact_block, False

        # 2. Check if a different canonical block exists at this height -> Reorganization
        canonical_stmt = sa.select(IndexedBlock).where(
            IndexedBlock.chain_id == self.chain_id,
            IndexedBlock.block_number == block_number,
            IndexedBlock.is_canonical.is_(True),
        )
        canonical_block = (await session.execute(canonical_stmt)).scalar_one_or_none()

        if canonical_block:
            if canonical_block.block_hash.lower() == block_hash:
                return canonical_block, False

            # Existing canonical block has different hash at same height -> Reorganization!
            logger.warning(
                "Block hash mismatch at height %d: canonical %s vs incoming %s",
                block_number,
                canonical_block.block_hash,
                block_hash,
            )
            from app.services.blockchain.metrics import BlockchainMetrics
            BlockchainMetrics.record_block_reorg_conflict(self.chain_id)
            await self.reorg_handler.handle_reorganization(
                session=session,
                detection_block_number=block_number,
                old_block_hash=canonical_block.block_hash,
                new_block_hash=block_hash,
            )

        # 3. Check previous block's parent hash link against canonical history
        if block_number > 0:
            prev_block_stmt = sa.select(IndexedBlock).where(
                IndexedBlock.chain_id == self.chain_id,
                IndexedBlock.block_number == block_number - 1,
                IndexedBlock.is_canonical.is_(True),
            )
            prev_block = (await session.execute(prev_block_stmt)).scalar_one_or_none()

            if prev_block and prev_block.block_hash.lower() != parent_hash:
                logger.warning(
                    "Parent hash mismatch at block %d: expected %s, found %s",
                    block_number,
                    prev_block.block_hash,
                    parent_hash,
                )
                from app.services.blockchain.metrics import BlockchainMetrics
                BlockchainMetrics.record_block_reorg_conflict(self.chain_id)
                await self.reorg_handler.handle_reorganization(
                    session=session,
                    detection_block_number=block_number,
                    old_block_hash=prev_block.block_hash,
                    new_block_hash=parent_hash,
                )

        # 4. Insert newly discovered block as CANONICAL
        new_block = IndexedBlock(
            chain_id=self.chain_id,
            block_number=block_number,
            block_hash=block_hash,
            parent_hash=parent_hash,
            timestamp=timestamp,
            status=BlockStatus.SEEN,
            is_canonical=True,
            first_seen_at=utc_now(),
        )
        session.add(new_block)
        await session.flush()
        return new_block, True

    async def advance_block_confirmations(
        self,
        session: AsyncSession,
        current_head_number: int,
    ) -> int:
        """Promotes blocks to CONFIRMING or CONFIRMED based on head depth."""
        stmt = (
            sa.select(IndexedBlock)
            .where(
                IndexedBlock.chain_id == self.chain_id,
                IndexedBlock.is_canonical.is_(True),
                IndexedBlock.status.in_([BlockStatus.SEEN, BlockStatus.CONFIRMING]),
                IndexedBlock.block_number <= current_head_number,
            )
            .order_by(IndexedBlock.block_number.asc())
        )
        unconfirmed_blocks = (await session.execute(stmt)).scalars().all()
        confirmed_count = 0

        for blk in unconfirmed_blocks:
            depth = (current_head_number - blk.block_number) + 1
            if depth >= self.confirmations_required:
                blk.status = BlockStatus.CONFIRMED
                blk.confirmed_at = utc_now()
                blk.updated_at = utc_now()
                confirmed_count += 1
            elif depth > 1:
                blk.status = BlockStatus.CONFIRMING
                blk.updated_at = utc_now()

        return confirmed_count
