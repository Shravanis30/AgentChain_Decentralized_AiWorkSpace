import logging
import uuid
from typing import Any

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit import AuditEventType, AuditLog

logger = logging.getLogger(__name__)


class AuditService:
    @staticmethod
    async def record_event(
        db: AsyncSession,
        event_type: AuditEventType | str,
        user_id: uuid.UUID | None = None,
        wallet_address: str | None = None,
        request: Request | None = None,
        request_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> AuditLog:
        """
        Record an immutable audit event in the database.
        Never logs sensitive secrets, signatures, or private keys.
        """
        ip_address: str | None = None
        user_agent: str | None = None

        if request is not None:
            if not request_id:
                request_id = request.headers.get("x-request-id")
            # Extract real IP considering reverse proxies
            forwarded = request.headers.get("x-forwarded-for")
            if forwarded:
                ip_address = forwarded.split(",")[0].strip()
            elif request.client:
                ip_address = request.client.host
            user_agent = request.headers.get("user-agent")

        # Sanitize metadata
        clean_metadata: dict[str, Any] = {}
        if metadata:
            for k, v in metadata.items():
                # Filter out sensitive fields
                if any(secret_term in k.lower() for secret_term in ["secret", "password", "private_key", "signature", "token"]):
                    continue
                clean_metadata[k] = v

        event_str = event_type.value if isinstance(event_type, AuditEventType) else str(event_type)

        audit_entry = AuditLog(
            user_id=user_id,
            wallet_address=wallet_address,
            event_type=event_str,
            request_id=request_id,
            ip_address=ip_address,
            user_agent=user_agent[:256] if user_agent else None,
            metadata_json=clean_metadata,
        )

        db.add(audit_entry)
        try:
            await db.flush()
        except Exception as e:
            logger.error(f"Failed to record audit event {event_str}: {e}")
            # Audit recording should not necessarily crash request in non-critical scenarios,
            # but in flush it's queued in current transaction.

        return audit_entry
