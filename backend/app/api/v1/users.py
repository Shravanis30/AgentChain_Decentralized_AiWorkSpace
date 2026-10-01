from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db, require_authenticated_user, require_role
from app.models.user import User, UserRole
from app.schemas.auth import WalletLinkRequest
from app.schemas.user import UserProfileResponse, WalletResponse
from app.services.auth import AuthService

router = APIRouter(prefix="/users", tags=["Users"])


@router.get(
    "/me",
    response_model=UserProfileResponse,
    status_code=status.HTTP_200_OK,
    summary="Get detailed profile for current authenticated user",
)
async def get_user_profile(
    user: User = Depends(require_authenticated_user),
) -> UserProfileResponse:
    return UserProfileResponse(
        id=user.id,
        primary_role=user.primary_role,
        roles=[r.value for r in user.roles],
        wallets=[WalletResponse.model_validate(w) for w in user.wallets],
        created_at=user.created_at,
        updated_at=user.updated_at,
    )


@router.get(
    "/me/wallets",
    response_model=list[WalletResponse],
    status_code=status.HTTP_200_OK,
    summary="List all verified wallets linked to current user",
)
async def get_user_wallets(
    user: User = Depends(require_authenticated_user),
) -> list[WalletResponse]:
    return [WalletResponse.model_validate(w) for w in user.wallets]


@router.post(
    "/me/wallets",
    response_model=WalletResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Link a new verified wallet to current user using SIWE proof",
)
async def link_new_wallet(
    payload: WalletLinkRequest,
    request: Request,
    user: User = Depends(require_authenticated_user),
    db: AsyncSession = Depends(get_db),
) -> WalletResponse:
    wallet = await AuthService.link_wallet(
        db=db,
        user=user,
        message_str=payload.message,
        signature=payload.signature,
        request=request,
    )
    return WalletResponse.model_validate(wallet)


@router.get(
    "/admin/audit-logs",
    status_code=status.HTTP_200_OK,
    summary="Admin only endpoint to verify RBAC protection",
)
async def get_admin_audit_logs(
    user: User = Depends(require_role(UserRole.ADMIN)),
) -> dict[str, str]:
    return {"message": "Admin authorization granted", "user_id": str(user.id)}


@router.get(
    "/operator/status",
    status_code=status.HTTP_200_OK,
    summary="Agent operator endpoint to verify RBAC protection",
)
async def get_operator_status(
    user: User = Depends(require_role(UserRole.AGENT_OPERATOR)),
) -> dict[str, str]:
    return {"message": "Operator authorization granted", "user_id": str(user.id)}

