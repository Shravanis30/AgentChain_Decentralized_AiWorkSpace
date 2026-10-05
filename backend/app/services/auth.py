import hashlib
import logging
import secrets
from datetime import timedelta
import uuid
from typing import Any

from eth_account.messages import encode_defunct
from fastapi import HTTPException, Request, status
from siwe import SiweMessage
from siwe.siwe import ExpiredMessage, InvalidSignature, NotYetValidMessage
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from web3 import Web3

from app.core.config import settings
from app.models.audit import AuditEventType
from app.models.base import utc_now
from app.models.session import AuthNonce, Session
from app.models.user import User, UserRole, Wallet
from app.services.audit import AuditService

logger = logging.getLogger(__name__)
w3 = Web3()


class AuthService:
    @staticmethod
    def normalize_address(address: str) -> str:
        """Normalize address to EIP-55 checksum format."""
        try:
            return Web3.to_checksum_address(address)
        except Exception:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid Ethereum wallet address format",
            )

    @classmethod
    async def create_nonce(
        cls,
        db: AsyncSession,
        wallet_address: str,
        chain_id: int | None = None,
        request: Request | None = None,
    ) -> AuthNonce:
        """
        Generate a cryptographically secure nonce with expiration and store it server-side.
        """
        normalized_address = cls.normalize_address(wallet_address)
        # 32 characters alphanumeric nonce
        nonce_str = secrets.token_hex(16)
        expires_at = utc_now() + timedelta(minutes=settings.NONCE_EXPIRE_MINUTES)

        auth_nonce = AuthNonce(
            wallet_address=normalized_address,
            nonce=nonce_str,
            chain_id=chain_id,
            expires_at=expires_at,
        )
        db.add(auth_nonce)
        await db.flush()

        # Audit event
        await AuditService.record_event(
            db=db,
            event_type=AuditEventType.AUTH_NONCE_CREATED,
            wallet_address=normalized_address,
            request=request,
            metadata={"chain_id": chain_id, "expires_at": expires_at.isoformat()},
        )
        await db.commit()
        await db.refresh(auth_nonce)
        return auth_nonce

    @classmethod
    async def verify_siwe(
        cls,
        db: AsyncSession,
        message_str: str,
        signature: str,
        request: Request | None = None,
    ) -> tuple[str, Session, User, Wallet]:
        """
        Cryptographically verify SIWE message & signature, enforce nonce consumption,
        upsert User & Wallet, create secure session, and audit event.
        """
        # 1. Parse SIWE message
        try:
            siwe_msg = SiweMessage.from_message(message_str)
        except Exception as e:
            logger.warning(f"Failed to parse SIWE message: {e}")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Malformed SIWE message format",
            )

        wallet_address = cls.normalize_address(siwe_msg.address)

        # 2. Domain verification
        domain_valid = any(
            siwe_msg.domain == allowed or siwe_msg.domain.startswith(allowed.split(":")[0])
            for allowed in settings.SIWE_ALLOWED_DOMAINS
        )
        if not domain_valid:
            await AuditService.record_event(
                db=db,
                event_type=AuditEventType.AUTH_FAILURE,
                wallet_address=wallet_address,
                request=request,
                metadata={"reason": "domain_mismatch", "domain": siwe_msg.domain},
            )
            await db.commit()
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"SIWE domain mismatch: {siwe_msg.domain} is not authorized",
            )

        # 3. Chain ID verification
        if siwe_msg.chain_id not in settings.ALLOWED_CHAIN_IDS:
            await AuditService.record_event(
                db=db,
                event_type=AuditEventType.AUTH_FAILURE,
                wallet_address=wallet_address,
                request=request,
                metadata={"reason": "chain_id_unsupported", "chain_id": siwe_msg.chain_id},
            )
            await db.commit()
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Unsupported chain ID: {siwe_msg.chain_id}",
            )

        # 4. Cryptographic signature verification
        try:
            # First, verify through siwe package
            siwe_msg.verify(signature)
        except InvalidSignature:
            await AuditService.record_event(
                db=db,
                event_type=AuditEventType.AUTH_FAILURE,
                wallet_address=wallet_address,
                request=request,
                metadata={"reason": "invalid_signature"},
            )
            await db.commit()
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid cryptographic signature",
            )
        except (ExpiredMessage, NotYetValidMessage) as e:
            await AuditService.record_event(
                db=db,
                event_type=AuditEventType.AUTH_FAILURE,
                wallet_address=wallet_address,
                request=request,
                metadata={"reason": "message_time_invalid", "detail": str(e)},
            )
            await db.commit()
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="SIWE message has expired or is not yet valid",
            )
        except Exception as e:
            logger.warning(f"SIWE verification error: {e}")
            await AuditService.record_event(
                db=db,
                event_type=AuditEventType.AUTH_FAILURE,
                wallet_address=wallet_address,
                request=request,
                metadata={"reason": "verification_exception", "error": str(e)},
            )
            await db.commit()
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Signature verification failed",
            )

        # 5. Dual check: Recover signer from eth_account to ensure address match
        try:
            signable_hash = encode_defunct(text=message_str)
            recovered_address = w3.eth.account.recover_message(signable_hash, signature=signature)
            if cls.normalize_address(recovered_address) != wallet_address:
                raise ValueError("Recovered address does not match message address")
        except Exception as e:
            await AuditService.record_event(
                db=db,
                event_type=AuditEventType.AUTH_FAILURE,
                wallet_address=wallet_address,
                request=request,
                metadata={"reason": "address_recovery_mismatch", "error": str(e)},
            )
            await db.commit()
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Recovered address does not match claimed address",
            )

        # 6. Nonce validation & single-use consumption
        nonce_query = select(AuthNonce).where(
            AuthNonce.nonce == siwe_msg.nonce,
            AuthNonce.wallet_address == wallet_address,
        )
        nonce_res = await db.execute(nonce_query)
        auth_nonce = nonce_res.scalar_one_or_none()

        now = utc_now()
        if not auth_nonce:
            await AuditService.record_event(
                db=db,
                event_type=AuditEventType.AUTH_FAILURE,
                wallet_address=wallet_address,
                request=request,
                metadata={"reason": "nonce_not_found"},
            )
            await db.commit()
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Nonce not found or does not belong to this address",
            )

        if auth_nonce.consumed_at is not None:
            await AuditService.record_event(
                db=db,
                event_type=AuditEventType.AUTH_FAILURE,
                wallet_address=wallet_address,
                request=request,
                metadata={"reason": "nonce_already_consumed"},
            )
            await db.commit()
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Nonce has already been used (replay rejected)",
            )

        if auth_nonce.expires_at < now:
            await AuditService.record_event(
                db=db,
                event_type=AuditEventType.AUTH_FAILURE,
                wallet_address=wallet_address,
                request=request,
                metadata={"reason": "nonce_expired"},
            )
            await db.commit()
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Nonce has expired",
            )

        # Mark nonce consumed immediately
        auth_nonce.consumed_at = now

        # 7. Upsert User & Wallet
        wallet_query = (
            select(Wallet)
            .options(selectinload(Wallet.user).selectinload(User.wallets))
            .where(
                Wallet.address == wallet_address,
                Wallet.chain_id == siwe_msg.chain_id,
            )
        )
        wallet_res = await db.execute(wallet_query)
        wallet = wallet_res.scalar_one_or_none()

        if wallet:
            user = wallet.user
        else:
            # Check if user with that primary wallet address exists
            user_query = (
                select(User)
                .options(selectinload(User.wallets))
                .where(User.wallet_address == wallet_address)
            )
            user_res = await db.execute(user_query)
            user = user_res.scalar_one_or_none()

            if not user:
                user = User(
                    wallet_address=wallet_address,
                    primary_role=UserRole.CLIENT.value,
                    additional_roles=[],
                    legacy_role=UserRole.CLIENT.value,
                )
                db.add(user)
                await db.flush()
                has_primary = False
            else:
                has_primary = (
                    any(w.is_primary for w in user.wallets)
                    if "wallets" in user.__dict__ and user.wallets
                    else False
                )

            # Create wallet
            wallet = Wallet(
                user_id=user.id,
                address=wallet_address,
                chain_id=siwe_msg.chain_id,
                is_primary=not has_primary,
                verified_at=now,
            )
            db.add(wallet)
            await db.flush()

        # 8. Create Authenticated Session
        raw_token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
        expires_at = now + timedelta(days=settings.SESSION_EXPIRE_DAYS)

        session = Session(
            user_id=user.id,
            token_hash=token_hash,
            expires_at=expires_at,
            last_seen_at=now,
        )
        db.add(session)
        await db.flush()

        # 9. Audit Success
        await AuditService.record_event(
            db=db,
            event_type=AuditEventType.AUTH_SUCCESS,
            user_id=user.id,
            wallet_address=wallet_address,
            request=request,
            metadata={
                "session_id": str(session.id),
                "chain_id": siwe_msg.chain_id,
            },
        )
        await db.commit()

        # Eagerly reload models with relationships to avoid lazy load in async mode
        refreshed_user = (
            await db.execute(
                select(User).options(selectinload(User.wallets)).where(User.id == user.id)
            )
        ).scalar_one()
        refreshed_wallet = (
            await db.execute(select(Wallet).where(Wallet.id == wallet.id))
        ).scalar_one()
        refreshed_session = (
            await db.execute(select(Session).where(Session.id == session.id))
        ).scalar_one()

        return raw_token, refreshed_session, refreshed_user, refreshed_wallet


    @classmethod
    async def get_session_by_token(
        cls,
        db: AsyncSession,
        raw_token: str,
    ) -> Session | None:
        """
        Lookup session by raw token. Touches last_seen_at if valid.
        """
        token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
        now = utc_now()

        query = (
            select(Session)
            .options(selectinload(Session.user).selectinload(User.wallets))
            .where(
                Session.token_hash == token_hash,
                Session.revoked_at.is_(None),
                Session.expires_at > now,
            )
        )
        res = await db.execute(query)
        session = res.scalar_one_or_none()

        if session:
            # Throttle last_seen_at update to every 60 seconds
            if (now - session.last_seen_at).total_seconds() > 60:
                session.last_seen_at = now
                await db.commit()

        return session

    @classmethod
    async def logout(
        cls,
        db: AsyncSession,
        session: Session,
        request: Request | None = None,
    ) -> None:
        """
        Revoke active session and audit logout.
        """
        session.revoked_at = utc_now()
        await AuditService.record_event(
            db=db,
            event_type=AuditEventType.LOGOUT,
            user_id=session.user_id,
            request=request,
            metadata={"session_id": str(session.id)},
        )
        await db.commit()

    @classmethod
    async def link_wallet(
        cls,
        db: AsyncSession,
        user: User,
        message_str: str,
        signature: str,
        request: Request | None = None,
    ) -> Wallet:
        """
        Link an additional verified wallet to an existing user account.
        """
        try:
            siwe_msg = SiweMessage.from_message(message_str)
        except Exception:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Malformed SIWE message format",
            )

        wallet_address = cls.normalize_address(siwe_msg.address)

        # Verify signature
        try:
            siwe_msg.verify(signature)
        except Exception:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid signature for wallet linking proof",
            )

        # Verify nonce
        nonce_query = select(AuthNonce).where(
            AuthNonce.nonce == siwe_msg.nonce,
            AuthNonce.wallet_address == wallet_address,
        )
        nonce_res = await db.execute(nonce_query)
        auth_nonce = nonce_res.scalar_one_or_none()
        now = utc_now()

        if not auth_nonce or auth_nonce.consumed_at is not None or auth_nonce.expires_at < now:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid, expired, or consumed nonce",
            )

        auth_nonce.consumed_at = now

        # Ensure not already linked to another user
        existing_wallet_res = await db.execute(
            select(Wallet).where(
                Wallet.address == wallet_address,
                Wallet.chain_id == siwe_msg.chain_id,
            )
        )
        existing = existing_wallet_res.scalar_one_or_none()
        if existing:
            if existing.user_id != user.id:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Wallet is already linked to another user account",
                )
            return existing

        new_wallet = Wallet(
            user_id=user.id,
            address=wallet_address,
            chain_id=siwe_msg.chain_id,
            is_primary=False,
            verified_at=now,
        )
        db.add(new_wallet)
        await db.flush()

        await AuditService.record_event(
            db=db,
            event_type=AuditEventType.WALLET_ADDED,
            user_id=user.id,
            wallet_address=wallet_address,
            request=request,
            metadata={"chain_id": siwe_msg.chain_id},
        )
        await db.commit()
        return (
            await db.execute(select(Wallet).where(Wallet.id == new_wallet.id))
        ).scalar_one()

