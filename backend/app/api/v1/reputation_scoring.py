"""API endpoints for Phase 6.5 — Reputation Scoring & Policy Engine.

All endpoints are read-only with the exception of the admin recalculation endpoint.

Security invariants enforced:
- No client can provide a score value.
- No client can modify historical scores.
- Admin recalculation always computes from authoritative evidence.
- Scoring endpoints expose policy version and evidence hash for auditability.
"""

import logging
from datetime import datetime, timezone
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_db, require_admin_user, require_authenticated_user
from app.models.reputation_scoring import (
    SCORE_SCALE,
    ReputationScore,
    ReputationScoreHistory,
)
from app.models.user import User
from app.schemas.reputation_scoring import (
    AdminRecalculateRequest,
    PolicyVersionResponse,
    ReputationExplanationResponse,
    ReputationMetricsResponse,
    ReputationScoreHistoryItem,
    ReputationScoreResponse,
)
from app.services.blockchain.config import CHAIN_ID_ANVIL
from app.services.reputation_policy.policy_v1 import (
    EvidenceEvent,
    ReputationPolicyV1,
    get_policy_v1,
)
from app.services.reputation_policy.scoring_service import (
    CalculationReason,
    ReputationScoringService,
)

logger = logging.getLogger("agentchain.api.reputation_scoring")

router = APIRouter(tags=["reputation-scoring"])


def _score_to_response(s: ReputationScore) -> ReputationScoreResponse:
    return ReputationScoreResponse(
        id=s.id,
        agent_id=s.agent_id,
        chain_id=s.chain_id,
        policy_version=s.policy_version,
        score_scaled=s.score_scaled,
        score_min=s.score_min,
        score_max=s.score_max,
        score_float=s.score_scaled / SCORE_SCALE,
        total_verified_executions=s.total_verified_executions,
        verified_successes=s.verified_successes,
        verified_failures=s.verified_failures,
        verified_timeouts=s.verified_timeouts,
        verified_cancellations=s.verified_cancellations,
        success_rate_scaled=s.success_rate_scaled,
        failure_rate_scaled=s.failure_rate_scaled,
        timeout_rate_scaled=s.timeout_rate_scaled,
        cancellation_rate_scaled=s.cancellation_rate_scaled,
        experience_count=s.experience_count,
        evidence_set_hash=s.evidence_set_hash,
        evidence_event_count=s.evidence_event_count,
        calculation_reason=s.calculation_reason,
        explanation_json=s.explanation_json or {},
        calculated_at=s.calculated_at,
        created_at=s.created_at,
        updated_at=s.updated_at,
    )


@router.get(
    "/agents/{agent_id}/reputation/score",
    response_model=ReputationScoreResponse,
    summary="Get deterministic reputation score for an agent (calculates if not cached)",
)
async def get_agent_reputation_score(
    agent_id: uuid.UUID,
    chain_id: int = Query(default=CHAIN_ID_ANVIL, description="Blockchain chain ID"),
    policy_version: str = Query(default=None, description="Policy version string, e.g. 'ReputationPolicyV1'. Defaults to latest."),
    session: AsyncSession = Depends(get_db),
) -> ReputationScoreResponse:
    """Returns the current deterministic reputation score.

    If no score exists yet, one is calculated and persisted from the
    canonical evidence set.  The score is fully deterministic:
    the same evidence always produces the same result.

    Score is derived from ReputationPolicyV1:
    'Score derived from verified execution evidence under Reputation Policy V1.'
    """
    target_policy = policy_version or get_policy_v1().POLICY_VERSION_STRING

    # Check for existing score
    existing = await ReputationScoringService.get_current_score(
        session, agent_id, chain_id, target_policy
    )
    if existing is not None:
        return _score_to_response(existing)

    # Calculate fresh (idempotent)
    try:
        score = await ReputationScoringService.calculate_and_persist_score(
            session=session,
            agent_id=agent_id,
            chain_id=chain_id,
            reason=CalculationReason.INITIAL_CALCULATION,
        )
        await session.commit()
        return _score_to_response(score)
    except Exception as exc:
        await session.rollback()
        logger.error("Score calculation failed for agent %s: %s", agent_id, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Score calculation failed: {exc}",
        ) from exc


