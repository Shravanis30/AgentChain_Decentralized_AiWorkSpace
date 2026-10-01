"""API endpoints for Verified Reputation Evidence and Observability."""

import logging
from typing import Any
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_db, require_authenticated_user
from app.models.reputation import ReputationEvent, ReputationProfile
from app.models.user import User
from app.schemas.reputation import (
    CreateReputationEventRequest,
    ReputationEventResponse,
    ReputationProfileResponse,
    ReputationVerifyResponse,
)
from app.services.blockchain.config import CHAIN_ID_ANVIL
from app.services.blockchain.errors import (
    ContractMismatchError,
    MainnetSubmissionBlockedError,
)
from app.services.reputation.errors import (
    ConflictingReputationEventError,
    ExecutionNotFoundError,
    ExecutionNotTerminalError,
    InsufficientConfirmationsError,
    InvalidOutcomeClassificationError,
    MainnetReputationBlockedError,
    NotarizationNotCanonicalError,
    ReputationError,
    ResultHashMismatchError,
    ResultNotNotarizedError,
)
from app.services.reputation.service import ReputationService

logger = logging.getLogger("agentchain.api.reputation")

router = APIRouter(tags=["reputation"])


def _to_event_response(e: ReputationEvent) -> ReputationEventResponse:
    return ReputationEventResponse(
        id=e.id,
        idempotency_key=e.idempotency_key,
        reputation_key=e.reputation_key,
        agent_id=e.agent_id,
        agent_version_id=e.agent_version_id,
        execution_id=e.execution_id,
        outcome_type=e.outcome_type,
        result_hash=e.result_hash,
        notarization_id=e.notarization_id,
        evidence_hash=e.evidence_hash,
        chain_id=e.chain_id,
        contract_address=e.contract_address,
        status=e.status,
        transaction_intent_id=e.transaction_intent_id,
        transaction_hash=e.transaction_hash,
        block_number=e.block_number,
        block_hash=e.block_hash,
        confirmations=e.confirmations,
        is_canonical=e.is_canonical,
        error_message=e.error_message,
        metadata_json=e.metadata_json or {},
        created_at=e.created_at,
        updated_at=e.updated_at,
        confirmed_at=e.confirmed_at,
        reorged_at=e.reorged_at,
    )


def _to_profile_response(p: ReputationProfile) -> ReputationProfileResponse:
    return ReputationProfileResponse(
        agent_id=p.agent_id,
        chain_id=p.chain_id,
        total_verified_executions=p.total_verified_executions,
        verified_successes=p.verified_successes,
        verified_failures=p.verified_failures,
        verified_timeouts=p.verified_timeouts,
        verified_cancellations=p.verified_cancellations,
        canonical_event_count=p.canonical_event_count,
        first_verified_execution_id=p.first_verified_execution_id,
        latest_verified_execution_id=p.latest_verified_execution_id,
        latest_verified_outcome=p.latest_verified_outcome,
        latest_verified_at=p.latest_verified_at,
        success_rate=float(p.success_rate) if p.success_rate is not None else None,
        last_recalculated_at=p.last_recalculated_at,
        created_at=p.created_at,
        updated_at=p.updated_at,
    )


@router.get(
    "/agents/{agent_id}/reputation",
    response_model=ReputationProfileResponse,
    summary="Get verified reputation profile for an agent",
)
async def get_agent_reputation_profile(
    agent_id: uuid.UUID,
    chain_id: int = Query(default=CHAIN_ID_ANVIL, description="Blockchain chain ID"),
    session: AsyncSession = Depends(get_db),
) -> ReputationProfileResponse:
    try:
        profile = await ReputationService.get_agent_profile(
            session=session,
            agent_id=agent_id,
            chain_id=chain_id,
        )
        return _to_profile_response(profile)
    except Exception as exc:
        logger.error("Failed to retrieve reputation profile for agent %s: %s", agent_id, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Could not load reputation profile: {exc}",
        ) from exc


