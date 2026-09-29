"""Blockchain transaction outbox dispatcher.

Guarantees:
- At-least-once delivery from PostgreSQL to Redis Streams.
- Retries with bounded exponential backoff on Redis outages.
- Dead-letter handling after max_attempts reached.
- Stream name: `agentchain:blockchain:tx_stream`.
"""

from datetime import timedelta
import json
import logging
from typing import Any
import redis.asyncio as aioredis
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.base import utc_now
from app.models.blockchain import BlockchainTxOutbox, BlockchainTransactionIntent, IntentStatus

logger = logging.getLogger(__name__)

TX_STREAM_KEY = "agentchain:blockchain:tx_stream"


class BlockchainTxOutboxDispatcher:
    """Dispatches persisted outbox entries to Redis Stream."""

    def __init__(self, redis_client: aioredis.Redis, stream_name: str = TX_STREAM_KEY) -> None:
        self.redis = redis_client
        self.stream_name = stream_name

    async def dispatch_pending(self, session: AsyncSession, batch_size: int = 50) -> int:
        """Finds due outbox items and publishes them to Redis Stream."""
        now = utc_now()
        stmt = (
            sa.select(BlockchainTxOutbox)
            .where(
                BlockchainTxOutbox.status == "PENDING",
                BlockchainTxOutbox.next_attempt_at <= now,
            )
            .order_by(BlockchainTxOutbox.next_attempt_at.asc())
            .limit(batch_size)
            .with_for_update(skip_locked=True)
        )
        items = (await session.execute(stmt)).scalars().all()
        dispatched_count = 0

        for item in items:
            item.attempts += 1
            try:
                # Publish payload to Redis Stream
                stream_payload = {
                    "outbox_id": str(item.id),
                    "intent_id": str(item.intent_id),
                    "chain_id": str(item.chain_id),
                    "idempotency_key": item.idempotency_key,
                    "payload": json.dumps(item.payload),
                    "timestamp": now.isoformat(),
                }
                await self.redis.xadd(self.stream_name, stream_payload)

                item.status = "DISPATCHED"
                item.dispatched_at = now
                item.updated_at = now

                # Also advance intent status to QUEUED
                intent_stmt = sa.select(BlockchainTransactionIntent).where(
                    BlockchainTransactionIntent.id == item.intent_id
                )
                intent = (await session.execute(intent_stmt)).scalar_one_or_none()
                if intent and intent.status == IntentStatus.CREATED:
                    intent.status = IntentStatus.QUEUED
                    intent.updated_at = now

                dispatched_count += 1
                logger.debug("Dispatched outbox item %s for intent %s", item.id, item.intent_id)

            except Exception as exc:
                logger.warning(
                    "Failed to dispatch outbox item %s (attempt %d/%d): %s",
                    item.id,
                    item.attempts,
                    item.max_attempts,
                    exc,
                )
                item.error_message = str(exc)
                item.updated_at = now

                if item.attempts >= item.max_attempts:
                    item.status = "FAILED"
                    # Mark intent as FAILED
                    intent_stmt = sa.select(BlockchainTransactionIntent).where(
                        BlockchainTransactionIntent.id == item.intent_id
                    )
                    intent = (await session.execute(intent_stmt)).scalar_one_or_none()
                    if intent:
                        intent.status = IntentStatus.FAILED
                        intent.error_message = f"Outbox dispatch exhausted {item.max_attempts} attempts: {exc}"
                        intent.updated_at = now
                else:
                    delay_secs = item.backoff_seconds * (2 ** (item.attempts - 1))
                    item.next_attempt_at = now + timedelta(seconds=delay_secs)

        await session.flush()
        return dispatched_count
