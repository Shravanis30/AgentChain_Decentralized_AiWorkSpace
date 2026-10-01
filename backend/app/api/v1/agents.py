import math
import uuid
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    get_db,
    require_authenticated_user,
    require_role,
)
from app.models.agent import Agent, AgentStatus
from app.models.user import User, UserRole
from app.schemas.agent import (
    AgentCreateRequest,
    AgentExecutionRequest,
    AgentExecutionResponse,
    AgentListResponse,
    AgentResponse,
    AgentUpdateRequest,
    AgentVersionResponse,
    ExecutionSubmitResponse,
)
from app.services.agent_execution import AgentExecutionService
from app.services.agent_registry import AgentRegistryService

router = APIRouter(prefix="/agents", tags=["Agent Registry"])


def serialize_agent(agent: Agent) -> AgentResponse:
    current_ver = None
    if agent.current_version:
        current_ver = AgentVersionResponse(
            id=agent.current_version.id,
            version=agent.current_version.version,
            manifest=agent.current_version.manifest,
            input_schema=agent.current_version.input_schema,
            output_schema=agent.current_version.output_schema,
            runtime_config=agent.current_version.runtime_config,
            pricing_config=agent.current_version.pricing_config,
            created_at=agent.current_version.created_at,
            published_at=agent.current_version.published_at,
        )

    capabilities = [c.capability for c in (agent.capabilities or [])]

    return AgentResponse(
        id=agent.id,
        owner_user_id=agent.owner_user_id,
        name=agent.name,
        slug=agent.slug,
        description=agent.description,
        status=agent.status,
        current_version_id=agent.current_version_id,
        current_version=current_ver,
        capabilities=capabilities,
        created_at=agent.created_at,
        updated_at=agent.updated_at,
        published_at=agent.published_at,
    )


@router.post(
    "",
    response_model=AgentResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new Agent in DRAFT status",
)
async def create_agent(
    payload: AgentCreateRequest,
    request: Request,
    user: User = Depends(require_role(UserRole.DEVELOPER, UserRole.AGENT_OPERATOR)),
    db: AsyncSession = Depends(get_db),
) -> AgentResponse:
    agent = await AgentRegistryService.create_agent(
        db=db,
        owner_user_id=user.id,
        payload=payload,
        request=request,
    )
    return serialize_agent(agent)


@router.get(
    "",
    response_model=AgentListResponse,
    status_code=status.HTTP_200_OK,
    summary="Public discovery of agents with filtering and database pagination",
)
async def list_agents(
    capability: str | None = Query(default=None, description="Filter by capability tag"),
    status: str | None = Query(default=None, description="Filter by status (defaults to PUBLISHED)"),
    owner_id: uuid.UUID | None = Query(default=None, description="Filter by owner user ID"),
    search: str | None = Query(default=None, description="Search term for name or description"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
) -> AgentListResponse:
    agents, total = await AgentRegistryService.list_agents(
        db=db,
        capability=capability,
        status_filter=status,
        owner_id=owner_id,
        search=search,
        page=page,
        page_size=page_size,
    )

    total_pages = math.ceil(total / page_size) if total > 0 else 1
    return AgentListResponse(
        items=[serialize_agent(a) for a in agents],
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
    )


@router.get(
    "/{agent_id}",
    response_model=AgentResponse,
    status_code=status.HTTP_200_OK,
    summary="Get details of a specific agent",
)
async def get_agent(
    agent_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> AgentResponse:
    agent = await AgentRegistryService.get_agent_by_id(db, agent_id)
    if not agent:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent '{agent_id}' not found",
        )
    return serialize_agent(agent)


@router.patch(
    "/{agent_id}",
    response_model=AgentResponse,
    status_code=status.HTTP_200_OK,
    summary="Update agent metadata or draft manifest (owner only)",
)
async def update_agent(
    agent_id: uuid.UUID,
    payload: AgentUpdateRequest,
    request: Request,
    user: User = Depends(require_authenticated_user),
    db: AsyncSession = Depends(get_db),
) -> AgentResponse:
    agent = await AgentRegistryService.get_agent_by_id(db, agent_id)
    if not agent:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")

    # Enforce ownership
    if agent.owner_user_id != user.id and not user.has_role(UserRole.ADMIN):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have permission to modify this agent",
        )

    updated = await AgentRegistryService.update_agent(db, agent, payload, request)
    return serialize_agent(updated)