@router.get(
    "/agents/{agent_id}/reputation/metrics",
    response_model=ReputationMetricsResponse,
    summary="Get objective reputation metrics for an agent",
)
async def get_agent_reputation_metrics(
    agent_id: uuid.UUID,
    chain_id: int = Query(default=CHAIN_ID_ANVIL),
    session: AsyncSession = Depends(get_db),
) -> ReputationMetricsResponse:
    """Returns objective evidence metrics separately from the final score.

    A user must be able to inspect exactly why a score exists.
    Scores must not hide the underlying evidence.
    """
    policy = get_policy_v1()
    existing = await ReputationScoringService.get_current_score(
        session, agent_id, chain_id, policy.POLICY_VERSION_STRING
    )

    if existing is None:
        # Return zero metrics if no score exists
        return ReputationMetricsResponse(
            agent_id=agent_id,
            chain_id=chain_id,
            policy_version=policy.POLICY_VERSION_STRING,
            total_verified_executions=0,
            verified_successes=0,
            verified_failures=0,
            verified_timeouts=0,
            verified_cancellations=0,
            experience_count=0,
            evidence_event_count=0,
        )

    return ReputationMetricsResponse(
        agent_id=agent_id,
        chain_id=chain_id,
        policy_version=existing.policy_version,
        total_verified_executions=existing.total_verified_executions,
        verified_successes=existing.verified_successes,
        verified_failures=existing.verified_failures,
        verified_timeouts=existing.verified_timeouts,
        verified_cancellations=existing.verified_cancellations,
        success_rate_scaled=existing.success_rate_scaled,
        failure_rate_scaled=existing.failure_rate_scaled,
        timeout_rate_scaled=existing.timeout_rate_scaled,
        cancellation_rate_scaled=existing.cancellation_rate_scaled,
        success_rate=(existing.success_rate_scaled / SCORE_SCALE if existing.success_rate_scaled is not None else None),
        failure_rate=(existing.failure_rate_scaled / SCORE_SCALE if existing.failure_rate_scaled is not None else None),
        timeout_rate=(existing.timeout_rate_scaled / SCORE_SCALE if existing.timeout_rate_scaled is not None else None),
        cancellation_rate=(existing.cancellation_rate_scaled / SCORE_SCALE if existing.cancellation_rate_scaled is not None else None),
        reputation_score=existing.score_scaled,
        experience_count=existing.experience_count,
        evidence_set_hash=existing.evidence_set_hash,
        evidence_event_count=existing.evidence_event_count,
        calculated_at=existing.calculated_at,
    )


@router.get(
    "/agents/{agent_id}/reputation/history",
    response_model=list[ReputationScoreHistoryItem],
    summary="Get immutable score calculation history for an agent",
)
async def get_agent_reputation_history(
    agent_id: uuid.UUID,
    chain_id: int = Query(default=CHAIN_ID_ANVIL),
    policy_version: str = Query(default=None),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(get_db),
) -> list[ReputationScoreHistoryItem]:
    """Returns immutable score history in reverse chronological order.

    Every recalculation preserves the previous score and reason.
    Historical records are never mutated.
    """
    history = await ReputationScoringService.get_score_history(
        session, agent_id, chain_id, policy_version, limit, offset
    )
    return [
        ReputationScoreHistoryItem(
            id=h.id,
            reputation_score_id=h.reputation_score_id,
            agent_id=h.agent_id,
            chain_id=h.chain_id,
            policy_version=h.policy_version,
            previous_score_scaled=h.previous_score_scaled,
            previous_evidence_set_hash=h.previous_evidence_set_hash,
            new_score_scaled=h.new_score_scaled,
            new_evidence_set_hash=h.new_evidence_set_hash,
            total_verified_executions=h.total_verified_executions,
            verified_successes=h.verified_successes,
            verified_failures=h.verified_failures,
            verified_timeouts=h.verified_timeouts,
            verified_cancellations=h.verified_cancellations,
            calculation_reason=h.calculation_reason,
            explanation_json=h.explanation_json or {},
            calculated_at=h.calculated_at,
        )
        for h in history
    ]


@router.get(
    "/agents/{agent_id}/reputation/explanation",
    response_model=ReputationExplanationResponse,
    summary="Get full auditable score explanation for an agent",
)
async def get_agent_reputation_explanation(
    agent_id: uuid.UUID,
    chain_id: int = Query(default=CHAIN_ID_ANVIL),
    session: AsyncSession = Depends(get_db),
) -> ReputationExplanationResponse:
    """Returns the full auditable explanation of the score calculation.

    Contains sufficient information for an auditor to independently
    reproduce the score without reading the source code.

    Note: Does not expose internal secrets or execution payloads.
    """
    policy = get_policy_v1()
    existing = await ReputationScoringService.get_current_score(
        session, agent_id, chain_id, policy.POLICY_VERSION_STRING
    )
    if existing is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No reputation score found for agent {agent_id} on chain {chain_id}",
        )

    expl = existing.explanation_json or {}
    return ReputationExplanationResponse(
        agent_id=agent_id,
        chain_id=chain_id,
        policy_version=existing.policy_version,
        policy_formula_summary=(
            "Score = round(verified_successes / (verified_successes + verified_failures) * 10000). "
            "VERIFIED_TIMEOUT and VERIFIED_CANCELLATION are neutral and excluded from ratio. "
            "Cold start (no successes or failures): score_scaled = 5000 (neutral). "
            "All arithmetic uses integers. Evidence set identified by SHA-256 hash."
        ),
        evidence_set_hash=existing.evidence_set_hash,
        evidence_event_count=existing.evidence_event_count,
        score_scaled=existing.score_scaled,
        score_float=existing.score_scaled / SCORE_SCALE,
        is_cold_start=expl.get("cold_start", {}).get("is_cold_start", False),
        experience_count=existing.experience_count,
        explanation_detail=expl,
        calculated_at=existing.calculated_at,
    )


