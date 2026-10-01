"""Reputation Scoring Service — Phase 6.5.

This service is the sole consumer of ReputationPolicyV1.
It reads verified canonical evidence from Phase 6.4 and calculates scores.

Architectural constraints:
- NEVER creates reputation events (that is Phase 6.4's responsibility).
- NEVER modifies historical score records.
- NEVER accepts score values from external callers.
- Always derives scores from canonical confirmed reputation events only.
- All calculations are deterministic and reproducible given the same evidence.
- Admin recalculation triggers fresh computation; never accepts a preset score.
"""

import logging
import time
import uuid
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit import AuditEventType, AuditLog
from app.models.base import utc_now
from app.models.reputation import (
    ReputationEvent,
    ReputationOutcomeType,
    ReputationStatus,
)
from app.models.reputation_scoring import (
    SCORE_MAX_SCALED as SCORE_MAX,
    SCORE_MIN_SCALED as SCORE_MIN,
    SCORE_SCALE,
    ReputationPolicyVersion,
    ReputationScore,
    ReputationScoreHistory,
)
from app.models.user import User
from app.services.blockchain.config import CHAIN_ID_ANVIL
from app.services.reputation_policy.policy_v1 import (
    EvidenceEvent,
    ReputationPolicyV1,
    get_policy_v1,
)
from app.services.reputation_policy.metrics import ScoringMetrics

logger = logging.getLogger(__name__)


class CalculationReason:
    INITIAL_CALCULATION = "INITIAL_CALCULATION"
    NEW_VERIFIED_EVENT = "NEW_VERIFIED_EVENT"
    REORG_RECALCULATION = "REORG_RECALCULATION"
    POLICY_VERSION_CHANGE = "POLICY_VERSION_CHANGE"
    MANUAL_RECALCULATION = "MANUAL_RECALCULATION"


async def _get_or_create_policy_version(
    session: AsyncSession,
    policy: ReputationPolicyV1,
) -> ReputationPolicyVersion:
    """Retrieve or create the ReputationPolicyVersion row for V1.

    This is idempotent: if the row already exists, it is returned.
    If it does not exist, it is created with the exact formula spec.
    """
    stmt = sa.select(ReputationPolicyVersion).where(
        ReputationPolicyVersion.policy_name == policy.POLICY_NAME,
        ReputationPolicyVersion.version_number == policy.POLICY_VERSION,
    )
    pv = (await session.execute(stmt)).scalar_one_or_none()
    if pv is None:
        pv = ReputationPolicyVersion(
            policy_name=policy.POLICY_NAME,
            version_number=policy.POLICY_VERSION,
            description=(
                "Deterministic reputation score derived exclusively from canonical verified "
                "execution evidence.  Score = round(verified_successes / "
                "(verified_successes + verified_failures) * 10000).  "
                "VERIFIED_TIMEOUT and VERIFIED_CANCELLATION are neutral (not penalized)."
            ),
            formula_json=policy.get_formula_spec(),
            is_active=True,
        )
        session.add(pv)
        await session.flush()
        logger.info(
            "Created reputation policy version: %s V%d",
            policy.POLICY_NAME,
            policy.POLICY_VERSION,
        )
    return pv


async def _fetch_canonical_events(
    session: AsyncSession,
    agent_id: uuid.UUID,
    chain_id: int,
) -> list[ReputationEvent]:
    """Fetch all CONFIRMED, CANONICAL reputation events for the agent.

    These are the authoritative inputs to the scoring policy.
    No filtering by outcome_type — all outcomes are included and the
    policy decides how to weight them.

    Events are ordered by (confirmed_at, created_at) for determinism.
    """
    stmt = (
        sa.select(ReputationEvent)
        .where(
            ReputationEvent.agent_id == agent_id,
            ReputationEvent.chain_id == chain_id,
            ReputationEvent.is_canonical.is_(True),
            ReputationEvent.status == ReputationStatus.CONFIRMED.value,
        )
        .order_by(
            ReputationEvent.confirmed_at.asc().nullsfirst(),
            ReputationEvent.created_at.asc(),
        )
    )
    return list((await session.execute(stmt)).scalars().all())


