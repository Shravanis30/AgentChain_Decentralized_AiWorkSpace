from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_session, get_db, require_authenticated_user
from app.core.config import settings
from app.models.session import Session
from app.models.user import User
from app.schemas.auth import (
    LogoutResponse,
    NonceRequest,
    NonceResponse,
    VerifyRequest,
    VerifyResponse,
)
from app.schemas.user import UserResponse, WalletResponse
from app.services.auth import AuthService
from app.services.rate_limiter import RateLimiter

router = APIRouter(prefix="/auth", tags=["Authentication"])


@router.post(
    "/nonce",
    response_model=NonceResponse,
    status_code=status.HTTP_200_OK,
    summary="Request a cryptographic SIWE nonce",
)
async def request_nonce(
    payload: NonceRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> NonceResponse:
    # Rate limit nonce requests
    await RateLimiter.enforce(
        request=request,
        key_prefix="auth_nonce",
        limit=settings.RATE_LIMIT_NONCE_LIMIT,
        window_seconds=settings.RATE_LIMIT_WINDOW_SECONDS,
    )

    auth_nonce = await AuthService.create_nonce(
        db=db,
        wallet_address=payload.wallet_address,
        chain_id=payload.chain_id,
        request=request,
    )

    return NonceResponse(
        nonce=auth_nonce.nonce,
        wallet_address=auth_nonce.wallet_address,
        expires_at=auth_nonce.expires_at,
        chain_id=auth_nonce.chain_id,
    )


@router.post(
    "/verify",
    response_model=VerifyResponse,
    status_code=status.HTTP_200_OK,
    summary="Verify SIWE signature and create authenticated session",
)
async def verify_signature(
    payload: VerifyRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
) -> VerifyResponse:
    # Rate limit verify requests
    await RateLimiter.enforce(
        request=request,
        key_prefix="auth_verify",
        limit=settings.RATE_LIMIT_VERIFY_LIMIT,
        window_seconds=settings.RATE_LIMIT_WINDOW_SECONDS,
    )

    raw_token, session, user, wallet = await AuthService.verify_siwe(
        db=db,
        message_str=payload.message,
        signature=payload.signature,
        request=request,
    )

    # Set secure HttpOnly session cookie
    response.set_cookie(
        key=settings.SESSION_COOKIE_NAME,
        value=raw_token,
        httponly=True,
        secure=settings.SESSION_COOKIE_SECURE,
        samesite=settings.SESSION_COOKIE_SAMESITE,
        domain=settings.SESSION_COOKIE_DOMAIN,
        expires=session.expires_at,
        path="/",
    )

    wallet_resp = WalletResponse.model_validate(wallet)
    user_resp = UserResponse(
        id=user.id,
        primary_role=user.primary_role,
        roles=[r.value for r in user.roles],
        primary_wallet=wallet_resp if wallet.is_primary else (
            WalletResponse.model_validate(user.primary_wallet) if user.primary_wallet else wallet_resp
        ),
        created_at=user.created_at,
    )

    return VerifyResponse(
        authenticated=True,
        user=user_resp,
        wallet=wallet_resp,
        session_expires_at=session.expires_at,
        token=raw_token,
    )


@router.post(
    "/logout",
    response_model=LogoutResponse,
    status_code=status.HTTP_200_OK,
    summary="Revoke active session and clear cookie",
)
async def logout(
    request: Request,
    response: Response,
    session: Session = Depends(get_current_session),
    db: AsyncSession = Depends(get_db),
) -> LogoutResponse:
    await AuthService.logout(db=db, session=session, request=request)

    response.delete_cookie(
        key=settings.SESSION_COOKIE_NAME,
        path="/",
        domain=settings.SESSION_COOKIE_DOMAIN,
    )

    return LogoutResponse(
        success=True,
        message="Logged out successfully",
    )


@router.get(
    "/me",
    response_model=UserResponse,
    status_code=status.HTTP_200_OK,
    summary="Get current authenticated user info",
)
async def get_auth_me(
    user: User = Depends(require_authenticated_user),
) -> UserResponse:
    primary_w = user.primary_wallet
    return UserResponse(
        id=user.id,
        primary_role=user.primary_role,
        roles=[r.value for r in user.roles],
        primary_wallet=WalletResponse.model_validate(primary_w) if primary_w else None,
        created_at=user.created_at,
    )