@router.get(
    "/agents/{agent_id}/reputation/policy",
    response_model=PolicyVersionResponse,
    summary="Get the active reputation policy version",
)
async def get_agent_reputation_policy(
    agent_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
) -> PolicyVersionResponse:
    """Returns the policy version and formula used for scoring this agent.

    The formula_json contains the complete, human-readable specification.
    """
    policy = get_policy_v1()
    pv = await ReputationScoringService.get_policy_version(session)
    if pv is None:
        # Auto-create policy version on first access
        from app.services.reputation_policy.scoring_service import _get_or_create_policy_version
        pv = await _get_or_create_policy_version(session, policy)
        await session.commit()

    return PolicyVersionResponse(
        id=pv.id,
        policy_name=pv.policy_name,
        version_number=pv.version_number,
        description=pv.description,
        formula_json=pv.formula_json or {},
        is_active=pv.is_active,
        activated_at=pv.activated_at,
        deprecated_at=pv.deprecated_at,
        created_at=pv.created_at,
        version_string=pv.version_string,
    )


@router.get(
    "/agents/{agent_id}/reputation/evidence",
    summary="Get evidence set hash and event count used in latest score calculation",
)
async def get_agent_reputation_evidence(
    agent_id: uuid.UUID,
    chain_id: int = Query(default=CHAIN_ID_ANVIL),
    session: AsyncSession = Depends(get_db),
) -> dict:
    """Returns the evidence set hash that identifies exactly which evidence produced the score.

    The evidence_set_hash is a SHA-256 of the canonical ordered set of
    reputation event IDs and outcome types.  Same hash = same score.
    """
    policy = get_policy_v1()
    existing = await ReputationScoringService.get_current_score(
        session, agent_id, chain_id, policy.POLICY_VERSION_STRING
    )
    if existing is None:
        return {
            "agent_id": str(agent_id),
            "chain_id": chain_id,
            "policy_version": policy.POLICY_VERSION_STRING,
            "evidence_set_hash": None,
            "evidence_event_count": 0,
            "has_score": False,
        }
    return {
        "agent_id": str(agent_id),
        "chain_id": chain_id,
        "policy_version": existing.policy_version,
        "evidence_set_hash": existing.evidence_set_hash,
        "evidence_event_count": existing.evidence_event_count,
        "score_scaled": existing.score_scaled,
        "calculated_at": existing.calculated_at.isoformat(),
        "has_score": True,
    }


@router.post(
    "/agents/{agent_id}/reputation/recalculate",
    response_model=ReputationScoreResponse,
    summary="Admin: trigger deterministic score recalculation from authoritative evidence",
    description=(
        "ADMIN ONLY. Triggers a fresh score computation from canonical evidence. "
        "Admins CANNOT set a score directly — the score is always derived from evidence. "
        "Rate-limited to prevent abuse. Every call is audit-logged."
    ),
)
async def admin_recalculate_reputation(
    agent_id: uuid.UUID,
    payload: AdminRecalculateRequest,
    request: Request,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin_user),
) -> ReputationScoreResponse:
    """Admin-only deterministic recalculation endpoint.

    - Authenticated: Yes (admin only)
    - Rate-limited: Yes (enforced by API gateway)
    - Audit-logged: Yes
    - Score is always derived from evidence (never manually set)
    """
    client_ip = request.client.host if request.client else None
    try:
        score = await ReputationScoringService.trigger_admin_recalculation(
            session=session,
            agent_id=agent_id,
            chain_id=payload.chain_id,
            actor_user=current_user,
            client_ip=client_ip,
        )
        await session.commit()
        return _score_to_response(score)
    except Exception as exc:
        await session.rollback()
        logger.error(
            "Admin recalculation failed for agent %s by user %s: %s",
            agent_id,
            current_user.id,
            exc,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Recalculation failed: {exc}",
        ) from exc
