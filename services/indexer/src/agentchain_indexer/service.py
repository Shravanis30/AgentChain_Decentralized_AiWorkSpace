"""Continuous blockchain indexing worker loop.

Responsibilities:
- Ingests blocks and logs continuously.
- Strictly validates RPC chain identity on startup and on error.
- Detects reorgs and maintains canonical status.
- Advances block and event confirmation tiers.
- Emits structured logs and metrics.
"""

import asyncio
import logging
from typing import Any
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models.base import utc_now
from app.services.blockchain import (
    BlockTracker,
    EscrowStateProjector,
    EventIndexer,
    ReorgHandler,
    BlockchainMetrics,
    BlockchainRpcClient,
    get_chain_config,
)
from agentchain_indexer.config import IndexerSettings

logger = logging.getLogger(__name__)


class IndexerService:
    """Manages the lifecycle of continuous block and event synchronization."""

    def __init__(self, settings: IndexerSettings) -> None:
        self.settings = settings
        self.chain_config = settings.get_chain_config()
        self.chain_id = self.chain_config.chain_id

        # Database engine & sessionmaker
        self.engine = create_async_engine(
            self.settings.database_url,
            pool_pre_ping=True,
            echo=False,
        )
        self.session_factory = async_sessionmaker(
            self.engine,
            expire_on_commit=False,
            class_=AsyncSession,
        )

        # Core blockchain services
        self.rpc_client = BlockchainRpcClient(self.chain_config)
        self.reorg_handler = ReorgHandler(self.rpc_client)
        self.block_tracker = BlockTracker(self.chain_config, self.reorg_handler)
        self.escrow_projector = EscrowStateProjector(self.chain_id)
        self.event_indexer = EventIndexer(
            config=self.chain_config,
            rpc_client=self.rpc_client,
            block_tracker=self.block_tracker,
            escrow_projector=self.escrow_projector,
        )
        self._running = False

    async def initialize(self) -> None:
        """Fails closed if RPC chain ID does not match configuration."""
        logger.info(
            "Initializing indexer for chain %d (%s) against RPC %s",
            self.chain_id,
            self.chain_config.network_name,
            self.chain_config.rpc_url,
        )
        verified_id = await self.rpc_client.verify_chain_id()
        logger.info("Successfully verified RPC chain ID: %d", verified_id)

    async def step(self) -> int:
        """Executes a single indexing iteration across new blocks.
        
        Returns:
            Number of newly indexed events.
        """
        async with self.session_factory() as session:
            async with session.begin():
                # 1. Determine last indexed canonical block
                latest_db_block = await self.block_tracker.get_latest_canonical_block(session)
                last_indexed_num = latest_db_block.block_number if latest_db_block else 0

                # 2. Get current chain head from RPC
                current_head = await self.rpc_client.get_block_number()
                BlockchainMetrics.set_indexing_lag(self.chain_id, max(0, current_head - last_indexed_num))

                if current_head <= last_indexed_num and latest_db_block is not None:
                    # Up to date, just advance confirmation progression
                    await self.block_tracker.advance_block_confirmations(session, current_head)
                    await self.event_indexer.advance_event_confirmations(session, current_head)
                    return 0

                # 3. Process next batch
                start_block = 0 if latest_db_block is None else last_indexed_num + 1
                end_block = min(current_head, start_block + self.settings.batch_block_size - 1)

                logger.debug(
                    "Indexing block range [%d, %d] on chain %d (head: %d)",
                    start_block,
                    end_block,
                    self.chain_id,
                    current_head,
                )

                events_indexed = await self.event_indexer.index_block_range(
                    session=session,
                    from_block=start_block,
                    to_block=end_block,
                )

                # 4. Advance confirmations
                confirmed_blocks = await self.block_tracker.advance_block_confirmations(session, current_head)
                confirmed_events = await self.event_indexer.advance_event_confirmations(session, current_head)

                # Record metrics
                BlockchainMetrics.record_block_scanned(self.chain_id, (end_block - start_block) + 1)
                if confirmed_blocks > 0:
                    BlockchainMetrics.record_block_confirmed(self.chain_id, confirmed_blocks)

                return events_indexed

    async def run(self) -> None:
        """Main service loop."""
        self._running = True
        await self.initialize()

        logger.info("Starting indexer run loop for chain %d...", self.chain_id)
        while self._running:
            try:
                await self.step()
                await asyncio.sleep(self.settings.poll_interval_seconds)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Error in indexer loop: %s", str(e), exc_info=True)
                BlockchainMetrics.record_rpc_error(self.chain_id, "loop_exception")
                await asyncio.sleep(self.settings.poll_interval_seconds * 2)

    async def stop(self) -> None:
        self._running = False
        await self.rpc_client.close()
        await self.engine.dispose()
        logger.info("Indexer service stopped for chain %d", self.chain_id)
