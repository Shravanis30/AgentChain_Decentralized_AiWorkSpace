"""Reconciliation service for Verified Reputation evidence.

Reconciles ReputationEvent records against canonical indexed blockchain events and transaction status.
Guarantees:
- Never marks CONFIRMED merely because transaction receipt exists.
- Requires canonical on-chain ReputationEventRegistered event at or beyond required confirmation depth.
- Detects orphaned events and updates status to REORGED.
- Recalculates ReputationProfile upon status transitions.
"""

import logging
from typing import Any
import uuid
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.base import utc_now
from app.models.blockchain import (
    BlockchainEvent,
    ChainReorganization,
    EventStatus,
    ReorgStatus,
)
from app.models.reputation import (
    ReputationEvent,
    ReputationHistory,
    ReputationStatus,
)
from app.services.blockchain.config import get_chain_config
from app.services.reputation.errors import ReputationError
from app.services.reputation.metrics import ReputationMetrics
from app.services.reputation.utils import uuid_to_bytes32

logger = logging.getLogger(__name__)


def get_required_confirmations(chain_id: int) -> int:
    try:
        config = get_chain_config(chain_id)
        return config.confirmations_required
    except Exception:
        return 32


class ReputationReconciliationService:
    """Reconciles ReputationEvent records against canonical blockchain state."""

    @staticmethod
    async def reconcile_event(
        session: AsyncSession,
        reputation_event_id: uuid.UUID,
    ) -> ReputationEvent:
        """Reconciles a single reputation event against on-chain canonical events."""
        stmt = (
            sa.select(ReputationEvent)
            .where(ReputationEvent.id == reputation_event_id)
            .with_for_update()
        )
        rep_event = (await session.execute(stmt)).scalar_one_or_none()
        if not rep_event:
            raise ReputationError(f"ReputationEvent {reputation_event_id} not found")

        if rep_event.status == ReputationStatus.FAILED.value:
            return rep_event

        now = utc_now()
        required_confs = get_required_confirmations(rep_event.chain_id)

        # 1. Check for active unrecovered reorg
        reorg_stmt = sa.select(ChainReorganization).where(
            ChainReorganization.chain_id == rep_event.chain_id,
            ChainReorganization.status.in_([ReorgStatus.FAILED, ReorgStatus.FAILED.value, "FAILED"]),
        )
        active_reorg = (await session.execute(reorg_stmt)).scalars().first()
        if active_reorg:
            logger.warning(
                "Chain %d has active unrecovered reorg. Halting reconciliation for reputation event %s",
                rep_event.chain_id,
                rep_event.id,
            )
            return rep_event

        # 2. Check if transaction was reorged
        if rep_event.transaction_hash:
            tx_events_stmt = sa.select(BlockchainEvent).where(
                BlockchainEvent.chain_id == rep_event.chain_id,
                BlockchainEvent.transaction_hash == rep_event.transaction_hash,
                BlockchainEvent.event_name == "ReputationEventRegistered",
            )
            tx_events = (await session.execute(tx_events_stmt)).scalars().all()
            if tx_events and all(not ev.is_canonical for ev in tx_events):
                old_status = rep_event.status
                rep_event.is_canonical = False
                rep_event.status = ReputationStatus.REORGED.value
                rep_event.reorged_at = now
                rep_event.updated_at = now
                history = ReputationHistory(
                    reputation_event_id=rep_event.id,
                    from_status=old_status,
                    to_status=ReputationStatus.REORGED.value,
                    reason="Transaction events marked orphaned by blockchain reorg",
                    metadata_json={"transaction_hash": rep_event.transaction_hash},
                    created_at=now,
                )
                session.add(history)
                await session.flush()
                from app.services.blockchain.reputation_projector import ReputationProjector
                await ReputationProjector(rep_event.chain_id).recalculate_profile(session, rep_event.agent_id)
                return rep_event

        # 3. Look up canonical ReputationEventRegistered events for this execution
        exec_b32 = uuid_to_bytes32(rep_event.execution_id)
        events_stmt = (
            sa.select(BlockchainEvent)
            .where(
                BlockchainEvent.chain_id == rep_event.chain_id,
                BlockchainEvent.event_name == "ReputationEventRegistered",
                BlockchainEvent.is_canonical.is_(True),
            )
            .order_by(BlockchainEvent.block_number.asc(), BlockchainEvent.log_index.asc())
        )
        candidate_events = (await session.execute(events_stmt)).scalars().all()
        matching_events = [
            ev for ev in candidate_events
            if ev.decoded_data
            and ev.decoded_data.get("executionId", "").lower() == exec_b32.lower()
        ]

        if not matching_events:
            # Event not yet mined or indexed
            if rep_event.status in (ReputationStatus.PENDING.value, ReputationStatus.SUBMITTED.value):
                # Age calculation
                age = (now - rep_event.created_at).total_seconds()
                ReputationMetrics.record_pending_age(rep_event.chain_id, age)
            return rep_event

        canonical_event = matching_events[-1]
        old_status = rep_event.status

        # Evaluate confirmation status
        new_status = (
            ReputationStatus.CONFIRMED.value
            if canonical_event.status == EventStatus.CONFIRMED
            else (
                ReputationStatus.CONFIRMING.value
                if canonical_event.status == EventStatus.CONFIRMING
                else ReputationStatus.SUBMITTED.value
            )
        )

        rep_event.transaction_hash = canonical_event.transaction_hash
        rep_event.block_number = canonical_event.block_number
        rep_event.block_hash = canonical_event.block_hash
        rep_event.is_canonical = True
        rep_event.confirmations = max(rep_event.confirmations, 1)

        if new_status == ReputationStatus.CONFIRMED.value and not rep_event.confirmed_at:
            rep_event.confirmed_at = now
            ReputationMetrics.record_confirmed(rep_event.chain_id, rep_event.outcome_type)

        if old_status != new_status:
            rep_event.status = new_status
            rep_event.updated_at = now
            history = ReputationHistory(
                reputation_event_id=rep_event.id,
                from_status=old_status,
                to_status=new_status,
                reason=f"Reconciled with canonical event at block {canonical_event.block_number}",
                metadata_json={
                    "event_id": str(canonical_event.id),
                    "block_number": canonical_event.block_number,
                    "transaction_hash": canonical_event.transaction_hash,
                },
                created_at=now,
            )
            session.add(history)
            await session.flush()
            from app.services.blockchain.reputation_projector import ReputationProjector
            await ReputationProjector(rep_event.chain_id).recalculate_profile(session, rep_event.agent_id)

        return rep_event
