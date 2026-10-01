"""Settlement API endpoints for Phase 6.2B."""

import logging
from typing import Any
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.dependencies import get_db, require_authenticated_user
from app.models.settlement import Settlement, SettlementStatus
from app.models.user import User, UserRole
from app.schemas.settlement import (
    SettlementAuthorizeRequest,
    SettlementCancelRequest,
    SettlementCreateRequest,
    SettlementListResponse,
    SettlementResponse,
)
from app.services.settlement.errors import (
    SettlementAlreadyExistsError,
    SettlementAuthorizationError,
    SettlementConfirmationError,
    SettlementEscrowVerificationError,
    SettlementMainnetBlockedError,
    SettlementNotFoundError,
    SettlementOrchestrationMismatchError,
    SettlementStateConflictError,
)
from app.services.settlement.service import SettlementService

logger = logging.getLogger("agentchain.api.settlements")

router = APIRouter(prefix="/settlements", tags=["settlements"])


def _to_settlement_response(s: Settlement) -> SettlementResponse:
    return SettlementResponse(
        id=s.id,
        idempotency_key=s.idempotency_key,
        chain_id=s.chain_id,
        escrow_contract=s.escrow_contract,
        escrow_id=s.escrow_id,
        client_address=s.client_address,
        beneficiary_address=s.beneficiary_address,
        token_address=s.token_address,
        amount=str(s.amount),
        action=s.action,
        status=s.status,
        authorization_version=s.authorization_version,
        orchestration_id=s.orchestration_id,
        execution_id=s.execution_id,
        authorized_by=s.authorized_by,
        authorized_at=s.authorized_at,
        authorization_reason=s.authorization_reason,
        blocked_reason=s.blocked_reason,
        transaction_intent_id=s.transaction_intent_id,
        submitted_at=s.submitted_at,
        confirmed_at=s.confirmed_at,
        failed_at=s.failed_at,
        cancelled_at=s.cancelled_at,
        settlement_tx_hash=s.settlement_tx_hash,
        block_number=s.block_number,
        block_hash=s.block_hash,
        confirmations=s.confirmations,
        error_message=s.error_message,
        metadata_json=s.metadata_json or {},
        created_at=s.created_at,
        updated_at=s.updated_at,
        history=[h for h in (s.history or [])],
    )


@router.post("", response_model=SettlementResponse, status_code=status.HTTP_201_CREATED)
async def create_settlement_endpoint(
    payload: SettlementCreateRequest,
    request: Request,
    response: Response,
    current_user: User = Depends(require_authenticated_user),
    db: AsyncSession = Depends(get_db),
) -> SettlementResponse:
    """Creates a new settlement request in PENDING_AUTHORIZATION state idempotently."""
    client_ip = request.client.host if request.client else None

    try:
        settlement, was_created = await SettlementService.create_settlement(
            session=db,
            chain_id=payload.chain_id,
            escrow_id=payload.escrow_id,
            action=payload.action,
            user_id=current_user.id,
            orchestration_id=payload.orchestration_id,
            execution_id=payload.execution_id,
            authorization_version=payload.authorization_version,
            beneficiary_amount=payload.beneficiary_amount,
            client_refund_amount=payload.client_refund_amount,
            metadata_json=payload.metadata_json,
            actor_user=current_user,
            client_ip=client_ip,
        )
        await db.commit()

        if not was_created:
            response.status_code = status.HTTP_200_OK

        # Reload with history
        stmt = (
            select(Settlement)
            .where(Settlement.id == settlement.id)
            .options(selectinload(Settlement.history))
        )
        loaded = (await db.execute(stmt)).scalar_one()
        return _to_settlement_response(loaded)

    except SettlementMainnetBlockedError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(exc),
        )
    except (SettlementEscrowVerificationError, SettlementConfirmationError) as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(exc),
        )
    except Exception as exc:
        await db.rollback()
        logger.exception("Failed to create settlement: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal settlement creation error",
        )


@router.get("/{settlement_id}", response_model=SettlementResponse)
async def get_settlement_endpoint(
    settlement_id: uuid.UUID,
    current_user: User = Depends(require_authenticated_user),
    db: AsyncSession = Depends(get_db),
) -> SettlementResponse:
    """Retrieves settlement state and audit transition history."""
    stmt = (
        select(Settlement)
        .where(Settlement.id == settlement_id)
        .options(selectinload(Settlement.history))
    )
    settlement = (await db.execute(stmt)).scalar_one_or_none()
    if not settlement:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Settlement {settlement_id} not found",
        )

    # Permission check: client, authorizer, or platform admin
    is_admin = current_user.primary_role == UserRole.ADMIN.value
    is_owner = (
        settlement.user_id == current_user.id
        or (current_user.wallet_address and current_user.wallet_address.lower() in (settlement.client_address.lower(), settlement.beneficiary_address.lower()))
    )
    if not (is_admin or is_owner):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden: insufficient permissions to access this settlement",
        )

    return _to_settlement_response(settlement)


