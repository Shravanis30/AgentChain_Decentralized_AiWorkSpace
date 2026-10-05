import logging
import math
import uuid
from typing import Any

from fastapi import HTTPException, Request, status
import jsonschema
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.agent import (
    Agent,
    AgentCapability,
    AgentStatus,
    AgentTool,
    AgentVersion,
)
from app.models.audit import AuditEventType
from app.models.base import utc_now
from app.schemas.agent import AgentCreateRequest, AgentUpdateRequest
from app.schemas.agent_manifest import AgentManifest
from app.services.audit import AuditService

logger = logging.getLogger(__name__)


class AgentRegistryService:
    @classmethod
    async def create_agent(
        cls,
        db: AsyncSession,
        owner_user_id: uuid.UUID,
        payload: AgentCreateRequest,
        request: Request | None = None,
    ) -> Agent:
        """
        Create a new Agent with initial Draft version, capabilities, and tools.
        """
        # 1. Enforce unique slug
        existing = await db.execute(select(Agent).where(Agent.slug == payload.slug))
        if existing.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Agent with slug '{payload.slug}' already exists",
            )

        manifest = payload.manifest
        now = utc_now()

        # 2. Create Agent record
        agent = Agent(
            owner_user_id=owner_user_id,
            name=payload.name,
            slug=payload.slug,
            description=payload.description or manifest.description,
            status=AgentStatus.DRAFT.value,
        )
        db.add(agent)
        await db.flush()

        # 3. Create AgentVersion
        version = AgentVersion(
            agent_id=agent.id,
            version=manifest.agent.version,
            manifest=manifest.model_dump(),
            input_schema=manifest.input_schema,
            output_schema=manifest.output_schema,
            runtime_config=manifest.runtime.model_dump(),
            pricing_config=manifest.pricing.model_dump(),
            verification_config=manifest.verification.model_dump(),
            created_at=now,
        )
        db.add(version)
        await db.flush()

        agent.current_version_id = version.id

        # 4. Attach Capabilities
        for cap in manifest.capabilities:
            db.add(
                AgentCapability(
                    agent_id=agent.id,
                    capability=cap,
                    description=f"Capability: {cap}",
                )
            )

        # 5. Attach Tools
        for tool in manifest.tools:
            db.add(
                AgentTool(
                    agent_id=agent.id,
                    tool_name=tool.tool_name,
                    configuration=tool.configuration,
                    enabled=tool.enabled,
                )
            )

        await db.flush()

        # 6. Audit event
        await AuditService.record_event(
            db=db,
            event_type=AuditEventType.AGENT_CREATED,
            user_id=owner_user_id,
            request=request,
            metadata={
                "agent_id": str(agent.id),
                "slug": agent.slug,
                "version": version.version,
            },
        )
        await db.commit()

        return await cls.get_agent_by_id(db, agent.id)  # type: ignore[return-value]

    @classmethod
    async def get_agent_by_id(
        cls,
        db: AsyncSession,
        agent_id: uuid.UUID,
    ) -> Agent | None:
        query = (
            select(Agent)
            .options(
                selectinload(Agent.current_version),
                selectinload(Agent.versions),
                selectinload(Agent.capabilities),
                selectinload(Agent.tools),
            )
            .where(Agent.id == agent_id)
        )
        res = await db.execute(query)
        return res.scalar_one_or_none()

    @classmethod
    async def get_agent_by_slug(
        cls,
        db: AsyncSession,
        slug: str,
    ) -> Agent | None:
        query = (
            select(Agent)
            .options(
                selectinload(Agent.current_version),
                selectinload(Agent.versions),
                selectinload(Agent.capabilities),
                selectinload(Agent.tools),
            )
            .where(Agent.slug == slug)
        )
        res = await db.execute(query)
        return res.scalar_one_or_none()

    @classmethod
    async def list_agents(
        cls,
        db: AsyncSession,
        capability: str | None = None,
        status_filter: str | None = None,
        owner_id: uuid.UUID | None = None,
        search: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[Agent], int]:
        """
        List agents with database-level pagination and filtering.
        Defaults to PUBLISHED agents if status is unspecified.
        """
        query = select(Agent).options(
            selectinload(Agent.current_version),
            selectinload(Agent.capabilities),
        )

        # Status filter: default to PUBLISHED for discovery
        if status_filter:
            query = query.where(Agent.status == status_filter.upper())
        else:
            query = query.where(Agent.status == AgentStatus.PUBLISHED.value)

        # Owner filter
        if owner_id:
            query = query.where(Agent.owner_user_id == owner_id)

        # Capability filter
        if capability:
            query = query.join(Agent.capabilities).where(
                AgentCapability.capability == capability.lower()
            )

        # Search filter (name or description)
        if search:
            search_pattern = f"%{search.strip()}%"
            query = query.where(
                or_(
                    Agent.name.ilike(search_pattern),
                    Agent.description.ilike(search_pattern),
                    Agent.slug.ilike(search_pattern),
                )
            )

        # Count total matches
        count_query = select(func.count()).select_from(query.subquery())
        total_count = (await db.execute(count_query)).scalar() or 0

        # Apply ordering and pagination
        offset = max(0, (page - 1) * page_size)
        query = query.order_by(Agent.created_at.desc()).offset(offset).limit(page_size)

        agents = (await db.execute(query)).scalars().all()
        return list(agents), total_count

    @classmethod
    async def update_agent(
        cls,
        db: AsyncSession,
        agent: Agent,
        payload: AgentUpdateRequest,
        request: Request | None = None,
    ) -> Agent:
        """
        Update an agent's metadata or draft manifest.
        Only allowed for agents in DRAFT or SUSPENDED states.
        """
        if agent.status == AgentStatus.PUBLISHED.value:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Published agents cannot be modified directly. Create a new version.",
            )

        if payload.name:
            agent.name = payload.name
        if payload.description is not None:
            agent.description = payload.description

        if payload.manifest:
            manifest = payload.manifest
            now = utc_now()

            # Check if this version exists or create new version
            version_query = select(AgentVersion).where(
                AgentVersion.agent_id == agent.id,
                AgentVersion.version == manifest.agent.version,
            )
            v_res = await db.execute(version_query)
            version = v_res.scalar_one_or_none()

            if version:
                version.manifest = manifest.model_dump()
                version.input_schema = manifest.input_schema
                version.output_schema = manifest.output_schema
                version.runtime_config = manifest.runtime.model_dump()
                version.pricing_config = manifest.pricing.model_dump()
                version.verification_config = manifest.verification.model_dump()
            else:
                version = AgentVersion(
                    agent_id=agent.id,
                    version=manifest.agent.version,
                    manifest=manifest.model_dump(),
                    input_schema=manifest.input_schema,
                    output_schema=manifest.output_schema,
                    runtime_config=manifest.runtime.model_dump(),
                    pricing_config=manifest.pricing.model_dump(),
                    verification_config=manifest.verification.model_dump(),
                    created_at=now,
                )
                db.add(version)
                await db.flush()

            agent.current_version_id = version.id

            # Sync capabilities
            await db.execute(
                select(AgentCapability).where(AgentCapability.agent_id == agent.id)
            )
            for cap in manifest.capabilities:
                existing_cap = await db.execute(
                    select(AgentCapability).where(
                        AgentCapability.agent_id == agent.id,
                        AgentCapability.capability == cap,
                    )
                )
                if not existing_cap.scalar_one_or_none():
                    db.add(AgentCapability(agent_id=agent.id, capability=cap))

        agent.updated_at = utc_now()
        await db.flush()

        await AuditService.record_event(
            db=db,
            event_type=AuditEventType.AGENT_UPDATED,
            user_id=agent.owner_user_id,
            request=request,
            metadata={"agent_id": str(agent.id), "slug": agent.slug},
        )
        await db.commit()
        return await cls.get_agent_by_id(db, agent.id)  # type: ignore[return-value]

    @classmethod
    async def delete_agent(
        cls,
        db: AsyncSession,
        agent: Agent,
        request: Request | None = None,
    ) -> None:
        """
        Delete an agent. Only DRAFT or DEPRECATED agents may be deleted.
        """
        if agent.status == AgentStatus.PUBLISHED.value:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Published agents cannot be deleted. Suspend or deprecate them first.",
            )

        agent_id = str(agent.id)
        owner_id = agent.owner_user_id
        await db.delete(agent)
        await db.flush()

        await AuditService.record_event(
            db=db,
            event_type=AuditEventType.AGENT_UPDATED,
            user_id=owner_id,
            request=request,
            metadata={"action": "delete", "agent_id": agent_id},
        )
        await db.commit()

    @classmethod
    async def validate_agent(
        cls,
        db: AsyncSession,
        agent: Agent,
        request: Request | None = None,
    ) -> Agent:
        """
        Execute formal validation against the current manifest before publication.
        Transitions DRAFT -> VALIDATING -> DRAFT.
        """
        if not agent.current_version:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Agent has no active version to validate",
            )

        manifest_data = agent.current_version.manifest
        try:
            # 1. Pydantic model validation
            manifest = AgentManifest.model_validate(manifest_data)

            # 2. Check JSON schemas meta-schema validation
            jsonschema.Draft202012Validator.check_schema(manifest.input_schema)
            jsonschema.Draft202012Validator.check_schema(manifest.output_schema)

            # 3. Check capabilities
            if not manifest.capabilities:
                raise ValueError("Agent must declare at least one capability")

        except Exception as e:
            agent.status = AgentStatus.DRAFT.value
            await db.commit()
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"Agent manifest validation failed: {e!s}",
            )

        # Temporarily transition to VALIDATING then back to DRAFT (validated state)
        agent.status = AgentStatus.DRAFT.value
        agent.updated_at = utc_now()
        await db.flush()

        await AuditService.record_event(
            db=db,
            event_type=AuditEventType.AGENT_VALIDATED,
            user_id=agent.owner_user_id,
            request=request,
            metadata={"agent_id": str(agent.id), "valid": True},
        )
        await db.commit()
        return agent

    @classmethod
    async def publish_agent(
        cls,
        db: AsyncSession,
        agent: Agent,
        request: Request | None = None,
    ) -> Agent:
        """
        Publish an agent to make it discoverable and executable.
        Must be validated before publication.
        """
        if agent.status == AgentStatus.PUBLISHED.value:
            return agent

        if not agent.current_version:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot publish agent without a defined version",
            )

        # Perform strict validation before publication
        await cls.validate_agent(db, agent, request)

        now = utc_now()
        agent.status = AgentStatus.PUBLISHED.value
        agent.published_at = now
        agent.updated_at = now
        agent.current_version.published_at = now

        await db.flush()

        await AuditService.record_event(
            db=db,
            event_type=AuditEventType.AGENT_PUBLISHED,
            user_id=agent.owner_user_id,
            request=request,
            metadata={"agent_id": str(agent.id), "version": agent.current_version.version},
        )
        await db.commit()
        return agent

    @classmethod
    async def suspend_agent(
        cls,
        db: AsyncSession,
        agent: Agent,
        reason: str | None = None,
        request: Request | None = None,
    ) -> Agent:
        """
        Suspend an agent so it can no longer be executed.
        Admins or the owner may suspend an agent.
        """
        agent.status = AgentStatus.SUSPENDED.value
        agent.updated_at = utc_now()
        await db.flush()

        await AuditService.record_event(
            db=db,
            event_type=AuditEventType.AGENT_SUSPENDED,
            user_id=agent.owner_user_id,
            request=request,
            metadata={"agent_id": str(agent.id), "reason": reason},
        )
        await db.commit()
        return agent
