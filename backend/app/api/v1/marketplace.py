"""Marketplace execution and escrow lifecycle API endpoints (Phase 6.8)."""

import logging
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db, require_authenticated_user
from app.models.user import User, UserRole
from app.schemas.marketplace import (
    MarketplaceDisputeCreate,
    MarketplaceDisputeResolve,
    MarketplaceOrderCreate,
    MarketplaceOrderResponse,
)
from app.services.marketplace import (
    MarketplaceAuthorizationError,
    MarketplaceEscrowMismatchError,
    MarketplaceEscrowUnconfirmedError,
    MarketplaceOrderingViolationError,
    MarketplaceOrderNotFoundError,
    MarketplacePriceTamperError,
    MarketplaceStateConflictError,
    marketplace_coordinator,
)

logger = logging.getLogger("agentchain.api.marketplace")

router = APIRouter(prefix="/marketplace", tags=["Marketplace"])


@router.post(
    "/orders",
    response_model=MarketplaceOrderResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new marketplace order with deterministic agent selection and locked price",
)
async def create_order(
    payload: MarketplaceOrderCreate,
    user: User = Depends(require_authenticated_user),
    db: AsyncSession = Depends(get_db),
) -> MarketplaceOrderResponse:
    try:
        order, _ = await marketplace_coordinator.create_order(
            db=db,
            client_user=user,
            goal=payload.goal,
            task_input=payload.task_input,
            chain_id=payload.chain_id,
            idempotency_key=payload.idempotency_key,
            required_capabilities=payload.required_capabilities,
            max_budget_atomic=payload.max_budget_atomic,
            price_currency=payload.price_currency,
            preferred_agent_id=payload.preferred_agent_id,
            preferred_agent_version=payload.preferred_agent_version,
        )
        await db.commit()
        refreshed = await marketplace_coordinator.get_order(db, order.id)
        return MarketplaceOrderResponse.model_validate(refreshed or order)
    except Exception as exc:
        await db.rollback()
        logger.error("Failed to create marketplace order: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )


@router.get(
    "/orders/{order_id}",
    response_model=MarketplaceOrderResponse,
    summary="Get authoritative marketplace order details and lifecycle state",
)
async def get_order(
    order_id: uuid.UUID,
    user: User = Depends(require_authenticated_user),
    db: AsyncSession = Depends(get_db),
) -> MarketplaceOrderResponse:
    order = await marketplace_coordinator.get_order(db, order_id)
    if not order:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Marketplace order {order_id} not found",
        )
    # Check access: ordering client or admin/verifier
    if order.client_user_id != user.id and not (user.has_role(UserRole.ADMIN) or user.has_role(UserRole.VERIFIER)):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied to this marketplace order",
        )
    return MarketplaceOrderResponse.model_validate(order)


@router.post(
    "/orders/{order_id}/verify-escrow",
    response_model=MarketplaceOrderResponse,
    summary="Verify on-chain canonical escrow funding (32-block depth policy)",
)
async def verify_escrow(
    order_id: uuid.UUID,
    user: User = Depends(require_authenticated_user),
    db: AsyncSession = Depends(get_db),
) -> MarketplaceOrderResponse:
    try:
        order = await marketplace_coordinator.verify_and_bind_escrow(db, order_id)
        await db.commit()
        refreshed = await marketplace_coordinator.get_order(db, order.id)
        return MarketplaceOrderResponse.model_validate(refreshed or order)
    except (MarketplaceEscrowMismatchError, MarketplaceEscrowUnconfirmedError) as exc:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    except MarketplaceOrderNotFoundError as exc:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except Exception as exc:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


@router.post(
    "/orders/{order_id}/execute",
    response_model=MarketplaceOrderResponse,
    summary="Enqueue execution for the order (strictly requires confirmed escrow funding)",
)
async def enqueue_execution(
    order_id: uuid.UUID,
    user: User = Depends(require_authenticated_user),
    db: AsyncSession = Depends(get_db),
) -> MarketplaceOrderResponse:
    try:
        await marketplace_coordinator.enqueue_execution(db, order_id)
        await db.commit()
        order = await marketplace_coordinator.get_order(db, order_id)
        return MarketplaceOrderResponse.model_validate(order)
    except MarketplaceOrderingViolationError as exc:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    except MarketplaceOrderNotFoundError as exc:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except Exception as exc:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


