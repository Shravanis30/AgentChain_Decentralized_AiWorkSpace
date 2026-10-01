import asyncio
from collections.abc import AsyncGenerator
import json
import logging
from typing import Any
import uuid

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.dependencies import get_db, get_session_token_from_request, require_authenticated_user
from app.core.metrics import metrics
from app.models.base import utc_now
from app.models.orchestration import (
    Artifact,
    Orchestration,
    OrchestrationStatus,
    OrchestrationTask,
)
from app.models.user import User, UserRole
from app.schemas.orchestration import (
    ArtifactResponse,
    OrchestrationCreate,
    OrchestrationListResponse,
    OrchestrationResponse,
    SSETokenResponse,
    TaskResponse,
)
from app.services.auth import AuthService
from app.services.dag_validator import DAGValidationError
from app.services.orchestration_engine import orchestration_engine
from app.services.rate_limiter import get_redis_client
from app.services.sse_ticket import SSETicketService

logger = logging.getLogger("agentchain.api.orchestrations")

router = APIRouter(prefix="/orchestrations", tags=["orchestrations"])


async def get_sse_authenticated_user_for_orch(
    orchestration_id: uuid.UUID,
    request: Request,
    db: AsyncSession,
) -> User:
    """
    Hardened SSE authentication for Orchestration stream:
    1. Primary: Session cookie or Authorization Bearer header.
    2. Fallback: Short-lived, orchestration-scoped, single-use ?sse_token ticket.
    Long-lived session tokens in URL query strings are strictly rejected.
    """
    # 1. Primary: Bearer header or session cookie
    session_token = await get_session_token_from_request(request)
    if session_token:
        session = await AuthService.get_session_by_token(db, session_token)
        if session and session.user:
            return session.user

    # 2. Secondary fallback: orchestration-scoped short-lived sse_token ticket
    sse_token = request.query_params.get("sse_token")
    if sse_token:
        user_id = await SSETicketService.validate_and_consume_orchestration_ticket(
            token=sse_token,
            expected_orchestration_id=orchestration_id,
        )
        if user_id:
            user = await db.get(User, user_id)
            if user:
                return user

    metrics.inc_counter("sse_auth_failure_total")
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Authentication required. Provide valid session credentials or an orchestration-scoped sse_token ticket.",
        headers={"WWW-Authenticate": "Bearer"},
    )


def _serialize_orchestration(orch: Orchestration) -> OrchestrationResponse:
    tasks_resp = [TaskResponse.model_validate(t) for t in (orch.tasks or [])]
    artifacts_resp = [ArtifactResponse.model_validate(a) for a in (orch.artifacts or [])]

    return OrchestrationResponse(
        id=orch.id,
        requested_by=orch.requested_by,
        goal=orch.goal,
        status=orch.status,
        graph_version=orch.graph_version,
        created_at=orch.created_at,
        started_at=orch.started_at,
        completed_at=orch.completed_at,
        failed_at=orch.failed_at,
        cancelled_at=orch.cancelled_at,
        result=orch.result,
        error_code=orch.error_code,
        error_message=orch.error_message,
        tasks=tasks_resp,
        artifacts=artifacts_resp,
        metadata=orch.metadata_json or {},
    )


@router.post(
    "",
    response_model=OrchestrationResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Submit a new user goal for multi-agent DAG orchestration",
)
async def create_orchestration(
    body: OrchestrationCreate,
    user: User = Depends(require_authenticated_user),
    db: AsyncSession = Depends(get_db),
) -> OrchestrationResponse:
    try:
        orch = await orchestration_engine.create_orchestration(
            db=db,
            user_id=user.id,
            goal=body.goal,
            tasks=body.tasks,
            metadata=body.metadata,
        )
        if not orch:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to initialize orchestration",
            )
        return _serialize_orchestration(orch)
    except DAGValidationError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(e),
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )


