"""Blockchain event indexer with deduplication and confirmation lifecycle management.

Guarantees:
- Deduplication key: (chain_id, transaction_hash, log_index).
- Repeated polling is completely idempotent.
- Events transition SEEN -> CONFIRMING -> CONFIRMED based on depth.
- Only canonical events update derived EscrowChainState.
"""

import logging
from typing import Any
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.base import utc_now
from app.models.blockchain import (
    BlockStatus,
    BlockchainEvent,
    EventStatus,
    IndexedBlock,
)
from app.services.blockchain.abi import decode_escrow_log, normalize_hex
from app.services.blockchain.block_tracker import BlockTracker
from app.services.blockchain.config import ChainConfig
from app.services.blockchain.escrow_state_projector import EscrowStateProjector
from app.services.blockchain.notarization_projector import NotarizationProjector
from app.services.blockchain.reputation_projector import ReputationProjector
from app.services.blockchain.rpc_client import BlockchainRpcClient

logger = logging.getLogger(__name__)


class EventIndexer:
    """Indexes smart contract events from logs with strict deduplication."""

    def __init__(
        self,
        config: ChainConfig,
        rpc_client: BlockchainRpcClient,
        block_tracker: BlockTracker,
        escrow_projector: EscrowStateProjector,
        notarization_projector: NotarizationProjector | None = None,
        reputation_projector: ReputationProjector | None = None,
    ) -> None:
        self.config = config
        self.chain_id = config.chain_id
        self.rpc_client = rpc_client
        self.block_tracker = block_tracker
        self.escrow_projector = escrow_projector
        self.notarization_projector = notarization_projector or NotarizationProjector(self.chain_id)
        self.reputation_projector = reputation_projector or ReputationProjector(self.chain_id)

    async def index_block_range(
        self,
        session: AsyncSession,
        from_block: int,
        to_block: int,
    ) -> int:
        """Fetches and indexes blocks and events in the specified range.
        
        Returns:
            Number of newly indexed events.
        """
        configured_addrs = [
            addr for addr in self.config.contract_addresses.values()
            if addr and addr != "0x0000000000000000000000000000000000000000"
        ]
        target_address = configured_addrs if len(configured_addrs) > 1 else (configured_addrs[0] if configured_addrs else None)

        # 1. Ingest all block headers in range if not already tracked
        for num in range(from_block, to_block + 1):
            block_data = await self.rpc_client.get_block_by_number(num)
            if block_data:
                await self.block_tracker.track_block(session, block_data)

        # 2. Fetch logs across range
        logs = await self.rpc_client.get_logs(
            from_block=from_block,
            to_block=to_block,
            address=target_address,
        )

        new_events_count = 0

        for log in logs:
            tx_hash = str(log["transactionHash"]).lower()
            log_index = int(log["logIndex"], 16) if isinstance(log["logIndex"], str) else int(log["logIndex"])
            block_num = int(log["blockNumber"], 16) if isinstance(log["blockNumber"], str) else int(log["blockNumber"])
            block_hash = str(log["blockHash"]).lower()
            tx_index = int(log.get("transactionIndex", 0), 16) if isinstance(log.get("transactionIndex", 0), str) else int(log.get("transactionIndex", 0))

            # Deduplication check: (chain_id, block_hash, transaction_hash, log_index)
            dedup_stmt = sa.select(BlockchainEvent.id).where(
                BlockchainEvent.chain_id == self.chain_id,
                BlockchainEvent.block_hash == block_hash,
                BlockchainEvent.transaction_hash == tx_hash,
                BlockchainEvent.log_index == log_index,
            )
            existing_event = (await session.execute(dedup_stmt)).scalar_one_or_none()
            if existing_event is not None:
                from app.services.blockchain.metrics import BlockchainMetrics
                BlockchainMetrics.record_duplicate_event(self.chain_id)
                continue

            # Decode event
            decoded = decode_escrow_log(log)
            if not decoded:
                continue
            event_name, decoded_args = decoded

            # Determine initial confirmation status and canonical status based on block
            block_stmt = sa.select(IndexedBlock).where(
                IndexedBlock.chain_id == self.chain_id,
                IndexedBlock.block_hash == block_hash,
            )
            blk = (await session.execute(block_stmt)).scalar_one_or_none()
            is_canonical = blk.is_canonical if blk else True
            init_status = EventStatus.SEEN
            if blk:
                if blk.status == BlockStatus.ORPHANED or not blk.is_canonical:
                    init_status = EventStatus.ORPHANED
                    is_canonical = False
                elif blk.status == BlockStatus.CONFIRMED:
                    init_status = EventStatus.CONFIRMED
                elif blk.status == BlockStatus.CONFIRMING:
                    init_status = EventStatus.CONFIRMING

            raw_topics = [normalize_hex(t) for t in log.get("topics", [])]
            raw_data = normalize_hex(log.get("data", "0x"))

            event_record = BlockchainEvent(
                chain_id=self.chain_id,
                contract_address=str(log.get("address", "")).lower(),
                block_number=block_num,
                block_hash=block_hash,
                transaction_hash=tx_hash,
                transaction_index=tx_index,
                log_index=log_index,
                event_name=event_name,
                event_signature=raw_topics[0] if raw_topics else None,
                decoded_data=decoded_args,
                raw_topics=raw_topics,
                raw_data=raw_data,
                status=init_status,
                is_canonical=is_canonical,
                first_seen_at=utc_now(),
                confirmed_at=utc_now() if init_status == EventStatus.CONFIRMED else None,
            )
            session.add(event_record)
            await session.flush()

            # Apply to derived state only if canonical
            if is_canonical:
                await self.escrow_projector.apply_event(session, event_record)
                await self.notarization_projector.apply_event(session, event_record)
                await self.reputation_projector.apply_event(session, event_record)
            
            from app.services.blockchain.metrics import BlockchainMetrics
            BlockchainMetrics.record_event_indexed(self.chain_id, event_name)
            new_events_count += 1

        return new_events_count

    async def advance_event_confirmations(
        self,
        session: AsyncSession,
        current_head_number: int,
    ) -> int:
        """Promotes events from SEEN -> CONFIRMING -> CONFIRMED."""
        stmt = (
            sa.select(BlockchainEvent)
            .where(
                BlockchainEvent.chain_id == self.chain_id,
                BlockchainEvent.is_canonical.is_(True),
                BlockchainEvent.status.in_([EventStatus.SEEN, EventStatus.CONFIRMING]),
                BlockchainEvent.block_number <= current_head_number,
            )
            .order_by(BlockchainEvent.block_number.asc())
        )
        unconfirmed_events = (await session.execute(stmt)).scalars().all()
        confirmed_count = 0

        for evt in unconfirmed_events:
            depth = (current_head_number - evt.block_number) + 1
            if depth >= self.config.confirmations_required:
                evt.status = EventStatus.CONFIRMED
                evt.confirmed_at = utc_now()
                evt.updated_at = utc_now()
                await self.escrow_projector.sync_confirmation_status(session, evt)
                await self.notarization_projector.sync_confirmation_status(session, evt)
                await self.reputation_projector.sync_confirmation_status(session, evt)
                confirmed_count += 1
            elif depth > 1:
                evt.status = EventStatus.CONFIRMING
                evt.updated_at = utc_now()
                await self.escrow_projector.sync_confirmation_status(session, evt)
                await self.notarization_projector.sync_confirmation_status(session, evt)
                await self.reputation_projector.sync_confirmation_status(session, evt)

        return confirmed_count
