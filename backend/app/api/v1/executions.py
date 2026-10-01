import asyncio
import json
import logging
import uuid
from typing import AsyncGenerator
from fastapi import APIRouter, Body, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db, require_authenticated_user
from app.core.dependencies import get_session_token_from_request
from app.core.metrics import metrics
from app.models.agent import ExecutionStatus
from app.models.base import utc_now
from app.models.user import User, UserRole
from app.schemas.agent import AgentExecutionResponse
from app.services.agent_execution import AgentExecutionService
from app.services.auth import AuthService
from app.services.rate_limiter import get_redis_client
from app.services.sse_ticket import SSETicketService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/executions", tags=["Agent Executions"])


async def get_sse_authenticated_user(
    execution_id: uuid.UUID,
    request: Request,
    db: AsyncSession,
) -> User:
    """
    Hardened SSE authentication:
    1. Primary: Session cookie or Authorization Bearer header.
    2. Fallback: Short-lived, execution-scoped, single-use ?sse_token ticket.
    Long-lived session tokens in URL query strings are strictly rejected.
    """
    # 1. Primary: Bearer header or session cookie
    session_token = await get_session_token_from_request(request)
    if session_token:
        session = await AuthService.get_session_by_token(db, session_token)
        if session and session.user:
            return session.user

    # 2. Secondary fallback: execution-scoped short-lived sse_token ticket
    sse_token = request.query_params.get("sse_token")
    if sse_token:
        user_id = await SSETicketService.validate_and_consume_ticket(
            token=sse_token,
            expected_execution_id=execution_id,
        )
        if user_id:
            user = await db.get(User, user_id)
            if user:
                return user

    metrics.inc_counter("sse_auth_failure_total")
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Authentication required. Provide valid session credentials or an execution-scoped sse_token ticket.",
        headers={"WWW-Authenticate": "Bearer"},
    )


@router.post(
    "/{execution_id}/sse-token",
    summary="Generate a short-lived execution-scoped SSE ticket",
    status_code=status.HTTP_200_OK,
)
async def generate_sse_token(
    execution_id: uuid.UUID,
    user: User = Depends(require_authenticated_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """
    Issues a short-lived (60s), single-use ticket scoped exclusively to the given execution.
    Normal session tokens are never passed through URLs.
    """
    execution = await AgentExecutionService.get_execution_by_id(db, execution_id)
    if not execution:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Execution '{execution_id}' not found",
        )

    # Authorization check
    is_requester = execution.requested_by == user.id
    is_owner = execution.agent.owner_user_id == user.id
    has_privileged_role = user.has_role(UserRole.ADMIN) or user.has_role(UserRole.VERIFIER)

    if not (is_requester or is_owner or has_privileged_role):
        metrics.inc_counter("sse_auth_failure_total")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You are not authorized to subscribe to this execution",
        )

    ticket = await SSETicketService.create_ticket(user.id, execution_id)
    return {
        "sse_token": ticket,
        "expires_in": 60,
        "execution_id": str(execution_id),
    }


@router.get(
    "/{execution_id}",
    response_model=AgentExecutionResponse,
    status_code=status.HTTP_200_OK,
    summary="Get execution status, inputs, results, and canonical hashes",
)
async def get_execution(
    execution_id: uuid.UUID,
    user: User = Depends(require_authenticated_user),
    db: AsyncSession = Depends(get_db),
) -> AgentExecutionResponse:
    execution = await AgentExecutionService.get_execution_by_id(db, execution_id)
    if not execution:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Execution '{execution_id}' not found",
        )

    # Authorization: only requester, agent owner, verifier, or admin
    is_requester = execution.requested_by == user.id
    is_owner = execution.agent.owner_user_id == user.id
    has_privileged_role = user.has_role(UserRole.ADMIN) or user.has_role(UserRole.VERIFIER)

    if not (is_requester or is_owner or has_privileged_role):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You are not authorized to view this execution",
        )

    return AgentExecutionResponse.model_validate(execution)


