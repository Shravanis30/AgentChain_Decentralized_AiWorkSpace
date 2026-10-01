from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.schemas.health import HealthResponse
from app.services.health import HealthService

router = APIRouter(prefix="/health", tags=["Health"])


@router.get("", response_model=HealthResponse)
async def check_health(
    db: Annotated[AsyncSession, Depends(get_db)],
) -> HealthResponse:
    """
    Returns real-time health and connectivity status of all core infrastructure dependencies.
    """
    return await HealthService.get_health_status(db)
