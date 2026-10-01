"""Settlement reconciliation service for Phase 6.2B.

Reconciles settlements against:
- Blockchain transaction intents and attempts.
- Canonical blockchain events and confirmation depths.
- Reorganization invalidations and orphan handling.
- Audit trail updates and Prometheus observability.
"""

from datetime import datetime, timezone
import logging
from typing import Any
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit import AuditEventType, AuditLog
from app.models.base import utc_now
from app.models.blockchain import (
    BlockchainEvent,
    BlockchainTransaction,
    BlockchainTransactionIntent,
    ChainReorganization,
    EscrowChainState,
    EventStatus,
    IntentStatus,
    ReorgStatus,
    TxLifecycleStatus,
)
from app.models.settlement import (
    Settlement,
    SettlementAction,
    SettlementHistory,
    SettlementStatus,
)
from app.services.settlement.config import get_required_confirmations
from app.services.settlement.metrics import SettlementMetrics

logger = logging.getLogger(__name__)

ACTION_TO_EVENT: dict[str, str] = {
    SettlementAction.RELEASE.value: "EscrowReleased",
    SettlementAction.REFUND.value: "EscrowRefunded",
    SettlementAction.DISPUTE_RESOLVE_RELEASE.value: "DisputeResolved",
    SettlementAction.DISPUTE_RESOLVE_REFUND.value: "DisputeResolved",
}