@router.post(
    "/{execution_id}/cancel",
    response_model=AgentExecutionResponse,
    status_code=status.HTTP_200_OK,
    summary="Request execution cancellation",
)
async def cancel_execution(
    execution_id: uuid.UUID,
    request: Request,
    body: dict[str, str] | None = Body(default=None),
    user: User = Depends(require_authenticated_user),
    db: AsyncSession = Depends(get_db),
) -> AgentExecutionResponse:
    reason = body.get("reason") if body else None
    cancelled = await AgentExecutionService.cancel_execution(
        db=db,
        execution_id=execution_id,
        user_id=user.id,
        user_role=user.primary_role,
        reason=reason,
        request=request,
    )
    return AgentExecutionResponse.model_validate(cancelled)


@router.get(
    "/{execution_id}/events",
    summary="Server-Sent Events (SSE) stream for execution lifecycle progress",
)
async def execution_events_stream(
    execution_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> StreamingResponse:
    """
    Subscribes to live Server-Sent Events for an execution.
    Streams state transitions: queued, started, progress, completed, failed, cancelled, timeout.
    Authenticated via session cookie, Bearer header, or short-lived execution-scoped sse_token.
    """
    user = await get_sse_authenticated_user(execution_id=execution_id, request=request, db=db)

    execution = await AgentExecutionService.get_execution_by_id(db, execution_id)
    if not execution:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Execution '{execution_id}' not found",
        )

    # Authorization check
    is_requester = execution.requested_by == user.id
    is_owner = execution.agent.owner_user_id == user.id
    has_privileged_role = user.has_role(UserRole.ADMIN) or user.has_role(UserRole.VERIFIER)

    if not (is_requester or is_owner or has_privileged_role):
        metrics.inc_counter("sse_auth_failure_total")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You are not authorized to subscribe to this execution",
        )

    async def event_generator() -> AsyncGenerator[str, None]:
        # 1. Emit snapshot of current execution status
        initial_event = {
            "execution_id": str(execution.id),
            "event_type": execution.status.lower(),
            "timestamp": utc_now().isoformat(),
            "sequence": 0,
            "payload": {
                "status": execution.status,
                "input_hash": execution.input_hash,
                "output_hash": execution.output_hash,
                "started_at": execution.started_at.isoformat() if execution.started_at else None,
                "completed_at": execution.completed_at.isoformat() if execution.completed_at else None,
                "error_code": execution.error_code,
                "error_message": execution.error_message,
            },
        }
        yield f"event: execution_event\ndata: {json.dumps(initial_event)}\n\n"
        await asyncio.sleep(0.01)

        # If already terminal, finish stream
        terminal_statuses = {
            ExecutionStatus.SUCCEEDED.value,
            ExecutionStatus.FAILED.value,
            ExecutionStatus.TIMED_OUT.value,
            ExecutionStatus.CANCELLED.value,
        }
        if execution.status in terminal_statuses:
            return

        # 2. Subscribe to Redis pubsub channel
        redis = get_redis_client()
        pubsub = redis.pubsub()
        channel = f"agentchain:events:{execution_id}"
        await pubsub.subscribe(channel)

        try:
            while True:
                if await request.is_disconnected():
                    break

                try:
                    # Non-blocking read with small timeout
                    message = await pubsub.get_message(
                        ignore_subscribe_messages=True,
                        timeout=0.5,
                    )
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    logger.warning(f"Error reading SSE pubsub message: {e}")
                    break

                if message and message.get("type") == "message":
                    raw_data = message.get("data")
                    if isinstance(raw_data, bytes):
                        raw_data = raw_data.decode("utf-8")

                    yield f"event: execution_event\ndata: {raw_data}\n\n"

                    # Check if terminal event
                    try:
                        data_obj = json.loads(raw_data)
                        ev_type = data_obj.get("event_type", "").upper()
                        status_val = data_obj.get("payload", {}).get("status", "").upper()
                        if ev_type in terminal_statuses or status_val in terminal_statuses:
                            break
                    except Exception:
                        pass
                else:
                    # Heartbeat comment to keep connection alive
                    yield ": ping\n\n"

        finally:
            try:
                await pubsub.unsubscribe(channel)
                await pubsub.aclose()
            except Exception:
                pass

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
