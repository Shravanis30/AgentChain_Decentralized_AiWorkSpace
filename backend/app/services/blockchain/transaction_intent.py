"""Idempotent blockchain transaction intent service with atomic outbox integration.

Enforces:
- Operation allowlist (only approved Escrow contract functions).
- Contract allowlist per chain.
- Strict deduplication via (chain_id, idempotency_key).
- Atomic persistence alongside the outbox queue entry within the caller's DB transaction.
"""

import logging
from typing import Any
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.base import utc_now
from app.models.blockchain import (
    BlockchainTransactionIntent,
    BlockchainTxOutbox,
    IntentStatus,
)
from app.services.blockchain.config import ALLOWED_OPERATIONS, get_chain_config
from app.services.blockchain.errors import (
    ContractMismatchError,
    UnauthorizedOperationError,
)

logger = logging.getLogger(__name__)


class TransactionIntentService:
    """Creates and manages idempotent blockchain transaction intents."""

    @staticmethod
    async def create_intent(
        session: AsyncSession,
        chain_id: int,
        idempotency_key: str,
        target_contract: str,
        operation: str,
        parameters: dict[str, Any],
    ) -> tuple[BlockchainTransactionIntent, bool]:
        """Creates an intent and enqueues it to the outbox atomically.
        
        Returns:
            (intent, was_newly_created)
        """
        # 1. Validate operation against strict allowlist
        if operation not in ALLOWED_OPERATIONS:
            raise UnauthorizedOperationError(
                f"Operation '{operation}' is not an authorized contract operation. Allowed: {sorted(ALLOWED_OPERATIONS)}"
            )

        # 2. Validate contract against chain configuration
        chain_config = get_chain_config(chain_id)
        norm_target = target_contract.lower()
        allowed_contracts = [addr.lower() for addr in chain_config.contract_addresses.values()]

        if norm_target not in allowed_contracts:
            raise ContractMismatchError(
                f"Target contract {target_contract} is not in the allowlist for chain {chain_id}: {chain_config.contract_addresses}"
            )

        # 3. Check for existing intent (idempotency check)
        existing_stmt = sa.select(BlockchainTransactionIntent).where(
            BlockchainTransactionIntent.chain_id == chain_id,
            BlockchainTransactionIntent.idempotency_key == idempotency_key,
        )
        existing_intent = (await session.execute(existing_stmt)).scalar_one_or_none()
        if existing_intent:
            logger.info(
                "Duplicate transaction intent requested for chain %d with idempotency key '%s'. Returning existing record %s",
                chain_id,
                idempotency_key,
                existing_intent.id,
            )
            return existing_intent, False

        # 4. Insert new intent
        new_intent = BlockchainTransactionIntent(
            chain_id=chain_id,
            idempotency_key=idempotency_key,
            target_contract=norm_target,
            operation=operation,
            parameters=parameters,
            status=IntentStatus.CREATED,
            created_at=utc_now(),
            updated_at=utc_now(),
        )
        session.add(new_intent)
        await session.flush()

        # 5. Insert atomic outbox record
        outbox_payload = {
            "intent_id": str(new_intent.id),
            "chain_id": chain_id,
            "idempotency_key": idempotency_key,
            "target_contract": norm_target,
            "operation": operation,
            "parameters": parameters,
        }
        outbox_entry = BlockchainTxOutbox(
            intent_id=new_intent.id,
            chain_id=chain_id,
            idempotency_key=idempotency_key,
            payload=outbox_payload,
            status="PENDING",
            attempts=0,
            max_attempts=5,
            backoff_seconds=2,
            next_attempt_at=utc_now(),
            created_at=utc_now(),
            updated_at=utc_now(),
        )
        session.add(outbox_entry)
        await session.flush()

        logger.info(
            "Created transaction intent %s (operation=%s, key=%s) and outbox entry %s",
            new_intent.id,
            operation,
            idempotency_key,
            outbox_entry.id,
        )
        return new_intent, True