@router.get(
    "",
    response_model=OrchestrationListResponse,
    summary="List orchestrations for the authenticated user",
)
async def list_orchestrations(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    user: User = Depends(require_authenticated_user),
    db: AsyncSession = Depends(get_db),
) -> OrchestrationListResponse:
    is_admin = user.has_role(UserRole.ADMIN)

    count_query = select(func.count(Orchestration.id))
    items_query = (
        select(Orchestration)
        .options(
            selectinload(Orchestration.tasks),
            selectinload(Orchestration.artifacts),
            selectinload(Orchestration.checkpoints),
        )
        .order_by(Orchestration.created_at.desc())
        .limit(limit)
        .offset(offset)
    )

    if not is_admin:
        count_query = count_query.where(Orchestration.requested_by == user.id)
        items_query = items_query.where(Orchestration.requested_by == user.id)

    total_res = await db.execute(count_query)
    total = total_res.scalar_one()

    items_res = await db.execute(items_query)
    orchs = items_res.scalars().all()

    return OrchestrationListResponse(
        items=[_serialize_orchestration(o) for o in orchs],
        total=total,
    )


@router.get(
    "/{orchestration_id}",
    response_model=OrchestrationResponse,
    summary="Get details, DAG tasks, results, and artifacts for an orchestration",
)
async def get_orchestration(
    orchestration_id: uuid.UUID,
    user: User = Depends(require_authenticated_user),
    db: AsyncSession = Depends(get_db),
) -> OrchestrationResponse:
    orch = await orchestration_engine.get_orchestration(db, orchestration_id)
    if not orch:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Orchestration '{orchestration_id}' not found",
        )

    # Authorization check
    is_requester = orch.requested_by == user.id
    is_admin = user.has_role(UserRole.ADMIN)
    if not (is_requester or is_admin):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You are not authorized to view this orchestration",
        )

    return _serialize_orchestration(orch)


@router.post(
    "/{orchestration_id}/start",
    response_model=OrchestrationResponse,
    summary="Start execution of an orchestration",
)
async def start_orchestration(
    orchestration_id: uuid.UUID,
    user: User = Depends(require_authenticated_user),
    db: AsyncSession = Depends(get_db),
) -> OrchestrationResponse:
    orch = await orchestration_engine.get_orchestration(db, orchestration_id)
    if not orch:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Orchestration '{orchestration_id}' not found",
        )

    is_requester = orch.requested_by == user.id
    is_admin = user.has_role(UserRole.ADMIN)
    if not (is_requester or is_admin):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You are not authorized to start this orchestration",
        )

    try:
        orch = await orchestration_engine.start_orchestration(db, orchestration_id, user.id)

        # Trigger background execution loop
        async def _run_bg():
            try:
                await orchestration_engine.run_until_complete(orchestration_id, timeout_seconds=300)
            except Exception as err:
                logger.error(f"Background execution of orchestration {orchestration_id} failed: {err}")

        asyncio.create_task(_run_bg())

        return _serialize_orchestration(orch)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )


@router.post(
    "/{orchestration_id}/cancel",
    response_model=OrchestrationResponse,
    summary="Cancel an active orchestration and propagate to child tasks",
)
async def cancel_orchestration(
    orchestration_id: uuid.UUID,
    body: dict[str, str] | None = Body(default=None),
    user: User = Depends(require_authenticated_user),
    db: AsyncSession = Depends(get_db),
) -> OrchestrationResponse:
    reason = body.get("reason") if body else None
    try:
        orch = await orchestration_engine.cancel_orchestration(
            db=db,
            orchestration_id=orchestration_id,
            user_id=user.id,
            user_role=user.primary_role,
            reason=reason,
        )
        return _serialize_orchestration(orch)
    except PermissionError:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You are not authorized to cancel this orchestration",
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )


@router.get(
    "/{orchestration_id}/tasks",
    response_model=list[TaskResponse],
    summary="List all DAG tasks for an orchestration",
)
async def get_orchestration_tasks(
    orchestration_id: uuid.UUID,
    user: User = Depends(require_authenticated_user),
    db: AsyncSession = Depends(get_db),
) -> list[TaskResponse]:
    orch = await orchestration_engine.get_orchestration(db, orchestration_id)
    if not orch:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Orchestration '{orchestration_id}' not found",
        )

    is_requester = orch.requested_by == user.id
    is_admin = user.has_role(UserRole.ADMIN)
    if not (is_requester or is_admin):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You are not authorized to view tasks for this orchestration",
        )

    return [TaskResponse.model_validate(t) for t in (orch.tasks or [])]


