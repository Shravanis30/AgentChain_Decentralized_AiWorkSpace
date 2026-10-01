"""API endpoints for Cryptographic Result Notarization and Verification."""

import logging
from typing import Any
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_db, require_authenticated_user
from app.models.notarization import ResultNotarization
from app.models.user import User
from app.schemas.notarization import (
    NotarizationCreateRequest,
    NotarizationResponse,
    NotarizationVerifyRequest,
    NotarizationVerifyResponse,
)
from app.services.blockchain.errors import (
    ContractMismatchError,
    MainnetSubmissionBlockedError,
)
from app.services.notarization.errors import (
    ExecutionNotEligibleError,
    ExecutionNotFoundError,
    NotarizationNotFoundError,
    ResultHashImmutabilityViolationError,
)
from app.services.notarization.service import NotarizationService

logger = logging.getLogger("agentchain.api.notarizations")

router = APIRouter(prefix="/notarizations", tags=["notarizations"])


def _to_notarization_response(n: ResultNotarization) -> NotarizationResponse:
    return NotarizationResponse(
        id=n.id,
        idempotency_key=n.idempotency_key,
        notarization_key=n.notarization_key,
        execution_id=n.execution_id,
        chain_id=n.chain_id,
        contract_address=n.contract_address,
        result_hash=n.result_hash,
        hash_algorithm=n.hash_algorithm,
        canonicalization_version=n.canonicalization_version,
        artifact_reference=n.artifact_reference,
        artifact_commitment=n.artifact_commitment,
        status=n.status,
        transaction_intent_id=n.transaction_intent_id,
        transaction_hash=n.transaction_hash,
        block_number=n.block_number,
        block_hash=n.block_hash,
        confirmations=n.confirmations,
        is_canonical=n.is_canonical,
        error_message=n.error_message,
        payload_json=n.payload_json or {},
        created_at=n.created_at,
        updated_at=n.updated_at,
        confirmed_at=n.confirmed_at,
        reorged_at=n.reorged_at,
    )


@router.post(
    "",
    response_model=NotarizationResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create result notarization proof intent",
)
async def create_notarization(
    payload: NotarizationCreateRequest,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_authenticated_user),
) -> NotarizationResponse:
    client_ip = request.client.host if request.client else None
    try:
        notarization, was_created = await NotarizationService.create_notarization(
            session=session,
            execution_id=payload.execution_id,
            chain_id=payload.chain_id,
            artifact_reference=payload.artifact_reference,
            idempotency_key=payload.idempotency_key,
            actor_user=current_user,
            client_ip=client_ip,
        )
        if not was_created:
            response.status_code = status.HTTP_200_OK

        return _to_notarization_response(notarization)
    except ExecutionNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ExecutionNotEligibleError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except ResultHashImmutabilityViolationError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except MainnetSubmissionBlockedError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except ContractMismatchError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.get(
    "/{id}",
    response_model=NotarizationResponse,
    summary="Get notarization by ID",
)
async def get_notarization_by_id(
    id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
) -> NotarizationResponse:
    notarization = await NotarizationService.get_notarization_by_id(session, id)
    if not notarization:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Notarization {id} not found")
    return _to_notarization_response(notarization)


@router.get(
    "/execution/{execution_id}",
    response_model=NotarizationResponse,
    summary="Get notarization by execution ID",
)
async def get_notarization_by_execution_id(
    execution_id: uuid.UUID,
    chain_id: int | None = Query(None, description="Optional target chain ID"),
    session: AsyncSession = Depends(get_db),
) -> NotarizationResponse:
    notarization = await NotarizationService.get_notarization_by_execution_id(
        session, execution_id, chain_id
    )
    if not notarization:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Notarization for execution {execution_id} not found",
        )
    return _to_notarization_response(notarization)


@router.post(
    "/verify",
    response_model=NotarizationVerifyResponse,
    summary="Cryptographic and on-chain verification of an execution result",
)
async def verify_notarization(
    payload: NotarizationVerifyRequest,
    session: AsyncSession = Depends(get_db),
) -> NotarizationVerifyResponse:
    """Verifies an execution result against canonical on-chain notarization.

    If result_payload is provided, recomputes RFC 8785 SHA-256 hash server-side.
    Verifies match against canonical on-chain notarized hash.
    """
    return await NotarizationService.verify_execution(
        session=session,
        execution_id=payload.execution_id,
        result_payload=payload.result_payload,
        result_hash=payload.result_hash,
        chain_id=payload.chain_id,
    )


@router.get(
    "/verify/{execution_id}",
    response_model=NotarizationVerifyResponse,
    summary="Verification status for an execution ID",
)
async def get_verification_status(
    execution_id: uuid.UUID,
    chain_id: int | None = Query(None, description="Target chain ID"),
    session: AsyncSession = Depends(get_db),
) -> NotarizationVerifyResponse:
    """Read-only verification status lookup for an execution proof."""
    return await NotarizationService.verify_execution(
        session=session,
        execution_id=execution_id,
        chain_id=chain_id,
    )
