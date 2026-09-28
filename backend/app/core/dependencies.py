from collections.abc import Callable
from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.session import get_db
from app.models.session import Session
from app.models.user import User, UserRole, Wallet
from app.services.auth import AuthService


async def get_session_token_from_request(request: Request) -> str | None:
    """Extract session token from Authorization header or cookie (Bearer header takes precedence)."""
    auth_header = request.headers.get("Authorization")
    if auth_header and auth_header.startswith("Bearer "):
        return auth_header.split(" ", 1)[1].strip()

    cookie_token = request.cookies.get(settings.SESSION_COOKIE_NAME)
    if cookie_token:
        return cookie_token

    return None


async def get_current_session(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> Session:
    """
    Validate session token and return the active Session.
    Raises HTTP 401 if missing, expired, or revoked.
    """
    token = await get_session_token_from_request(request)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )

    session = await AuthService.get_session_by_token(db, token)
    if not session:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired session",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return session


async def require_authenticated_user(
    session: Session = Depends(get_current_session),
) -> User:
    """
    Ensure the request is authenticated and return the current User.
    """
    if not session.user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found for active session",
        )
    return session.user


def require_role(*allowed_roles: UserRole) -> Callable[[User], User]:
    """
    Factory creating a dependency that enforces the user possesses at least one of the allowed roles.
    ADMIN role always bypasses role checks.
    """
    async def role_checker(
        user: User = Depends(require_authenticated_user),
    ) -> User:
        if not any(user.has_role(role) for role in allowed_roles):
            allowed_role_names = [r.value for r in allowed_roles]
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Operation not permitted. Required roles: {allowed_role_names}",
            )
        return user

    return role_checker


async def require_admin_user(
    user: User = Depends(require_authenticated_user),
) -> User:
    """Ensure the current user has ADMIN role.

    Used for privileged operations such as admin-triggered reputation
    recalculation.  Even admin users cannot set scores directly — they
    only trigger deterministic recomputation from authoritative evidence.
    """
    if not user.has_role(UserRole.ADMIN):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin role required for this operation",
        )
    return user


async def require_wallet_verified(
    user: User = Depends(require_authenticated_user),
) -> Wallet:
    """
    Ensure the user has at least one verified wallet.
    Returns the primary wallet.
    """
    wallet = user.primary_wallet
    if not wallet or not wallet.verified_at:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Verified wallet required for this operation",
        )
    return wallet