@router.delete(
    "/{agent_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete an agent (owner only, draft/deprecated only)",
)
async def delete_agent(
    agent_id: uuid.UUID,
    request: Request,
    user: User = Depends(require_authenticated_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    agent = await AgentRegistryService.get_agent_by_id(db, agent_id)
    if not agent:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")

    if agent.owner_user_id != user.id and not user.has_role(UserRole.ADMIN):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have permission to delete this agent",
        )

    await AgentRegistryService.delete_agent(db, agent, request)


@router.post(
    "/{agent_id}/validate",
    response_model=AgentResponse,
    status_code=status.HTTP_200_OK,
    summary="Trigger validation of agent manifest",
)
async def validate_agent_lifecycle(
    agent_id: uuid.UUID,
    request: Request,
    user: User = Depends(require_authenticated_user),
    db: AsyncSession = Depends(get_db),
) -> AgentResponse:
    agent = await AgentRegistryService.get_agent_by_id(db, agent_id)
    if not agent:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")

    if agent.owner_user_id != user.id and not user.has_role(UserRole.ADMIN):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have permission to validate this agent",
        )

    validated = await AgentRegistryService.validate_agent(db, agent, request)
    return serialize_agent(validated)


@router.post(
    "/{agent_id}/publish",
    response_model=AgentResponse,
    status_code=status.HTTP_200_OK,
    summary="Publish agent to make it discoverable and executable",
)
async def publish_agent_lifecycle(
    agent_id: uuid.UUID,
    request: Request,
    user: User = Depends(require_authenticated_user),
    db: AsyncSession = Depends(get_db),
) -> AgentResponse:
    agent = await AgentRegistryService.get_agent_by_id(db, agent_id)
    if not agent:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")

    if agent.owner_user_id != user.id and not user.has_role(UserRole.ADMIN):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have permission to publish this agent",
        )

    published = await AgentRegistryService.publish_agent(db, agent, request)
    return serialize_agent(published)


@router.post(
    "/{agent_id}/suspend",
    response_model=AgentResponse,
    status_code=status.HTTP_200_OK,
    summary="Suspend a published agent (admin or owner)",
)
async def suspend_agent_lifecycle(
    agent_id: uuid.UUID,
    request: Request,
    reason: str | None = Query(default=None),
    user: User = Depends(require_authenticated_user),
    db: AsyncSession = Depends(get_db),
) -> AgentResponse:
    agent = await AgentRegistryService.get_agent_by_id(db, agent_id)
    if not agent:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")

    if agent.owner_user_id != user.id and not user.has_role(UserRole.ADMIN):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have permission to suspend this agent",
        )

    suspended = await AgentRegistryService.suspend_agent(db, agent, reason, request)
    return serialize_agent(suspended)


@router.post(
    "/{agent_id}/execute",
    response_model=ExecutionSubmitResponse,
    status_code=status.HTTP_200_OK,
    summary="Invoke and execute a published agent",
)
async def execute_agent(
    agent_id: uuid.UUID,
    payload: AgentExecutionRequest,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    user: User = Depends(require_authenticated_user),
    db: AsyncSession = Depends(get_db),
) -> ExecutionSubmitResponse:
    execution = await AgentExecutionService.enqueue_agent_execution(
        db=db,
        agent_id=agent_id,
        requested_by_user_id=user.id,
        input_data=payload.input,
        idempotency_key=idempotency_key,
        request=request,
    )

    return ExecutionSubmitResponse(
        execution_id=execution.id,
        status=execution.status,
        input_hash=execution.input_hash,
        agent_id=execution.agent_id,
        agent_version=execution.version.version,
    )