class SettlementReconciliationService:
    """Synchronizes settlement domain state with canonical blockchain infrastructure."""

    def __init__(self, chain_id: int) -> None:
        self.chain_id = chain_id

    async def reconcile_settlements(self, session: AsyncSession) -> dict[str, int]:
        """Runs a complete reconciliation pass across active settlements on this chain."""
        stats = {
            "advanced_to_submitted": 0,
            "advanced_to_confirmed": 0,
            "marked_failed": 0,
            "reorg_invalidated": 0,
        }

        # 1. Reconcile AUTHORIZED -> SUBMITTED
        stats["advanced_to_submitted"] = await self._reconcile_authorized(session)

        # 2. Reconcile SUBMITTED -> CONFIRMED / FAILED
        stats["advanced_to_confirmed"], stats["marked_failed"] = await self._reconcile_submitted(session)

        # 3. Check CONFIRMED for reorg invalidation
        stats["reorg_invalidated"] = await self._reconcile_confirmed_for_reorg(session)

        return stats

    async def _reconcile_authorized(self, session: AsyncSession) -> int:
        """Transitions AUTHORIZED settlements to SUBMITTED if an on-chain transaction exists."""
        stmt = (
            sa.select(Settlement)
            .where(
                Settlement.chain_id == self.chain_id,
                Settlement.status == SettlementStatus.AUTHORIZED.value,
                Settlement.transaction_intent_id.isnot(None),
            )
            .with_for_update(skip_locked=True)
        )
        settlements = (await session.execute(stmt)).scalars().all()
        count = 0

        for s in settlements:
            # Check intent status
            intent_stmt = sa.select(BlockchainTransactionIntent).where(
                BlockchainTransactionIntent.id == s.transaction_intent_id
            )
            intent = (await session.execute(intent_stmt)).scalar_one_or_none()
            if not intent:
                continue

            # Check if transaction was created
            tx_stmt = sa.select(BlockchainTransaction).where(
                BlockchainTransaction.intent_id == intent.id
            )
            tx = (await session.execute(tx_stmt)).scalar_one_or_none()

            now = utc_now()
            if tx or intent.status in (IntentStatus.SUBMITTED.value, IntentStatus.PENDING.value):
                s.status = SettlementStatus.SUBMITTED.value
                s.submitted_at = now
                if tx and tx.current_tx_hash:
                    s.settlement_tx_hash = tx.current_tx_hash
                s.updated_at = now

                session.add(
                    SettlementHistory(
                        settlement_id=s.id,
                        from_status=SettlementStatus.AUTHORIZED.value,
                        to_status=SettlementStatus.SUBMITTED.value,
                        reason="Transaction broadcast observed; settlement marked SUBMITTED",
                        metadata_json={
                            "intent_id": str(intent.id),
                            "tx_hash": s.settlement_tx_hash,
                        },
                        created_at=now,
                    )
                )
                session.add(
                    AuditLog(
                        user_id=s.user_id,
                        event_type=AuditEventType.SETTLEMENT_SUBMITTED.value,
                        timestamp=now,
                        metadata_json={
                            "settlement_id": str(s.id),
                            "tx_hash": s.settlement_tx_hash,
                        },
                    )
                )
                SettlementMetrics.record_submitted(self.chain_id, s.action)
                count += 1
            elif intent.status == IntentStatus.FAILED.value:
                s.status = SettlementStatus.FAILED.value
                s.failed_at = now
                s.error_message = intent.error_message or "Transaction intent failed before submission"
                s.updated_at = now

                session.add(
                    SettlementHistory(
                        settlement_id=s.id,
                        from_status=SettlementStatus.AUTHORIZED.value,
                        to_status=SettlementStatus.FAILED.value,
                        reason=s.error_message,
                        metadata_json={"intent_id": str(intent.id)},
                        created_at=now,
                    )
                )
                SettlementMetrics.record_failed(self.chain_id, s.action, "intent_failed")

        await session.flush()
        return count

    async def _reconcile_submitted(self, session: AsyncSession) -> tuple[int, int]:
        """Transitions SUBMITTED settlements to CONFIRMED or FAILED based on canonical events."""
        stmt = (
            sa.select(Settlement)
            .where(
                Settlement.chain_id == self.chain_id,
                Settlement.status == SettlementStatus.SUBMITTED.value,
            )
            .with_for_update(skip_locked=True)
        )
        settlements = (await session.execute(stmt)).scalars().all()
        confirmed_count = 0
        failed_count = 0

        for s in settlements:
            target_event_name = ACTION_TO_EVENT.get(s.action)
            if not target_event_name:
                continue

            # Query canonical confirmed event for this escrow on the trusted contract
            clean_escrow = s.escrow_id.lower()
            evt_stmt = (
                sa.select(BlockchainEvent)
                .where(
                    BlockchainEvent.chain_id == self.chain_id,
                    BlockchainEvent.contract_address.ilike(s.escrow_contract),
                    BlockchainEvent.event_name == target_event_name,
                    BlockchainEvent.is_canonical.is_(True),
                )
                .order_by(BlockchainEvent.block_number.desc(), BlockchainEvent.log_index.desc())
            )
            events = (await session.execute(evt_stmt)).scalars().all()

            # Find matching event for this specific escrowId
            matching_event: BlockchainEvent | None = None
            for evt in events:
                evt_escrow = (evt.decoded_data or {}).get("escrowId", "")
                if evt_escrow.lower() == clean_escrow:
                    matching_event = evt
                    break

            now = utc_now()
            if matching_event and matching_event.status == EventStatus.CONFIRMED.value:
                # Event is canonical and confirmed!
                s.status = SettlementStatus.CONFIRMED.value
                s.confirmed_at = now
                s.settlement_tx_hash = matching_event.transaction_hash
                s.block_number = matching_event.block_number
                s.block_hash = matching_event.block_hash
                s.confirmations = get_required_confirmations(self.chain_id)
                s.updated_at = now

                session.add(
                    SettlementHistory(
                        settlement_id=s.id,
                        from_status=SettlementStatus.SUBMITTED.value,
                        to_status=SettlementStatus.CONFIRMED.value,
                        reason=f"Canonical event '{matching_event.event_name}' confirmed at block {matching_event.block_number}",
                        metadata_json={
                            "tx_hash": matching_event.transaction_hash,
                            "block_number": matching_event.block_number,
                            "block_hash": matching_event.block_hash,
                        },
                        created_at=now,
                    )
                )
                session.add(
                    AuditLog(
                        user_id=s.user_id,
                        event_type=AuditEventType.SETTLEMENT_CONFIRMED.value,
                        timestamp=now,
                        metadata_json={
                            "settlement_id": str(s.id),
                            "tx_hash": matching_event.transaction_hash,
                            "block_number": matching_event.block_number,
                        },
                    )
                )
                SettlementMetrics.record_confirmed(self.chain_id, s.action)
                confirmed_count += 1
                continue

            # Check if intent / transaction has irrecoverably failed
            if s.transaction_intent_id:
                intent_stmt = sa.select(BlockchainTransactionIntent).where(
                    BlockchainTransactionIntent.id == s.transaction_intent_id
                )
                intent = (await session.execute(intent_stmt)).scalar_one_or_none()
                if intent and intent.status == IntentStatus.FAILED.value:
                    s.status = SettlementStatus.FAILED.value
                    s.failed_at = now
                    s.error_message = intent.error_message or "Transaction failed on-chain"
                    s.updated_at = now

                    session.add(
                        SettlementHistory(
                            settlement_id=s.id,
                            from_status=SettlementStatus.SUBMITTED.value,
                            to_status=SettlementStatus.FAILED.value,
                            reason=s.error_message,
                            metadata_json={"intent_id": str(intent.id)},
                            created_at=now,
                        )
                    )
                    session.add(
                        AuditLog(
                            user_id=s.user_id,
                            event_type=AuditEventType.SETTLEMENT_FAILED.value,
                            timestamp=now,
                            metadata_json={
                                "settlement_id": str(s.id),
                                "error": s.error_message,
                            },
                        )
                    )
                    SettlementMetrics.record_failed(self.chain_id, s.action, "tx_failed")
                    failed_count += 1

        await session.flush()
        return confirmed_count, failed_count

    async def _reconcile_confirmed_for_reorg(self, session: AsyncSession) -> int:
        """Detects if previously confirmed settlement events became orphaned during a reorg."""
        stmt = (
            sa.select(Settlement)
            .where(
                Settlement.chain_id == self.chain_id,
                Settlement.status == SettlementStatus.CONFIRMED.value,
                Settlement.settlement_tx_hash.isnot(None),
            )
            .with_for_update(skip_locked=True)
        )
        settlements = (await session.execute(stmt)).scalars().all()
        reorg_count = 0

        for s in settlements:
            # Check if there is an active failed deep reorg on this chain
            deep_reorg_stmt = sa.select(ChainReorganization).where(
                ChainReorganization.chain_id == self.chain_id,
                ChainReorganization.status.in_([ReorgStatus.FAILED, ReorgStatus.FAILED.value, "FAILED"]),
            )
            deep_failed = (await session.execute(deep_reorg_stmt)).scalars().first()
            if deep_failed:
                logger.critical(
                    "Deep reorganization failure on chain %d. Blocking settlement %s.",
                    self.chain_id,
                    s.id,
                )
                now = utc_now()
                s.status = SettlementStatus.BLOCKED.value
                s.blocked_reason = "Deep chain reorganization exceeded tracking window. Manual intervention required."
                s.updated_at = now
                session.add(
                    SettlementHistory(
                        settlement_id=s.id,
                        from_status=SettlementStatus.CONFIRMED.value,
                        to_status=SettlementStatus.BLOCKED.value,
                        reason=s.blocked_reason,
                        created_at=now,
                    )
                )
                SettlementMetrics.record_authorization_blocked(s.action, "deep_reorg_failure")
                reorg_count += 1
                continue

            # Check the event in blockchain_events
            evt_stmt = sa.select(BlockchainEvent).where(
                BlockchainEvent.chain_id == self.chain_id,
                BlockchainEvent.contract_address.ilike(s.escrow_contract),
                BlockchainEvent.transaction_hash == s.settlement_tx_hash,
            )
            events = (await session.execute(evt_stmt)).scalars().all()

            # If all events for this tx hash are now non-canonical (orphaned)
            if events and all(not evt.is_canonical for evt in events):
                logger.warning(
                    "Reorg detected for confirmed settlement %s! Transaction %s is now orphaned.",
                    s.id,
                    s.settlement_tx_hash,
                )
                now = utc_now()
                s.status = SettlementStatus.SUBMITTED.value  # Roll back to SUBMITTED to await canonical re-confirmation or repair
                s.confirmed_at = None
                s.error_message = "Settlement invalidated by chain reorganization (event orphaned)"
                s.updated_at = now

                session.add(
                    SettlementHistory(
                        settlement_id=s.id,
                        from_status=SettlementStatus.CONFIRMED.value,
                        to_status=SettlementStatus.SUBMITTED.value,
                        reason=s.error_message,
                        metadata_json={"orphaned_tx_hash": s.settlement_tx_hash},
                        created_at=now,
                    )
                )
                session.add(
                    AuditLog(
                        user_id=s.user_id,
                        event_type=AuditEventType.SETTLEMENT_REORG_INVALIDATED.value,
                        timestamp=now,
                        metadata_json={
                            "settlement_id": str(s.id),
                            "orphaned_tx_hash": s.settlement_tx_hash,
                        },
                    )
                )
                SettlementMetrics.record_reorg_invalidated(self.chain_id)
                reorg_count += 1

        await session.flush()
        return reorg_count
