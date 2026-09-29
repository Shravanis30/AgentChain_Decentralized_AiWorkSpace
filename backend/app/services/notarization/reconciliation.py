"""Reconciliation service for Result Notarizations.

Reconciles ResultNotarization records against canonical indexed blockchain events and transaction status.
Guarantees:
- Never marks CONFIRMED merely because transaction receipt exists.
- Requires canonical on-chain ResultNotarized event at or beyond required confirmation depth.
- Detects orphaned events and updates status to REORGED.
- Preserves original immutable result hash.
"""

import logging
from typing import Any
import uuid
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.base import utc_now
from app.models.blockchain import (
    BlockchainEvent,
    BlockchainTransaction,
    ChainReorganization,
    EventStatus,
    ReorgStatus,
)
from app.models.notarization import (
    NotarizationHistory,
    NotarizationStatus,
    ResultNotarization,
)
from app.services.blockchain.config import get_chain_config
from app.services.notarization.errors import NotarizationNotFoundError
from app.services.notarization.metrics import NotarizationMetrics
from app.services.notarization.utils import (
    bytes32_to_execution_id,
    execution_id_to_bytes32,
)

logger = logging.getLogger(__name__)


def get_required_confirmations(chain_id: int) -> int:
    try:
        config = get_chain_config(chain_id)
        return config.confirmations_required
    except Exception:
        return 32


