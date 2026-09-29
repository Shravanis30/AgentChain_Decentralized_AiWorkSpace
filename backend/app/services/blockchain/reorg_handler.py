"""Chain reorganization detection, isolation, and rollback handler.

When parent_hash relationship breaks:
1. Walks back blocks via RPC to locate the common ancestor.
2. Creates an audit record in `chain_reorganizations`.
3. Marks orphaned blocks in `indexed_blocks` as ORPHANED.
4. Marks orphaned events in `blockchain_events` as is_canonical=False, status=ORPHANED.
5. Rewinds/recalculates derived `escrow_chain_state` projections.
"""

import logging
from typing import Any
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.base import utc_now
from app.models.blockchain import (
    BlockStatus,
    ChainReorganization,
    EscrowChainState,
    EventStatus,
    IndexedBlock,
    BlockchainEvent,
    ReorgStatus,
)
from app.services.blockchain.errors import DeepReorganizationError
from app.services.blockchain.notarization_projector import NotarizationProjector
from app.services.blockchain.reputation_projector import ReputationProjector
from app.services.blockchain.rpc_client import BlockchainRpcClient

logger = logging.getLogger(__name__)


class ReorgHandler:
    """Manages blockchain reorganization detection and rollback recovery."""

    def __init__(self, rpc_client: BlockchainRpcClient, max_reorg_depth: int = 32) -> None:
        self.rpc_client = rpc_client
        self.chain_id = rpc_client.chain_id
        self.max_reorg_depth = max_reorg_depth
        self._notarization_projector = NotarizationProjector(self.chain_id)
        self._reputation_projector = ReputationProjector(self.chain_id)

    async def find_common_ancestor(
        self,
        session: AsyncSession,
        divergent_block_number: int,
    ) -> tuple[int, str]:
        """Walks backward through history comparing RPC block hashes with database hashes.
        
        Returns:
            (common_ancestor_number, common_ancestor_hash)
        """
        curr_number = divergent_block_number - 1
        while curr_number >= 0:
            db_block_stmt = sa.select(IndexedBlock).where(
                IndexedBlock.chain_id == self.chain_id,
                IndexedBlock.block_number == curr_number,
                IndexedBlock.is_canonical.is_(True),
            )
            db_block = (await session.execute(db_block_stmt)).scalar_one_or_none()
            if not db_block:
                # If we don't have this block, we can't walk back further
                rpc_block = await self.rpc_client.get_block_by_number(curr_number)
                rpc_hash = rpc_block["hash"] if rpc_block else ""
                return curr_number, rpc_hash

            rpc_block = await self.rpc_client.get_block_by_number(curr_number)
            if not rpc_block:
                raise RuntimeError(f"RPC failed to return block {curr_number} during reorg investigation")

            rpc_hash = rpc_block["hash"].lower()
            if db_block.block_hash.lower() == rpc_hash:
                return curr_number, rpc_hash

            curr_number -= 1

        return 0, ""

    async def handle_reorganization(
        self,
        session: AsyncSession,
        detection_block_number: int,
        old_block_hash: str,
        new_block_hash: str,
    ) -> ChainReorganization:
        """Executes full reorg recovery: isolates orphaned data and restores canonical projections."""
        # Concurrency safety: advisory lock on PostgreSQL prevents concurrent conflicting reorg handling
        bind = session.get_bind()
        if bind and getattr(bind.dialect, "name", "") == "postgresql":
            try:
                await session.execute(
                    sa.text("SELECT pg_advisory_xact_lock(hashtext(:lock_key))"),
                    {"lock_key": f"chain_reorg_{self.chain_id}"},
                )
            except Exception as lock_err:
                logger.debug("Advisory lock not acquired or unsupported: %s", lock_err)

        logger.warning(
            "Reorganization detected on chain %d at block %d: expected parent %s, found %s",
            self.chain_id,
            detection_block_number,
            old_block_hash,
            new_block_hash,
        )

        ancestor_num, ancestor_hash = await self.find_common_ancestor(session, detection_block_number)
        depth = detection_block_number - ancestor_num

        if depth > self.max_reorg_depth:
            err_msg = (
                f"Reorganization depth {depth} exceeds maximum tracking window {self.max_reorg_depth}. "
                f"Automatic recovery halted. Operator intervention required."
            )
            logger.critical(
                "DEEP REORGANIZATION DETECTED on chain %d: %s",
                self.chain_id,
                err_msg,
            )
            reorg_record = ChainReorganization(
                chain_id=self.chain_id,
                detection_block_number=detection_block_number,
                old_block_hash=old_block_hash,
                new_block_hash=new_block_hash,
                common_ancestor_number=ancestor_num,
                common_ancestor_hash=ancestor_hash,
                depth=depth,
                affected_blocks_count=0,
                affected_events_count=0,
                status=ReorgStatus.FAILED,
                error_message=err_msg,
                detected_at=utc_now(),
            )
            session.add(reorg_record)
            await session.flush()
            raise DeepReorganizationError(err_msg)

        reorg_record = ChainReorganization(
            chain_id=self.chain_id,
            detection_block_number=detection_block_number,
            old_block_hash=old_block_hash,
            new_block_hash=new_block_hash,
            common_ancestor_number=ancestor_num,
            common_ancestor_hash=ancestor_hash,
            depth=depth,
            affected_blocks_count=0,
            affected_events_count=0,
            status=ReorgStatus.DETECTED,
            detected_at=utc_now(),
        )
        session.add(reorg_record)
        await session.flush()

        try:
            # 1. Mark orphaned blocks: MUST set is_canonical = False to satisfy partial unique index
            orphan_blocks_stmt = (
                sa.update(IndexedBlock)
                .where(
                    IndexedBlock.chain_id == self.chain_id,
                    IndexedBlock.block_number > ancestor_num,
                    IndexedBlock.is_canonical.is_(True),
                )
                .values(
                    is_canonical=False,
                    status=BlockStatus.ORPHANED,
                    updated_at=utc_now(),
                )
            )
            block_res = await session.execute(orphan_blocks_stmt)
            affected_blocks = block_res.rowcount

            # 2. Identify affected events and mark non-canonical
            affected_events_stmt = sa.select(BlockchainEvent).where(
                BlockchainEvent.chain_id == self.chain_id,
                BlockchainEvent.block_number > ancestor_num,
                BlockchainEvent.is_canonical.is_(True),
            )
            affected_events = (await session.execute(affected_events_stmt)).scalars().all()

            affected_escrow_ids: set[str] = set()
            affected_exec_ids: set[str] = set()
            for evt in affected_events:
                if evt.decoded_data and "escrowId" in evt.decoded_data:
                    affected_escrow_ids.add(evt.decoded_data["escrowId"])
                if evt.decoded_data and "executionId" in evt.decoded_data:
                    affected_exec_ids.add(evt.decoded_data["executionId"])

            orphan_events_stmt = (
                sa.update(BlockchainEvent)
                .where(
                    BlockchainEvent.chain_id == self.chain_id,
                    BlockchainEvent.block_number > ancestor_num,
                    BlockchainEvent.is_canonical.is_(True),
                )
                .values(
                    is_canonical=False,
                    status=EventStatus.ORPHANED,
                    updated_at=utc_now(),
                )
            )
            event_res = await session.execute(orphan_events_stmt)
            affected_events_count = event_res.rowcount

            # 3. Recalculate derived escrow state for affected escrows
            for escrow_id in affected_escrow_ids:
                await self._reproject_escrow(session, escrow_id)

            # 4. Recalculate derived notarization state for affected executions
            for exec_id_hex in affected_exec_ids:
                await self._notarization_projector.reproject_notarization(session, exec_id_hex)

            # 5. Recalculate derived reputation state for affected executions
            for exec_id_hex in affected_exec_ids:
                await self._reputation_projector.reproject_reputation(session, exec_id_hex)

            reorg_record.affected_blocks_count = affected_blocks
            reorg_record.affected_events_count = affected_events_count
            reorg_record.status = ReorgStatus.RESOLVED
            reorg_record.resolved_at = utc_now()
            reorg_record.updated_at = utc_now()

            from app.services.blockchain.metrics import BlockchainMetrics
            BlockchainMetrics.record_reorg(self.chain_id, depth)
            BlockchainMetrics.record_canonical_block_replacement(self.chain_id)
            if affected_events_count > 0:
                BlockchainMetrics.record_orphaned_event(self.chain_id, affected_events_count)

            logger.info(
                "Successfully resolved chain %d reorg: depth=%d, %d blocks orphaned, %d events orphaned",
                self.chain_id,
                depth,
                affected_blocks,
                affected_events_count,
            )
            return reorg_record

        except Exception as e:
            reorg_record.status = ReorgStatus.FAILED
            reorg_record.error_message = str(e)
            reorg_record.updated_at = utc_now()
            logger.error("Failed to resolve chain reorg: %s", str(e), exc_info=True)
            raise

    async def _reproject_escrow(self, session: AsyncSession, escrow_id: str) -> None:
        """Re-evaluates the derived on-chain state for a given escrow from its canonical events."""
        canonical_events_stmt = (
            sa.select(BlockchainEvent)
            .where(
                BlockchainEvent.chain_id == self.chain_id,
                BlockchainEvent.is_canonical.is_(True),
            )
            .order_by(BlockchainEvent.block_number.asc(), BlockchainEvent.log_index.asc())
        )
        all_events = (await session.execute(canonical_events_stmt)).scalars().all()
        escrow_events = [
            e for e in all_events
            if e.decoded_data and e.decoded_data.get("escrowId", "").lower() == escrow_id.lower()
        ]

        state_stmt = sa.select(EscrowChainState).where(
            EscrowChainState.chain_id == self.chain_id,
            EscrowChainState.escrow_id == escrow_id,
        )
        escrow_state = (await session.execute(state_stmt)).scalar_one_or_none()

        if not escrow_events:
            # If all events for this escrow were orphaned, mark derived state as non-canonical
            if escrow_state:
                escrow_state.is_canonical = False
                escrow_state.updated_at = utc_now()
            return

        # Re-derive from canonical events
        state_map = {
            "EscrowCreated": 0,
            "EscrowFunded": 1,
            "EscrowLocked": 2,
            "EscrowReleased": 3,
            "EscrowRefunded": 4,
            "DisputeOpened": 5,
            "DisputeResolved": 6,
        }

        first_evt = escrow_events[0]
        latest_evt = escrow_events[-1]
        decoded = first_evt.decoded_data

        if not escrow_state:
            escrow_state = EscrowChainState(
                chain_id=self.chain_id,
                escrow_id=escrow_id,
                contract_address=first_evt.contract_address,
                client=decoded.get("client", ""),
                developer=decoded.get("beneficiary", ""),
                token="",
                amount=int(decoded.get("amount", 0)),
                reference_id=decoded.get("referenceId", ""),
                salt=str(decoded.get("salt", "0")),
                current_chain_state=state_map.get(latest_evt.event_name, 0),
                creation_tx_hash=first_evt.transaction_hash,
                latest_tx_hash=latest_evt.transaction_hash,
                latest_block_number=latest_evt.block_number,
                latest_block_hash=latest_evt.block_hash,
                is_canonical=True,
                confirmation_status=latest_evt.status,
                last_reconciliation_at=utc_now(),
            )
            session.add(escrow_state)
        else:
            escrow_state.current_chain_state = state_map.get(latest_evt.event_name, escrow_state.current_chain_state)
            escrow_state.latest_tx_hash = latest_evt.transaction_hash
            escrow_state.latest_block_number = latest_evt.block_number
            escrow_state.latest_block_hash = latest_evt.block_hash
            escrow_state.is_canonical = True
            escrow_state.confirmation_status = latest_evt.status
            escrow_state.last_reconciliation_at = utc_now()
            escrow_state.updated_at = utc_now()
