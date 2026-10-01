"""Distribution API endpoints for Phase 6.2C (85/10/5 Economic Settlements)."""

import logging
from typing import Any
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.dependencies import get_db, require_authenticated_user
from app.models.distribution import Distribution, DistributionHistory
from app.models.user import User
from app.schemas.distribution import (
    DistributionCreateRequest,
    DistributionDetailResponse,
    DistributionHistoryItem,
    DistributionListResponse,
    DistributionResponse,
)
from app.services.distribution.errors import (
    DistributionAuthorizationError,
    DistributionConservationError,
    DistributionDuplicateRecipientError,
    DistributionMainnetBlockedError,
    DistributionNotFoundError,
    DistributionStateConflictError,
)
from app.services.distribution.service import DistributionService

logger = logging.getLogger("agentchain.api.distributions")

router = APIRouter(prefix="/distributions", tags=["distributions"])


def _to_distribution_response(d: Distribution) -> DistributionResponse:
    return DistributionResponse(
        id=d.id,
        idempotency_key=d.idempotency_key,
        distribution_key=d.distribution_key,
        chain_id=d.chain_id,
        escrow_contract=d.escrow_contract,
        escrow_id=d.escrow_id,
        settlement_id=d.settlement_id,
        distributor_contract=d.distributor_contract,
        token_address=d.token_address,
        gross_amount=str(d.gross_amount),
        developer_amount=str(d.developer_amount),
        developer_recipient=d.developer_recipient,
        staker_amount=str(d.staker_amount),
        staker_recipient=d.staker_recipient,
        dao_amount=str(d.dao_amount),
        dao_recipient=d.dao_recipient,
        distribution_version=d.distribution_version,
        status=d.status,
        transaction_intent_id=d.transaction_intent_id,
        distribution_tx_hash=d.distribution_tx_hash,
        onchain_distribution_id=d.onchain_distribution_id,
        block_number=d.block_number,
        block_hash=d.block_hash,
        confirmations=d.confirmations,
        error_message=d.error_message,
        created_at=d.created_at,
        updated_at=d.updated_at,
        confirmed_at=d.confirmed_at,
    )


@router.post(
    "",
    response_model=DistributionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create or retrieve economic distribution for authorized settlement",
)
async def create_distribution(
    request_data: DistributionCreateRequest,
    request: Request,
    response: Response,
    current_user: User = Depends(require_authenticated_user),
    session: AsyncSession = Depends(get_db),
) -> DistributionResponse:
    """Creates a deterministic 85/10/5 distribution record from an authorized settlement release.
    
    Security: Percentages, recipients, tokens, and gross amounts cannot be chosen by the caller.
    They are derived strictly from authoritative backend and chain state.
    """
    client_ip = request.client.host if request.client else None

    try:
        dist, was_created = await DistributionService.create_distribution(
            session=session,
            settlement_id=request_data.settlement_id,
            idempotency_key=request_data.idempotency_key,
            metadata_json=request_data.metadata_json,
            actor_user=current_user,
            client_ip=client_ip,
        )
        await session.commit()
    except DistributionMainnetBlockedError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(exc),
        )
    except DistributionNotFoundError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        )
    except DistributionAuthorizationError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(exc),
        )
    except (DistributionStateConflictError, DistributionDuplicateRecipientError, DistributionConservationError) as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        )
    except Exception as exc:
        await session.rollback()
        logger.exception("Unexpected error creating distribution: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal distribution service failure",
        )

    if not was_created:
        response.status_code = status.HTTP_200_OK

    return _to_distribution_response(dist)


@router.get(
    "",
    response_model=DistributionListResponse,
    summary="List distributions with optional filters",
)
async def list_distributions(
    chain_id: int | None = Query(None, description="Filter by blockchain chain ID"),
    distribution_status: str | None = Query(None, alias="status", description="Filter by status"),
    escrow_id: str | None = Query(None, description="Filter by escrow ID"),
    settlement_id: uuid.UUID | None = Query(None, description="Filter by settlement ID"),
    skip: int = Query(0, ge=0, description="Offset for pagination"),
    limit: int = Query(50, ge=1, le=100, description="Page size"),
    current_user: User = Depends(require_authenticated_user),
    session: AsyncSession = Depends(get_db),
) -> DistributionListResponse:
    """Lists distribution records with filtering and pagination."""
    items, total = await DistributionService.list_distributions(
        session=session,
        chain_id=chain_id,
        status=distribution_status,
        escrow_id=escrow_id,
        settlement_id=settlement_id,
        skip=skip,
        limit=limit,
    )
    return DistributionListResponse(
        items=[_to_distribution_response(d) for d in items],
        total=total,
    )


@router.get(
    "/{distribution_id}",
    response_model=DistributionDetailResponse,
    summary="Get detailed distribution record with audit history",
)
async def get_distribution(
    distribution_id: uuid.UUID,
    current_user: User = Depends(require_authenticated_user),
    session: AsyncSession = Depends(get_db),
) -> DistributionDetailResponse:
    """Retrieves full details of a distribution including status history."""
    stmt = (
        select(Distribution)
        .where(Distribution.id == distribution_id)
        .options(selectinload(Distribution.history))
    )
    dist = (await session.execute(stmt)).scalar_one_or_none()
    if not dist:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Distribution {distribution_id} not found",
        )

    base_resp = _to_distribution_response(dist)
    history_items = [
        DistributionHistoryItem(
            id=h.id,
            from_status=h.from_status,
            to_status=h.to_status,
            reason=h.reason,
            metadata_json=h.metadata_json,
            created_at=h.created_at,
        )
        for h in dist.history
    ]

    return DistributionDetailResponse(
        **base_resp.model_dump(),
        history=history_items,
    )