@router.post(
    "/orders/{order_id}/notarize-and-verify",
    response_model=MarketplaceOrderResponse,
    summary="Cryptographically notarize execution deliverable and record canonical reputation evidence",
)
async def notarize_and_verify(
    order_id: uuid.UUID,
    user: User = Depends(require_authenticated_user),
    db: AsyncSession = Depends(get_db),
) -> MarketplaceOrderResponse:
    try:
        await marketplace_coordinator.notarize_and_verify_outcome(db, order_id)
        await db.commit()
        order = await marketplace_coordinator.get_order(db, order_id)
        return MarketplaceOrderResponse.model_validate(order)
    except (MarketplaceOrderingViolationError, MarketplaceStateConflictError) as exc:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    except MarketplaceOrderNotFoundError as exc:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except Exception as exc:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


@router.post(
    "/orders/{order_id}/settle",
    response_model=MarketplaceOrderResponse,
    summary="Initiate authoritative on-chain settlement intent (RELEASE for success, REFUND for failure)",
)
async def settle_order(
    order_id: uuid.UUID,
    user: User = Depends(require_authenticated_user),
    db: AsyncSession = Depends(get_db),
) -> MarketplaceOrderResponse:
    try:
        await marketplace_coordinator.initiate_settlement(db, order_id, actor_user=user)
        await db.commit()
        order = await marketplace_coordinator.get_order(db, order_id)
        return MarketplaceOrderResponse.model_validate(order)
    except (MarketplaceOrderingViolationError, MarketplaceStateConflictError) as exc:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    except MarketplaceOrderNotFoundError as exc:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except Exception as exc:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


@router.post(
    "/orders/{order_id}/finalize",
    response_model=MarketplaceOrderResponse,
    summary="Finalize order on confirmed settlement, triggering 85/10/5 economic distribution",
)
async def finalize_order(
    order_id: uuid.UUID,
    user: User = Depends(require_authenticated_user),
    db: AsyncSession = Depends(get_db),
) -> MarketplaceOrderResponse:
    try:
        await marketplace_coordinator.finalize_settlement_and_distribution(db, order_id)
        await db.commit()
        order = await marketplace_coordinator.get_order(db, order_id)
        return MarketplaceOrderResponse.model_validate(order)
    except (MarketplaceEscrowUnconfirmedError, MarketplaceOrderingViolationError) as exc:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    except MarketplaceOrderNotFoundError as exc:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except Exception as exc:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


@router.post(
    "/orders/{order_id}/dispute",
    response_model=MarketplaceOrderResponse,
    summary="Open an escrow dispute for platform-managed multisig arbitration",
)
async def dispute_order(
    order_id: uuid.UUID,
    payload: MarketplaceDisputeCreate,
    user: User = Depends(require_authenticated_user),
    db: AsyncSession = Depends(get_db),
) -> MarketplaceOrderResponse:
    try:
        await marketplace_coordinator.open_dispute(
            db, order_id, dispute_reason=payload.dispute_reason, claimant_user=user
        )
        await db.commit()
        order = await marketplace_coordinator.get_order(db, order_id)
        return MarketplaceOrderResponse.model_validate(order)
    except (MarketplaceAuthorizationError, MarketplaceStateConflictError) as exc:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
    except MarketplaceOrderNotFoundError as exc:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except Exception as exc:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


@router.post(
    "/orders/{order_id}/resolve-dispute",
    response_model=MarketplaceOrderResponse,
    summary="Resolve an escrow dispute (Designated Arbitrator or Admin only)",
)
async def resolve_dispute(
    order_id: uuid.UUID,
    payload: MarketplaceDisputeResolve,
    user: User = Depends(require_authenticated_user),
    db: AsyncSession = Depends(get_db),
) -> MarketplaceOrderResponse:
    try:
        await marketplace_coordinator.resolve_dispute(
            db,
            order_id=order_id,
            beneficiary_amount=payload.beneficiary_amount,
            client_refund_amount=payload.client_refund_amount,
            arbitrator_user=user,
            reason=payload.reason,
        )
        await db.commit()
        order = await marketplace_coordinator.get_order(db, order_id)
        return MarketplaceOrderResponse.model_validate(order)
    except MarketplaceAuthorizationError as exc:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
    except (MarketplaceEscrowMismatchError, MarketplaceStateConflictError) as exc:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    except MarketplaceOrderNotFoundError as exc:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except Exception as exc:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