class NotarizationReconciliationService:
    """Reconciles ResultNotarization records against canonical blockchain state."""

    @staticmethod
    async def reconcile_notarization(
        session: AsyncSession,
        notarization_id: uuid.UUID,
    ) -> ResultNotarization:
        """Reconciles a single notarization record against on-chain canonical events."""
        stmt = (
            sa.select(ResultNotarization)
            .where(ResultNotarization.id == notarization_id)
            .with_for_update()
        )
        notarization = (await session.execute(stmt)).scalar_one_or_none()
        if not notarization:
            raise NotarizationNotFoundError(f"ResultNotarization {notarization_id} not found")

        # Skip failed states
        if notarization.status == NotarizationStatus.FAILED.value:
            return notarization

        now = utc_now()
        required_confs = get_required_confirmations(notarization.chain_id)

        # 1. Fail closed if there is an active unrecovered reorg on this chain
        reorg_stmt = sa.select(ChainReorganization).where(
            ChainReorganization.chain_id == notarization.chain_id,
            ChainReorganization.status.in_([ReorgStatus.FAILED, ReorgStatus.FAILED.value, "FAILED"]),
        )
        active_reorg = (await session.execute(reorg_stmt)).scalars().first()
        if active_reorg:
            logger.warning(
                "Chain %d has active unrecovered reorg. Halting reconciliation for notarization %s",
                notarization.chain_id,
                notarization.id,
            )
            return notarization

        # 2. Check if previously confirmed/tracked notarization was reorged
        if notarization.transaction_hash:
            tx_events_stmt = sa.select(BlockchainEvent).where(
                BlockchainEvent.chain_id == notarization.chain_id,
                BlockchainEvent.transaction_hash == notarization.transaction_hash,
                BlockchainEvent.event_name == "ResultNotarized",
            )
            tx_events = (await session.execute(tx_events_stmt)).scalars().all()
            if tx_events and all(not ev.is_canonical for ev in tx_events):
                logger.warning(
                    "Reorg detected for notarization %s! Transaction %s is now orphaned.",
                    notarization.id,
                    notarization.transaction_hash,
                )
                prev_status = notarization.status
                notarization.status = NotarizationStatus.REORGED.value
                notarization.is_canonical = False
                notarization.error_message = f"Reorganized: transaction {notarization.transaction_hash} was orphaned"
                notarization.reorged_at = now
                notarization.updated_at = now

                session.add(
                    NotarizationHistory(
                        notarization_id=notarization.id,
                        from_status=prev_status,
                        to_status=NotarizationStatus.REORGED.value,
                        reason="Transaction events marked non-canonical due to chain reorganization",
                        metadata_json={"orphaned_tx": notarization.transaction_hash},
                        created_at=now,
                    )
                )
                NotarizationMetrics.record_reorged(notarization.chain_id)
                await session.flush()
                return notarization

        # 3. Look up canonical ResultNotarized event for this execution
        exec_bytes32 = execution_id_to_bytes32(notarization.execution_id)
        canonical_event_stmt = (
            sa.select(BlockchainEvent)
            .where(
                BlockchainEvent.chain_id == notarization.chain_id,
                BlockchainEvent.event_name == "ResultNotarized",
                BlockchainEvent.is_canonical.is_(True),
            )
            .order_by(BlockchainEvent.block_number.desc(), BlockchainEvent.log_index.desc())
        )
        candidates = (await session.execute(canonical_event_stmt)).scalars().all()
        canonical_event = None
        for ev in candidates:
            dec = ev.decoded_data or {}
            ev_exec_id = dec.get("executionId", "")
            if ev_exec_id.lower() == exec_bytes32.lower():
                canonical_event = ev
                break

        # 4. If canonical event exists, verify parameters
        if canonical_event:
            dec = canonical_event.decoded_data or {}
            event_result_hash = dec.get("resultHash", "").removeprefix("0x").lower()

            # Result hash integrity check
            if event_result_hash != notarization.result_hash.lower():
                logger.error(
                    "Result hash mismatch in event for notarization %s: on-chain=%s, expected=%s",
                    notarization.id,
                    event_result_hash,
                    notarization.result_hash,
                )
                NotarizationMetrics.record_verification_mismatch(notarization.chain_id)
                notarization.error_message = f"Hash mismatch: on-chain={event_result_hash}, expected={notarization.result_hash}"
                notarization.updated_at = now
                return notarization

            # Verify contract address
            if canonical_event.contract_address.lower() != notarization.contract_address.lower():
                logger.error(
                    "Contract address mismatch in event for notarization %s: on-chain=%s, expected=%s",
                    notarization.id,
                    canonical_event.contract_address,
                    notarization.contract_address,
                )
                notarization.error_message = "Contract address mismatch"
                notarization.updated_at = now
                return notarization

            # Update block & tx data
            notarization.transaction_hash = canonical_event.transaction_hash
            notarization.block_number = canonical_event.block_number
            notarization.block_hash = canonical_event.block_hash
            notarization.is_canonical = True

            # Determine depth
            from app.models.blockchain import IndexedBlock
            max_block_stmt = sa.select(sa.func.max(IndexedBlock.block_number)).where(
                IndexedBlock.chain_id == notarization.chain_id,
                IndexedBlock.is_canonical.is_(True),
            )
            head_block = (await session.execute(max_block_stmt)).scalar() or canonical_event.block_number
            depth = max(1, (head_block - canonical_event.block_number) + 1)
            notarization.confirmations = depth

            # State transition based on confirmation depth
            if depth >= required_confs or canonical_event.status == EventStatus.CONFIRMED.value:
                if notarization.status != NotarizationStatus.CONFIRMED.value:
                    prev_status = notarization.status
                    notarization.status = NotarizationStatus.CONFIRMED.value
                    notarization.confirmed_at = canonical_event.confirmed_at or now
                    notarization.updated_at = now
                    session.add(
                        NotarizationHistory(
                            notarization_id=notarization.id,
                            from_status=prev_status,
                            to_status=NotarizationStatus.CONFIRMED.value,
                            reason=f"Reconciled: canonical event confirmed at depth {depth}/{required_confs}",
                            metadata_json={"block_number": canonical_event.block_number, "depth": depth},
                            created_at=now,
                        )
                    )
                    NotarizationMetrics.record_confirmed(notarization.chain_id)
            elif depth > 1:
                if notarization.status != NotarizationStatus.CONFIRMING.value and notarization.status != NotarizationStatus.CONFIRMED.value:
                    prev_status = notarization.status
                    notarization.status = NotarizationStatus.CONFIRMING.value
                    notarization.updated_at = now
                    session.add(
                        NotarizationHistory(
                            notarization_id=notarization.id,
                            from_status=prev_status,
                            to_status=NotarizationStatus.CONFIRMING.value,
                            reason=f"Reconciled: event confirming at depth {depth}/{required_confs}",
                            metadata_json={"block_number": canonical_event.block_number, "depth": depth},
                            created_at=now,
                        )
                    )
            else:
                if notarization.status == NotarizationStatus.PENDING.value:
                    notarization.status = NotarizationStatus.SUBMITTED.value
                    notarization.updated_at = now

            await session.flush()
            return notarization

        # 5. If no canonical event found yet, check transaction submission status
        if notarization.transaction_hash and notarization.status == NotarizationStatus.PENDING.value:
            tx_stmt = sa.select(BlockchainTransaction).where(
                BlockchainTransaction.chain_id == notarization.chain_id,
                BlockchainTransaction.current_tx_hash == notarization.transaction_hash,
            )
            tx = (await session.execute(tx_stmt)).scalar_one_or_none()
            if tx and tx.status in ("SUBMITTED", "PENDING"):
                notarization.status = NotarizationStatus.SUBMITTED.value
                notarization.updated_at = now
                await session.flush()

        return notarization

    @staticmethod
    async def reconcile_chain_notarizations(
        session: AsyncSession,
        chain_id: int,
        limit: int = 50,
    ) -> list[ResultNotarization]:
        """Batch-reconciles pending, submitted, or confirming notarizations for a specific chain."""
        stmt = (
            sa.select(ResultNotarization)
            .where(
                ResultNotarization.chain_id == chain_id,
                ResultNotarization.status.in_([
                    NotarizationStatus.PENDING.value,
                    NotarizationStatus.SUBMITTED.value,
                    NotarizationStatus.CONFIRMING.value,
                    NotarizationStatus.REORGED.value,
                ]),
            )
            .order_by(ResultNotarization.created_at.asc())
            .limit(limit)
        )
        notarizations = (await session.execute(stmt)).scalars().all()
        reconciled = []
        for n in notarizations:
            rec = await NotarizationReconciliationService.reconcile_notarization(session, n.id)
            reconciled.append(rec)
        return reconciled