def _db_events_to_evidence(
    db_events: list[ReputationEvent],
) -> list[EvidenceEvent]:
    """Convert DB ReputationEvent records to policy-layer EvidenceEvent objects."""
    result = []
    for ev in db_events:
        confirmed_str = (
            ev.confirmed_at.isoformat()
            if ev.confirmed_at
            else ev.created_at.isoformat()
        )
        result.append(
            EvidenceEvent(
                event_id=str(ev.id).lower(),
                outcome_type=ev.outcome_type,
                confirmed_at=confirmed_str,
            )
        )
    return result


class ReputationScoringService:
    """Service for calculating and persisting deterministic reputation scores.

    All score calculations are derived from Phase 6.4 canonical evidence.
    This service NEVER creates reputation events.
    This service NEVER accepts externally-provided scores.
    All admin-triggered recalculations are still fully deterministic.
    """

    @staticmethod
    async def calculate_and_persist_score(
        session: AsyncSession,
        agent_id: uuid.UUID,
        chain_id: int | None = None,
        reason: str = CalculationReason.INITIAL_CALCULATION,
        actor_user: User | None = None,
        client_ip: str | None = None,
    ) -> ReputationScore:
        """Calculate and persist a deterministic reputation score.

        This is the primary entry point.  It:
        1. Fetches canonical confirmed reputation events (Phase 6.4 evidence).
        2. Computes evidence_set_hash for deterministic identification.
        3. Applies ReputationPolicyV1 to compute score_scaled.
        4. Persists or updates the ReputationScore record.
        5. Appends an immutable ReputationScoreHistory row.
        6. Records an audit log entry.
        7. Records Prometheus metrics.

        Args:
            session: AsyncSession (caller manages transaction).
            agent_id: UUID of the agent to score.
            chain_id: Blockchain chain ID. Defaults to CHAIN_ID_ANVIL.
            reason: Why this calculation was triggered. Must be one of CalculationReason.*.
            actor_user: The user who triggered this calculation (for audit).
            client_ip: Caller IP for audit log.

        Returns:
            ReputationScore — the persisted (possibly updated) score record.

        Raises:
            ValueError: If reason is not a recognized CalculationReason.
        """
        target_chain_id = chain_id if chain_id is not None else CHAIN_ID_ANVIL
        policy = get_policy_v1()
        t_start = time.monotonic()

        valid_reasons = {
            CalculationReason.INITIAL_CALCULATION,
            CalculationReason.NEW_VERIFIED_EVENT,
            CalculationReason.REORG_RECALCULATION,
            CalculationReason.POLICY_VERSION_CHANGE,
            CalculationReason.MANUAL_RECALCULATION,
        }
        if reason not in valid_reasons:
            raise ValueError(f"Invalid calculation reason: {reason!r}")

        # 1. Ensure policy version row exists
        policy_version_record = await _get_or_create_policy_version(session, policy)

        # 2. Fetch canonical evidence from Phase 6.4
        db_events = await _fetch_canonical_events(session, agent_id, target_chain_id)
        evidence_events = _db_events_to_evidence(db_events)

        # 3. Apply policy
        score_scaled, explanation = policy.calculate_score(evidence_events)
        evidence_set_hash = explanation.evidence_set_hash

        # 4. Short-circuit: if same evidence set already produced the same score,
        #    skip unnecessary DB writes (idempotency)
        existing_stmt = (
            sa.select(ReputationScore)
            .where(
                ReputationScore.agent_id == agent_id,
                ReputationScore.chain_id == target_chain_id,
                ReputationScore.policy_version == policy.POLICY_VERSION_STRING,
            )
            .with_for_update()
        )
        existing_score = (await session.execute(existing_stmt)).scalar_one_or_none()

        if (
            existing_score is not None
            and existing_score.evidence_set_hash == evidence_set_hash
            and existing_score.score_scaled == score_scaled
            and reason != CalculationReason.MANUAL_RECALCULATION
        ):
            logger.debug(
                "Score for agent %s unchanged (evidence_set_hash=%s score=%d). Skipping write.",
                agent_id,
                evidence_set_hash,
                score_scaled,
            )
            ScoringMetrics.record_calculation(reason_code="idempotent_skip")
            return existing_score

        # 5. Prepare metric snapshot
        expl = explanation
        expl_dict = expl.to_dict()
        previous_score_scaled = existing_score.score_scaled if existing_score else None
        previous_evidence_set_hash = existing_score.evidence_set_hash if existing_score else None

        if existing_score is None:
            # Create new score record
            score_record = ReputationScore(
                agent_id=agent_id,
                chain_id=target_chain_id,
                policy_version_id=policy_version_record.id,
                policy_version=policy.POLICY_VERSION_STRING,
                score_scaled=score_scaled,
                score_min=SCORE_MIN,
                score_max=SCORE_MAX,
                total_verified_executions=expl.total_verified_executions,
                verified_successes=expl.verified_successes,
                verified_failures=expl.verified_failures,
                verified_timeouts=expl.verified_timeouts,
                verified_cancellations=expl.verified_cancellations,
                success_rate_scaled=expl.success_rate_scaled,
                failure_rate_scaled=expl.failure_rate_scaled,
                timeout_rate_scaled=expl.timeout_rate_scaled,
                cancellation_rate_scaled=expl.cancellation_rate_scaled,
                experience_count=expl.experience_count,
                evidence_set_hash=evidence_set_hash,
                evidence_event_count=expl.evidence_event_count,
                calculation_reason=reason,
                explanation_json=expl_dict,
                calculated_at=utc_now(),
            )
            session.add(score_record)
            await session.flush()
        else:
            # Update existing score record in-place
            existing_score.score_scaled = score_scaled
            existing_score.total_verified_executions = expl.total_verified_executions
            existing_score.verified_successes = expl.verified_successes
            existing_score.verified_failures = expl.verified_failures
            existing_score.verified_timeouts = expl.verified_timeouts
            existing_score.verified_cancellations = expl.verified_cancellations
            existing_score.success_rate_scaled = expl.success_rate_scaled
            existing_score.failure_rate_scaled = expl.failure_rate_scaled
            existing_score.timeout_rate_scaled = expl.timeout_rate_scaled
            existing_score.cancellation_rate_scaled = expl.cancellation_rate_scaled
            existing_score.experience_count = expl.experience_count
            existing_score.evidence_set_hash = evidence_set_hash
            existing_score.evidence_event_count = expl.evidence_event_count
            existing_score.calculation_reason = reason
            existing_score.explanation_json = expl_dict
            existing_score.calculated_at = utc_now()
            existing_score.updated_at = utc_now()
            await session.flush()
            score_record = existing_score

        # 6. Append immutable history row
        history = ReputationScoreHistory(
            reputation_score_id=score_record.id,
            agent_id=agent_id,
            chain_id=target_chain_id,
            policy_version=policy.POLICY_VERSION_STRING,
            previous_score_scaled=previous_score_scaled,
            previous_evidence_set_hash=previous_evidence_set_hash,
            new_score_scaled=score_scaled,
            new_evidence_set_hash=evidence_set_hash,
            total_verified_executions=expl.total_verified_executions,
            verified_successes=expl.verified_successes,
            verified_failures=expl.verified_failures,
            verified_timeouts=expl.verified_timeouts,
            verified_cancellations=expl.verified_cancellations,
            calculation_reason=reason,
            explanation_json=expl_dict,
            calculated_at=utc_now(),
        )
        session.add(history)

        # 7. Audit log
        audit = AuditLog(
            user_id=actor_user.id if actor_user else None,
            event_type=AuditEventType.REPUTATION_SCORE_CALCULATED.value,
            ip_address=client_ip,
            metadata_json={
                "action": "reputation.score_calculated",
                "agent_id": str(agent_id),
                "chain_id": target_chain_id,
                "policy_version": policy.POLICY_VERSION_STRING,
                "evidence_set_hash": evidence_set_hash,
                "evidence_event_count": expl.evidence_event_count,
                "score_scaled": score_scaled,
                "previous_score_scaled": previous_score_scaled,
                "reason": reason,
            },
        )
        session.add(audit)
        await session.flush()

        # 8. Metrics
        duration = time.monotonic() - t_start
        ScoringMetrics.record_calculation(reason_code=reason.lower())
        ScoringMetrics.record_duration(duration)

        logger.info(
            "Scored agent %s chain %d policy %s: score=%d evidence_count=%d reason=%s "
            "evidence_hash=%s",
            agent_id,
            target_chain_id,
            policy.POLICY_VERSION_STRING,
            score_scaled,
            expl.evidence_event_count,
            reason,
            evidence_set_hash,
        )
        return score_record

    @staticmethod
    async def get_current_score(
        session: AsyncSession,
        agent_id: uuid.UUID,
        chain_id: int | None = None,
        policy_version: str | None = None,
    ) -> ReputationScore | None:
        """Retrieve the current active score for an agent.

        Returns None if no score has been calculated yet.

        Args:
            policy_version: e.g. "ReputationPolicyV1". Defaults to V1.
        """
        target_chain_id = chain_id if chain_id is not None else CHAIN_ID_ANVIL
        target_policy = policy_version or get_policy_v1().POLICY_VERSION_STRING
        stmt = sa.select(ReputationScore).where(
            ReputationScore.agent_id == agent_id,
            ReputationScore.chain_id == target_chain_id,
            ReputationScore.policy_version == target_policy,
        )
        return (await session.execute(stmt)).scalar_one_or_none()

    @staticmethod
    async def get_score_history(
        session: AsyncSession,
        agent_id: uuid.UUID,
        chain_id: int | None = None,
        policy_version: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[ReputationScoreHistory]:
        """Retrieve immutable score history for an agent.

        Returns history in reverse chronological order (most recent first).
        """
        target_chain_id = chain_id if chain_id is not None else CHAIN_ID_ANVIL
        conditions = [
            ReputationScoreHistory.agent_id == agent_id,
            ReputationScoreHistory.chain_id == target_chain_id,
        ]
        if policy_version:
            conditions.append(ReputationScoreHistory.policy_version == policy_version)

        stmt = (
            sa.select(ReputationScoreHistory)
            .where(*conditions)
            .order_by(ReputationScoreHistory.calculated_at.desc())
            .offset(offset)
            .limit(limit)
        )
        return list((await session.execute(stmt)).scalars().all())

    @staticmethod
    async def get_policy_version(
        session: AsyncSession,
        policy_name: str | None = None,
        version_number: int | None = None,
    ) -> ReputationPolicyVersion | None:
        """Retrieve a policy version record by name and/or number."""
        policy = get_policy_v1()
        pname = policy_name or policy.POLICY_NAME
        pnum = version_number if version_number is not None else policy.POLICY_VERSION
        stmt = sa.select(ReputationPolicyVersion).where(
            ReputationPolicyVersion.policy_name == pname,
            ReputationPolicyVersion.version_number == pnum,
        )
        return (await session.execute(stmt)).scalar_one_or_none()

    @staticmethod
    async def trigger_admin_recalculation(
        session: AsyncSession,
        agent_id: uuid.UUID,
        chain_id: int | None = None,
        actor_user: User | None = None,
        client_ip: str | None = None,
    ) -> ReputationScore:
        """Admin-triggered deterministic recalculation.

        Admin CANNOT set a score directly.
        This method always recomputes from authoritative evidence.
        Rate-limiting and authorization must be enforced by the caller (API layer).
        """
        logger.info(
            "Admin recalculation triggered for agent %s chain %s by user %s",
            agent_id,
            chain_id,
            actor_user.id if actor_user else "unknown",
        )
        ScoringMetrics.record_recalculation()
        return await ReputationScoringService.calculate_and_persist_score(
            session=session,
            agent_id=agent_id,
            chain_id=chain_id,
            reason=CalculationReason.MANUAL_RECALCULATION,
            actor_user=actor_user,
            client_ip=client_ip,
        )

    @staticmethod
    async def handle_reorg_recalculation(
        session: AsyncSession,
        agent_id: uuid.UUID,
        chain_id: int | None = None,
    ) -> ReputationScore:
        """Triggered by reorg handler when evidence changes due to reorganization.

        Preserves previous score history; calculates fresh score from
        post-reorg canonical evidence.
        """
        logger.info(
            "Reorg recalculation triggered for agent %s chain %s",
            agent_id,
            chain_id,
        )
        ScoringMetrics.record_reorg_recalculation()
        return await ReputationScoringService.calculate_and_persist_score(
            session=session,
            agent_id=agent_id,
            chain_id=chain_id,
            reason=CalculationReason.REORG_RECALCULATION,
        )