@router.get(
    "/agents/{agent_id}/reputation/events",
    response_model=list[ReputationEventResponse],
    summary="List verified reputation evidence events for an agent",
)
async def list_agent_reputation_events(
    agent_id: uuid.UUID,
    chain_id: int = Query(default=CHAIN_ID_ANVIL, description="Blockchain chain ID"),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    canonical_only: bool = Query(default=True, description="Filter for canonical events only"),
    session: AsyncSession = Depends(get_db),
) -> list[ReputationEventResponse]:
    stmt = (
        sa.select(ReputationEvent)
        .where(
            ReputationEvent.agent_id == agent_id,
            ReputationEvent.chain_id == chain_id,
        )
    )
    if canonical_only:
        stmt = stmt.where(ReputationEvent.is_canonical.is_(True))

    stmt = stmt.order_by(ReputationEvent.created_at.desc()).offset(offset).limit(limit)
    events = (await session.execute(stmt)).scalars().all()
    return [_to_event_response(e) for e in events]


@router.get(
    "/executions/{execution_id}/reputation",
    response_model=ReputationEventResponse,
    summary="Get verified reputation event for an execution",
)
async def get_execution_reputation_event(
    execution_id: uuid.UUID,
    chain_id: int = Query(default=CHAIN_ID_ANVIL, description="Blockchain chain ID"),
    session: AsyncSession = Depends(get_db),
) -> ReputationEventResponse:
    stmt = (
        sa.select(ReputationEvent)
        .where(
            ReputationEvent.execution_id == execution_id,
            ReputationEvent.chain_id == chain_id,
        )
    )
    event = (await session.execute(stmt)).scalar_one_or_none()
    if not event:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Reputation event for execution {execution_id} on chain {chain_id} not found",
        )
    return _to_event_response(event)


@router.get(
    "/reputation/events/{reputation_event_id}",
    response_model=ReputationEventResponse,
    summary="Get reputation event by ID",
)
async def get_reputation_event_by_id(
    reputation_event_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
) -> ReputationEventResponse:
    stmt = sa.select(ReputationEvent).where(ReputationEvent.id == reputation_event_id)
    event = (await session.execute(stmt)).scalar_one_or_none()
    if not event:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Reputation event {reputation_event_id} not found",
        )
    return _to_event_response(event)


@router.get(
    "/reputation/verify/{reputation_event_id}",
    response_model=ReputationVerifyResponse,
    summary="Verify cryptographic and on-chain validity of a reputation event",
)
async def verify_reputation_event(
    reputation_event_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
) -> ReputationVerifyResponse:
    try:
        return await ReputationService.verify_reputation_event(
            session=session,
            reputation_event_id=reputation_event_id,
        )
    except ReputationError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc


@router.post(
    "/reputation/events",
    response_model=ReputationEventResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create verified reputation event intent (authenticated internal)",
)
async def create_reputation_event(
    payload: CreateReputationEventRequest,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_authenticated_user),
) -> ReputationEventResponse:
    client_ip = request.client.host if request.client else None
    try:
        event, was_created = await ReputationService.create_reputation_event(
            session=session,
            execution_id=payload.execution_id,
            chain_id=payload.chain_id,
            idempotency_key=payload.idempotency_key,
            evidence_payload=payload.evidence_payload,
            actor_user=current_user,
            client_ip=client_ip,
        )
        if not was_created:
            response.status_code = status.HTTP_200_OK

        return _to_event_response(event)
    except ExecutionNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ExecutionNotTerminalError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except ResultNotNotarizedError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except ResultHashMismatchError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except NotarizationNotCanonicalError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except InsufficientConfirmationsError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except ConflictingReputationEventError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except MainnetReputationBlockedError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except ContractMismatchError as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc
    except Exception as exc:
        logger.error("Failed to create reputation event: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Reputation creation failed: {exc}",
        ) from exc
