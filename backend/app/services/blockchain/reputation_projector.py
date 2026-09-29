"""Derived on-chain Reputation state projector and profile aggregator.

Translates canonical BlockchainEvents (ReputationEventRegistered) into queryable ReputationEvent records:
- ReputationEventRegistered (SEEN) -> PENDING / SUBMITTED
- ReputationEventRegistered (CONFIRMING) -> CONFIRMING
- ReputationEventRegistered (CONFIRMED) -> CONFIRMED
- Orphaned event -> REORGED

Maintains reorg-safe ReputationProfile aggregations derived exclusively from canonical confirmed events:
- total_verified_executions
- verified_successes
- verified_failures
- verified_timeouts
- verified_cancellations
- first / latest verified execution
- success_rate = verified_successes / total_verified_executions
"""

import logging
from decimal import Decimal
from typing import Any
import uuid
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.base import utc_now
from app.models.blockchain import BlockchainEvent, EventStatus
from app.models.reputation import (
    ReputationEvent,
    ReputationHistory,
    ReputationOutcomeType,
    ReputationProfile,
    ReputationStatus,
)
from app.services.reputation.metrics import ReputationMetrics
from app.services.reputation.utils import bytes32_to_uuid

logger = logging.getLogger(__name__)

# Map uint8 enum on-chain to string outcome type
OUTCOME_TYPE_MAP: dict[int, str] = {
    1: ReputationOutcomeType.VERIFIED_SUCCESS.value,
    2: ReputationOutcomeType.VERIFIED_FAILURE.value,
    3: ReputationOutcomeType.VERIFIED_TIMEOUT.value,
    4: ReputationOutcomeType.VERIFIED_CANCELLATION.value,
}