@router.post(
    "/{orchestration_id}/sse-token",
    response_model=SSETokenResponse,
    summary="Generate a short-lived single-use SSE ticket for orchestration progress",
)
async def generate_orchestration_sse_token(
    orchestration_id: uuid.UUID,
    user: User = Depends(require_authenticated_user),
    db: AsyncSession = Depends(get_db),
) -> SSETokenResponse:
    orch = await orchestration_engine.get_orchestration(db, orchestration_id)
    if not orch:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Orchestration '{orchestration_id}' not found",
        )

    is_requester = orch.requested_by == user.id
    is_admin = user.has_role(UserRole.ADMIN)
    if not (is_requester or is_admin):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You are not authorized to subscribe to this orchestration",
        )

    token = await SSETicketService.create_orchestration_ticket(user.id, orchestration_id)
    stream_url = f"/api/v1/orchestrations/{orchestration_id}/events?sse_token={token}"
    return SSETokenResponse(token=token, expires_in=60, stream_url=stream_url)


@router.get(
    "/{orchestration_id}/events",
    summary="Server-Sent Events (SSE) stream for orchestration progress",
)
async def orchestration_events_stream(
    orchestration_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> StreamingResponse:
    user = await get_sse_authenticated_user_for_orch(
        orchestration_id=orchestration_id, request=request, db=db
    )

    orch = await orchestration_engine.get_orchestration(db, orchestration_id)
    if not orch:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Orchestration '{orchestration_id}' not found",
        )

    is_requester = orch.requested_by == user.id
    is_admin = user.has_role(UserRole.ADMIN)
    if not (is_requester or is_admin):
        metrics.inc_counter("sse_auth_failure_total")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You are not authorized to subscribe to this orchestration",
        )

    async def event_generator() -> AsyncGenerator[str, None]:
        # 1. Snapshot event
        initial_event = {
            "orchestration_id": str(orch.id),
            "event_type": "ORCHESTRATION_SNAPSHOT",
            "timestamp": utc_now().isoformat(),
            "sequence": 0,
            "payload": {
                "status": orch.status,
                "goal": orch.goal,
                "tasks_count": len(orch.tasks),
                "tasks": [
                    {
                        "task_key": t.task_key,
                        "status": t.status,
                        "attempt": t.attempt,
                        "execution_id": str(t.execution_id) if t.execution_id else None,
                    }
                    for t in orch.tasks
                ],
                "result": orch.result,
                "error_code": orch.error_code,
                "error_message": orch.error_message,
            },
        }
        yield f"event: orchestration_event\ndata: {json.dumps(initial_event)}\n\n"
        await asyncio.sleep(0.01)

        # Replay any buffered history
        redis = get_redis_client()
        history_key = f"agentchain:orchestration:{orchestration_id}:history"
        try:
            history_items = await redis.lrange(history_key, 0, -1)
            for item in history_items:
                raw_str = item.decode("utf-8") if isinstance(item, bytes) else item
                yield f"event: orchestration_event\ndata: {raw_str}\n\n"
                await asyncio.sleep(0.005)
        except Exception as err:
            logger.warning(f"Error fetching orchestration history: {err}")

        if orch.status in (
            OrchestrationStatus.SUCCEEDED.value,
            OrchestrationStatus.FAILED.value,
            OrchestrationStatus.CANCELLED.value,
        ):
            return

        # 2. Subscribe to Redis pubsub
        pubsub = redis.pubsub()
        channel = f"agentchain:orchestration:{orchestration_id}:events"
        await pubsub.subscribe(channel)

        try:
            while True:
                if await request.is_disconnected():
                    break

                msg = await pubsub.get_message(
                    ignore_subscribe_messages=True, timeout=1.0
                )
                if msg and msg.get("type") == "message":
                    data = msg.get("data", "")
                    data_str = data.decode("utf-8") if isinstance(data, bytes) else str(data)
                    yield f"event: orchestration_event\ndata: {data_str}\n\n"

                    try:
                        parsed = json.loads(data_str)
                        evt_type = parsed.get("event_type")
                        if evt_type in (
                            "ORCHESTRATION_SUCCEEDED",
                            "ORCHESTRATION_FAILED",
                            "ORCHESTRATION_CANCELLED",
                        ):
                            break
                    except Exception:
                        pass
                else:
                    # Keep-alive heartbeat comment
                    yield ": heartbeat\n\n"
                    await asyncio.sleep(1.0)
        finally:
            try:
                await pubsub.unsubscribe(channel)
                await pubsub.close()
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