@router.get("", response_model=SettlementListResponse)
async def list_settlements_endpoint(
    chain_id: int | None = Query(None),
    status_filter: str | None = Query(None, alias="status"),
    escrow_id: str | None = Query(None),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    current_user: User = Depends(require_authenticated_user),
    db: AsyncSession = Depends(get_db),
) -> SettlementListResponse:
    """Lists settlements with optional filtering."""
    stmt = select(Settlement).options(selectinload(Settlement.history))

    is_admin = current_user.primary_role == UserRole.ADMIN.value
    if not is_admin:
        # Non-admins can only see their own settlements
        if current_user.wallet_address:
            w_lower = current_user.wallet_address.lower()
            stmt = stmt.where(
                (Settlement.user_id == current_user.id)
                | (Settlement.client_address == w_lower)
                | (Settlement.beneficiary_address == w_lower)
            )
        else:
            stmt = stmt.where(Settlement.user_id == current_user.id)

    if chain_id is not None:
        stmt = stmt.where(Settlement.chain_id == chain_id)
    if status_filter:
        stmt = stmt.where(Settlement.status == status_filter.upper())
    if escrow_id:
        stmt = stmt.where(Settlement.escrow_id == escrow_id.strip())

    count_stmt = select(func.count()).select_from(stmt.subquery())
    total = (await db.execute(count_stmt)).scalar() or 0

    stmt = stmt.order_by(Settlement.created_at.desc()).offset(offset).limit(limit)
    records = (await db.execute(stmt)).scalars().all()

    return SettlementListResponse(
        settlements=[_to_settlement_response(r) for r in records],
        total=total,
    )


@router.post("/{settlement_id}/authorize", response_model=SettlementResponse)
async def authorize_settlement_endpoint(
    settlement_id: uuid.UUID,
    payload: SettlementAuthorizeRequest,
    request: Request,
    current_user: User = Depends(require_authenticated_user),
    db: AsyncSession = Depends(get_db),
) -> SettlementResponse:
    """Evaluates policy and authorizes a settlement for on-chain submission."""
    client_ip = request.client.host if request.client else None

    try:
        settlement = await SettlementService.authorize_settlement(
            session=db,
            settlement_id=settlement_id,
            actor_user=current_user,
            caller_wallet=current_user.wallet_address,
            reason=payload.reason,
            client_ip=client_ip,
        )
        await db.commit()

        # Reload with history
        stmt = (
            select(Settlement)
            .where(Settlement.id == settlement.id)
            .options(selectinload(Settlement.history))
        )
        loaded = (await db.execute(stmt)).scalar_one()
        return _to_settlement_response(loaded)

    except SettlementNotFoundError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        )
    except SettlementStateConflictError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        )
    except SettlementAuthorizationError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(exc),
        )
    except (SettlementEscrowVerificationError, SettlementConfirmationError, SettlementOrchestrationMismatchError) as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(exc),
        )
    except Exception as exc:
        await db.rollback()
        logger.exception("Failed to authorize settlement %s: %s", settlement_id, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal settlement authorization error",
        )


@router.post("/{settlement_id}/cancel", response_model=SettlementResponse)
async def cancel_settlement_endpoint(
    settlement_id: uuid.UUID,
    payload: SettlementCancelRequest,
    request: Request,
    current_user: User = Depends(require_authenticated_user),
    db: AsyncSession = Depends(get_db),
) -> SettlementResponse:
    """Cancels a pending settlement before blockchain broadcast."""
    client_ip = request.client.host if request.client else None

    try:
        settlement = await SettlementService.cancel_settlement(
            session=db,
            settlement_id=settlement_id,
            actor_user=current_user,
            reason=payload.reason,
            client_ip=client_ip,
        )
        await db.commit()

        # Reload with history
        stmt = (
            select(Settlement)
            .where(Settlement.id == settlement.id)
            .options(selectinload(Settlement.history))
        )
        loaded = (await db.execute(stmt)).scalar_one()
        return _to_settlement_response(loaded)

    except SettlementNotFoundError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        )
    except SettlementStateConflictError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        )
    except Exception as exc:
        await db.rollback()
        logger.exception("Failed to cancel settlement %s: %s", settlement_id, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal settlement cancellation error",
        )
