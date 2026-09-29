"""Derived on-chain ResultNotarization state projector.

Translates canonical BlockchainEvents (ResultNotarized) into queryable ResultNotarization records:
- ResultNotarized (SEEN) -> PENDING / SUBMITTED
- ResultNotarized (CONFIRMING) -> CONFIRMING
- ResultNotarized (CONFIRMED) -> CONFIRMED
- Orphaned event -> REORGED

Note: The blockchain remains authoritative for cryptographic proof;
this projection maintains verified state for fast query and audit trail.
"""

import logging
from typing import Any
import uuid
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.base import utc_now
from app.models.blockchain import BlockchainEvent, EventStatus
from app.models.notarization import (
    NotarizationHistory,
    NotarizationStatus,
    ResultNotarization,
)
from app.services.notarization.utils import (
    bytes32_to_execution_id,
    bytes32_to_result_hash,
)

logger = logging.getLogger(__name__)


class NotarizationProjector:
    """Projects canonical ResultNotarized blockchain events onto ResultNotarization records."""

    def __init__(self, chain_id: int) -> None:
        self.chain_id = chain_id

    async def apply_event(
        self,
        session: AsyncSession,
        event: BlockchainEvent,
    ) -> ResultNotarization | None:
        """Applies a single canonical blockchain event to the derived notarization state."""
        if not event.is_canonical or event.event_name != "ResultNotarized":
            return None

        decoded = event.decoded_data or {}
        exec_id_hex = decoded.get("executionId")
        if not exec_id_hex:
            return None

        try:
            exec_uuid = bytes32_to_execution_id(exec_id_hex)
        except Exception as exc:
            logger.warning("Failed to decode execution ID from event: %s", exc)
            return None

        stmt = sa.select(ResultNotarization).where(
            ResultNotarization.chain_id == self.chain_id,
            ResultNotarization.execution_id == exec_uuid,
        )
        notarization = (await session.execute(stmt)).scalar_one_or_none()
        if not notarization:
            logger.info("Notarization record not found for exec %s on chain %d. Skipping projection.", exec_uuid, self.chain_id)
            return None

        old_status = notarization.status
        new_status = (
            NotarizationStatus.CONFIRMED.value
            if event.status == EventStatus.CONFIRMED.value
            else (
                NotarizationStatus.CONFIRMING.value
                if event.status == EventStatus.CONFIRMING.value
                else NotarizationStatus.SUBMITTED.value
            )
        )

        notarization.transaction_hash = event.transaction_hash
        notarization.block_number = event.block_number
        notarization.block_hash = event.block_hash
        notarization.confirmations = max(notarization.confirmations, 1)
        notarization.is_canonical = True
        notarization.status = new_status
        notarization.updated_at = utc_now()

        if new_status == NotarizationStatus.CONFIRMED.value and not notarization.confirmed_at:
            notarization.confirmed_at = utc_now()

        if old_status != new_status:
            history = NotarizationHistory(
                notarization_id=notarization.id,
                from_status=old_status,
                to_status=new_status,
                reason=f"Projected from canonical event {event.event_name} (depth={notarization.confirmations})",
                metadata_json={
                    "block_number": event.block_number,
                    "transaction_hash": event.transaction_hash,
                },
                created_at=utc_now(),
            )
            session.add(history)

        await session.flush()
        return notarization

    async def sync_confirmation_status(
        self,
        session: AsyncSession,
        event: BlockchainEvent,
    ) -> None:
        """Synchronizes confirmation status when depth changes."""
        if event.event_name != "ResultNotarized":
            return

        decoded = event.decoded_data or {}
        exec_id_hex = decoded.get("executionId")
        if not exec_id_hex:
            return

        try:
            exec_uuid = bytes32_to_execution_id(exec_id_hex)
        except Exception:
            return

        stmt = sa.select(ResultNotarization).where(
            ResultNotarization.chain_id == self.chain_id,
            ResultNotarization.execution_id == exec_uuid,
        )
        notarization = (await session.execute(stmt)).scalar_one_or_none()
        if not notarization:
            return

        old_status = notarization.status
        if event.status == EventStatus.CONFIRMED:
            notarization.status = NotarizationStatus.CONFIRMED.value
            notarization.confirmations = max(notarization.confirmations, 1)
            if not notarization.confirmed_at:
                notarization.confirmed_at = utc_now()
            notarization.updated_at = utc_now()
        elif event.status == EventStatus.CONFIRMING:
            if notarization.status != NotarizationStatus.CONFIRMED.value:
                notarization.status = NotarizationStatus.CONFIRMING.value
                notarization.updated_at = utc_now()
        elif event.status == EventStatus.ORPHANED or not event.is_canonical:
            notarization.is_canonical = False
            notarization.status = NotarizationStatus.REORGED.value
            notarization.reorged_at = utc_now()
            notarization.updated_at = utc_now()

        if old_status != notarization.status:
            history = NotarizationHistory(
                notarization_id=notarization.id,
                from_status=old_status,
                to_status=notarization.status,
                reason=f"Event confirmation status updated to {event.status}",
                metadata_json={
                    "block_number": event.block_number,
                    "event_status": str(event.status),
                },
                created_at=utc_now(),
            )
            session.add(history)
            await session.flush()

    async def reproject_notarization(
        self,
        session: AsyncSession,
        exec_id_hex: str,
    ) -> None:
        """Recalculates notarization projection after a reorg."""
        try:
            exec_uuid = bytes32_to_execution_id(exec_id_hex)
        except Exception:
            return

        stmt = sa.select(ResultNotarization).where(
            ResultNotarization.chain_id == self.chain_id,
            ResultNotarization.execution_id == exec_uuid,
        )
        notarization = (await session.execute(stmt)).scalar_one_or_none()
        if not notarization:
            return

        # Query all events for this execution
        events_stmt = (
            sa.select(BlockchainEvent)
            .where(
                BlockchainEvent.chain_id == self.chain_id,
                BlockchainEvent.event_name == "ResultNotarized",
            )
            .order_by(BlockchainEvent.block_number.asc(), BlockchainEvent.log_index.asc())
        )
        all_events = (await session.execute(events_stmt)).scalars().all()
        matching_canonical = [
            e for e in all_events
            if e.is_canonical
            and e.decoded_data
            and e.decoded_data.get("executionId", "").lower() == exec_id_hex.lower()
        ]

        old_status = notarization.status
        if not matching_canonical:
            # All events for this execution were orphaned
            notarization.is_canonical = False
            notarization.status = NotarizationStatus.REORGED.value
            notarization.reorged_at = utc_now()
            notarization.updated_at = utc_now()
        else:
            latest = matching_canonical[-1]
            notarization.is_canonical = True
            notarization.transaction_hash = latest.transaction_hash
            notarization.block_number = latest.block_number
            notarization.block_hash = latest.block_hash
            notarization.status = (
                NotarizationStatus.CONFIRMED.value
                if latest.status == EventStatus.CONFIRMED
                else (
                    NotarizationStatus.CONFIRMING.value
                    if latest.status == EventStatus.CONFIRMING
                    else NotarizationStatus.SUBMITTED.value
                )
            )
            notarization.updated_at = utc_now()

        if old_status != notarization.status:
            history = NotarizationHistory(
                notarization_id=notarization.id,
                from_status=old_status,
                to_status=notarization.status,
                reason="Reprojected after blockchain reorganization",
                metadata_json={"is_canonical": notarization.is_canonical},
                created_at=utc_now(),
            )
            session.add(history)

        await session.flush()