class ReputationProjector:
    """Projects canonical ReputationEventRegistered blockchain events onto ReputationEvent records
    and maintains derived ReputationProfile aggregates."""

    def __init__(self, chain_id: int) -> None:
        self.chain_id = chain_id

    async def apply_event(
        self,
        session: AsyncSession,
        event: BlockchainEvent,
    ) -> ReputationEvent | None:
        """Applies a single canonical blockchain event to the derived reputation event state."""
        if not event.is_canonical or event.event_name != "ReputationEventRegistered":
            return None

        decoded = event.decoded_data or {}
        exec_id_hex = decoded.get("executionId")
        if not exec_id_hex:
            return None

        try:
            exec_uuid = bytes32_to_uuid(exec_id_hex)
        except Exception as exc:
            logger.warning("Failed to decode execution ID from reputation event: %s", exc)
            return None

        stmt = sa.select(ReputationEvent).where(
            ReputationEvent.chain_id == self.chain_id,
            ReputationEvent.execution_id == exec_uuid,
        )
        rep_event = (await session.execute(stmt)).scalar_one_or_none()
        if not rep_event:
            logger.info(
                "Reputation event record not found for exec %s on chain %d. Skipping projection.",
                exec_uuid,
                self.chain_id,
            )
            return None

        old_status = rep_event.status
        new_status = (
            ReputationStatus.CONFIRMED.value
            if event.status == EventStatus.CONFIRMED.value
            else (
                ReputationStatus.CONFIRMING.value
                if event.status == EventStatus.CONFIRMING.value
                else ReputationStatus.SUBMITTED.value
            )
        )

        rep_event.transaction_hash = event.transaction_hash
        rep_event.block_number = event.block_number
        rep_event.block_hash = event.block_hash
        rep_event.confirmations = max(rep_event.confirmations, 1)
        rep_event.is_canonical = True
        rep_event.status = new_status
        rep_event.updated_at = utc_now()

        if new_status == ReputationStatus.CONFIRMED.value and not rep_event.confirmed_at:
            rep_event.confirmed_at = utc_now()
            ReputationMetrics.record_confirmed(self.chain_id, rep_event.outcome_type)

        if old_status != new_status:
            history = ReputationHistory(
                reputation_event_id=rep_event.id,
                from_status=old_status,
                to_status=new_status,
                reason=f"Projected from canonical event {event.event_name} (depth={rep_event.confirmations})",
                metadata_json={
                    "block_number": event.block_number,
                    "transaction_hash": event.transaction_hash,
                },
                created_at=utc_now(),
            )
            session.add(history)

        await session.flush()

        # Update profile for this agent
        await self.recalculate_profile(session, rep_event.agent_id)
        return rep_event

    async def sync_confirmation_status(
        self,
        session: AsyncSession,
        event: BlockchainEvent,
    ) -> None:
        """Synchronizes confirmation status when depth changes."""
        if event.event_name != "ReputationEventRegistered":
            return

        decoded = event.decoded_data or {}
        exec_id_hex = decoded.get("executionId")
        if not exec_id_hex:
            return

        try:
            exec_uuid = bytes32_to_uuid(exec_id_hex)
        except Exception:
            return

        stmt = sa.select(ReputationEvent).where(
            ReputationEvent.chain_id == self.chain_id,
            ReputationEvent.execution_id == exec_uuid,
        )
        rep_event = (await session.execute(stmt)).scalar_one_or_none()
        if not rep_event:
            return

        old_status = rep_event.status
        if event.status == EventStatus.CONFIRMED:
            rep_event.status = ReputationStatus.CONFIRMED.value
            rep_event.confirmations = max(rep_event.confirmations, 1)
            if not rep_event.confirmed_at:
                rep_event.confirmed_at = utc_now()
                ReputationMetrics.record_confirmed(self.chain_id, rep_event.outcome_type)
            rep_event.updated_at = utc_now()
        elif event.status == EventStatus.CONFIRMING:
            if rep_event.status != ReputationStatus.CONFIRMED.value:
                rep_event.status = ReputationStatus.CONFIRMING.value
                rep_event.updated_at = utc_now()
        elif event.status == EventStatus.ORPHANED or not event.is_canonical:
            rep_event.is_canonical = False
            rep_event.status = ReputationStatus.REORGED.value
            rep_event.reorged_at = utc_now()
            rep_event.updated_at = utc_now()
            ReputationMetrics.record_reorged(self.chain_id)

        if old_status != rep_event.status:
            history = ReputationHistory(
                reputation_event_id=rep_event.id,
                from_status=old_status,
                to_status=rep_event.status,
                reason=f"Event confirmation status updated to {event.status}",
                metadata_json={
                    "block_number": event.block_number,
                    "event_status": str(event.status),
                },
                created_at=utc_now(),
            )
            session.add(history)
            await session.flush()

        await self.recalculate_profile(session, rep_event.agent_id)

    async def reproject_reputation(
        self,
        session: AsyncSession,
        exec_id_hex: str,
    ) -> None:
        """Recalculates reputation event projection and profile after a reorg."""
        try:
            exec_uuid = bytes32_to_uuid(exec_id_hex)
        except Exception:
            return

        stmt = sa.select(ReputationEvent).where(
            ReputationEvent.chain_id == self.chain_id,
            ReputationEvent.execution_id == exec_uuid,
        )
        rep_event = (await session.execute(stmt)).scalar_one_or_none()
        if not rep_event:
            return

        # Query all events for this execution
        events_stmt = (
            sa.select(BlockchainEvent)
            .where(
                BlockchainEvent.chain_id == self.chain_id,
                BlockchainEvent.event_name == "ReputationEventRegistered",
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

        old_status = rep_event.status
        if not matching_canonical:
            # All events for this execution were orphaned
            rep_event.is_canonical = False
            rep_event.status = ReputationStatus.REORGED.value
            rep_event.reorged_at = utc_now()
            rep_event.updated_at = utc_now()
            ReputationMetrics.record_reorged(self.chain_id)
        else:
            latest = matching_canonical[-1]
            rep_event.is_canonical = True
            rep_event.transaction_hash = latest.transaction_hash
            rep_event.block_number = latest.block_number
            rep_event.block_hash = latest.block_hash
            rep_event.status = (
                ReputationStatus.CONFIRMED.value
                if latest.status == EventStatus.CONFIRMED
                else (
                    ReputationStatus.CONFIRMING.value
                    if latest.status == EventStatus.CONFIRMING
                    else ReputationStatus.SUBMITTED.value
                )
            )
            rep_event.updated_at = utc_now()

        if old_status != rep_event.status:
            history = ReputationHistory(
                reputation_event_id=rep_event.id,
                from_status=old_status,
                to_status=rep_event.status,
                reason="Reprojected after blockchain reorganization",
                metadata_json={"is_canonical": rep_event.is_canonical},
                created_at=utc_now(),
            )
            session.add(history)

        await session.flush()
        await self.recalculate_profile(session, rep_event.agent_id)

    async def recalculate_profile(
        self,
        session: AsyncSession,
        agent_id: uuid.UUID,
    ) -> ReputationProfile:
        """Derives the authoritative reputation profile strictly from CANONICAL CONFIRMED events."""
        # Query all canonical confirmed reputation events for this agent on this chain
        stmt = (
            sa.select(ReputationEvent)
            .where(
                ReputationEvent.chain_id == self.chain_id,
                ReputationEvent.agent_id == agent_id,
                ReputationEvent.is_canonical.is_(True),
                ReputationEvent.status == ReputationStatus.CONFIRMED.value,
            )
            .order_by(ReputationEvent.confirmed_at.asc(), ReputationEvent.created_at.asc())
        )
        events = (await session.execute(stmt)).scalars().all()

        total = len(events)
        successes = 0
        failures = 0
        timeouts = 0
        cancellations = 0

        first_exec_id: uuid.UUID | None = None
        latest_exec_id: uuid.UUID | None = None
        latest_outcome: str | None = None
        latest_at = None

        if events:
            first_exec_id = events[0].execution_id
            latest_event = events[-1]
            latest_exec_id = latest_event.execution_id
            latest_outcome = latest_event.outcome_type
            latest_at = latest_event.confirmed_at or latest_event.created_at

            for ev in events:
                if ev.outcome_type == ReputationOutcomeType.VERIFIED_SUCCESS.value:
                    successes += 1
                elif ev.outcome_type == ReputationOutcomeType.VERIFIED_FAILURE.value:
                    failures += 1
                elif ev.outcome_type == ReputationOutcomeType.VERIFIED_TIMEOUT.value:
                    timeouts += 1
                elif ev.outcome_type == ReputationOutcomeType.VERIFIED_CANCELLATION.value:
                    cancellations += 1

        success_rate: Decimal | None = None
        if total > 0:
            success_rate = (Decimal(successes) / Decimal(total)).quantize(Decimal("0.0001"))

        # Lock / Upsert ReputationProfile
        profile_stmt = (
            sa.select(ReputationProfile)
            .where(
                ReputationProfile.chain_id == self.chain_id,
                ReputationProfile.agent_id == agent_id,
            )
            .with_for_update()
        )
        profile = (await session.execute(profile_stmt)).scalar_one_or_none()

        if not profile:
            profile = ReputationProfile(
                agent_id=agent_id,
                chain_id=self.chain_id,
                total_verified_executions=total,
                verified_successes=successes,
                verified_failures=failures,
                verified_timeouts=timeouts,
                verified_cancellations=cancellations,
                canonical_event_count=total,
                first_verified_execution_id=first_exec_id,
                latest_verified_execution_id=latest_exec_id,
                latest_verified_outcome=latest_outcome,
                latest_verified_at=latest_at,
                success_rate=success_rate,
                last_recalculated_at=utc_now(),
            )
            session.add(profile)
        else:
            profile.total_verified_executions = total
            profile.verified_successes = successes
            profile.verified_failures = failures
            profile.verified_timeouts = timeouts
            profile.verified_cancellations = cancellations
            profile.canonical_event_count = total
            profile.first_verified_execution_id = first_exec_id
            profile.latest_verified_execution_id = latest_exec_id
            profile.latest_verified_outcome = latest_outcome
            profile.latest_verified_at = latest_at
            profile.success_rate = success_rate
            profile.last_recalculated_at = utc_now()
            profile.updated_at = utc_now()

        await session.flush()

        # Phase 6.5 integration: trigger scoring recalculation when evidence changes
        try:
            from app.services.reputation_policy.scoring_service import (
                CalculationReason,
                ReputationScoringService,
            )
            await ReputationScoringService.calculate_and_persist_score(
                session=session,
                agent_id=agent_id,
                chain_id=self.chain_id,
                reason=CalculationReason.NEW_VERIFIED_EVENT,
            )
        except Exception as scoring_exc:
            # Scoring failure must not block evidence projection
            logger.warning(
                "Phase 6.5 scoring recalculation failed for agent %s (non-fatal): %s",
                agent_id,
                scoring_exc,
            )

        return profile
