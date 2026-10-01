import logging
from typing import Any
import uuid

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit import AuditEventType, AuditLog
from app.models.base import utc_now
from app.models.blockchain import (
    BlockchainEvent,
    BlockchainTransaction,
    ChainReorganization,
    EventStatus,
    ReorgStatus,
)
from app.models.distribution import (
    Distribution,
    DistributionHistory,
    DistributionStatus,
)
from app.services.blockchain.config import (
    get_chain_config,
)
from app.services.distribution.errors import DistributionNotFoundError
from app.services.distribution.metrics import DistributionMetrics

logger = logging.getLogger(__name__)


def get_required_confirmations(chain_id: int) -> int:
    try:
        config = get_chain_config(chain_id)
        return config.confirmations_required
    except Exception:
        return 32


class DistributionReconciliationService:
    """Reconciles Distribution records against canonical indexed blockchain events and transaction status."""

    @staticmethod
    async def reconcile_distribution(
        session: AsyncSession,
        distribution_id: uuid.UUID,
    ) -> Distribution:
        stmt = (
            sa.select(Distribution)
            .where(Distribution.id == distribution_id)
            .with_for_update()
        )
        dist = (await session.execute(stmt)).scalar_one_or_none()
        if not dist:
            raise DistributionNotFoundError(f"Distribution {distribution_id} not found")

        # Skip terminal states that are not reorg-recoverable
        if dist.status in (DistributionStatus.CANCELLED.value, DistributionStatus.BLOCKED.value):
            return dist

        now = utc_now()
        required_confs = get_required_confirmations(dist.chain_id)

        # 1. Check for active unrecovered reorg on this chain (fail-closed)
        reorg_stmt = sa.select(ChainReorganization).where(
            ChainReorganization.chain_id == dist.chain_id,
            ChainReorganization.status.in_([ReorgStatus.FAILED, ReorgStatus.FAILED.value, "FAILED"]),
        )
        active_reorg = (await session.execute(reorg_stmt)).scalars().first()
        if active_reorg:
            logger.warning(
                "Chain %d has active unrecovered reorg. Halting reconciliation for distribution %s",
                dist.chain_id,
                dist.id,
            )
            return dist

        # 2. Check if previously confirmed distribution was reorged (orphaned)
        if dist.status == DistributionStatus.CONFIRMED.value and dist.distribution_tx_hash:
            tx_event_stmt = sa.select(BlockchainEvent).where(
                BlockchainEvent.chain_id == dist.chain_id,
                BlockchainEvent.transaction_hash == dist.distribution_tx_hash,
            )
            tx_events = (await session.execute(tx_event_stmt)).scalars().all()
            if tx_events and all(not ev.is_canonical for ev in tx_events):
                logger.warning(
                    "Reorg detected for confirmed distribution %s! Transaction %s is now orphaned.",
                    dist.id,
                    dist.distribution_tx_hash,
                )
                prev_status = dist.status
                dist.status = DistributionStatus.REORGED.value
                dist.error_message = f"Reorganized: transaction {dist.distribution_tx_hash} was orphaned"
                dist.confirmed_at = None
                dist.updated_at = now

                session.add(
                    DistributionHistory(
                        distribution_id=dist.id,
                        from_status=prev_status,
                        to_status=DistributionStatus.REORGED.value,
                        reason=dist.error_message,
                        metadata_json={"orphaned_tx_hash": dist.distribution_tx_hash},
                        created_at=now,
                    )
                )
                session.add(
                    AuditLog(
                        event_type=AuditEventType.DISTRIBUTION_REORGED.value,
                        timestamp=now,
                        metadata_json={
                            "distribution_id": str(dist.id),
                            "reason": dist.error_message,
                        },
                    )
                )
                await session.flush()
                DistributionMetrics.record_reorged(dist.chain_id)
                DistributionMetrics.record_reconciliation(dist.chain_id, "reorged")
                return dist

        # 3. Look for canonical on-chain DistributionExecuted or EscrowReleased event matching escrow

        event_stmt = sa.select(BlockchainEvent).where(
            BlockchainEvent.chain_id == dist.chain_id,
            BlockchainEvent.event_name.in_(["DistributionExecuted", "EscrowReleased"]),
            BlockchainEvent.is_canonical.is_(True),
        ).order_by(BlockchainEvent.block_number.desc(), BlockchainEvent.log_index.desc())
        events = (await session.execute(event_stmt)).scalars().all()

        clean_escrow = dist.escrow_id.lower()
        canonical_event = None
        for ev in events:
            ev_escrow = str((ev.decoded_data or {}).get("escrowId", "")).lower()
            if ev_escrow == clean_escrow:
                canonical_event = ev
                break

        # 4. Handle canonical event found
        if canonical_event:
            dist.block_number = canonical_event.block_number
            dist.block_hash = canonical_event.block_hash
            dist.distribution_tx_hash = canonical_event.transaction_hash
            dist.updated_at = now

            if canonical_event.status == EventStatus.CONFIRMED.value:
                if dist.status != DistributionStatus.CONFIRMED.value:
                    prev_status = dist.status
                    dist.status = DistributionStatus.CONFIRMED.value
                    dist.confirmed_at = now

                    session.add(
                        DistributionHistory(
                            distribution_id=dist.id,
                            from_status=prev_status,
                            to_status=DistributionStatus.CONFIRMED.value,
                            reason=f"Canonical distribution event confirmed at block {canonical_event.block_number}",
                            metadata_json={
                                "block_number": canonical_event.block_number,
                                "tx_hash": canonical_event.transaction_hash,
                            },
                            created_at=now,
                        )
                    )
                    session.add(
                        AuditLog(
                            event_type=AuditEventType.DISTRIBUTION_CONFIRMED.value,
                            timestamp=now,
                            metadata_json={
                                "distribution_id": str(dist.id),
                                "tx_hash": canonical_event.transaction_hash,
                                "block_number": canonical_event.block_number,
                            },
                        )
                    )
                    DistributionMetrics.record_confirmed(dist.chain_id, dist.distribution_version)
                    DistributionMetrics.record_reconciliation(dist.chain_id, "confirmed")
            else:
                if dist.status != DistributionStatus.SUBMITTED.value:
                    dist.status = DistributionStatus.SUBMITTED.value
                DistributionMetrics.record_reconciliation(dist.chain_id, "awaiting_confirmations")
            await session.flush()
            return dist


        await session.flush()
        DistributionMetrics.record_reconciliation(dist.chain_id, "no_change")
        return dist


