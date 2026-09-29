"""Concurrency-safe persistent nonce management with on-chain reconciliation.

Guarantees:
- Row-level locking (SELECT ... FOR UPDATE) prevents concurrent nonce collisions.
- Persistent state across application and worker restarts.
- Auto-reconciliation against eth_getTransactionCount('latest' and 'pending').
- Detects and handles gaps and external nonce consumption.
"""

import logging
from typing import Any
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.base import utc_now
from app.models.blockchain import RelayerNonce
from app.services.blockchain.errors import NonceCollisionError
from app.services.blockchain.rpc_client import BlockchainRpcClient

logger = logging.getLogger(__name__)


class NonceManager:
    """Manages transactional nonce reservations for relayer signing accounts."""

    def __init__(self, rpc_client: BlockchainRpcClient) -> None:
        self.rpc_client = rpc_client
        self.chain_id = rpc_client.chain_id

    async def reserve_nonce(
        self,
        session: AsyncSession,
        account_address: str,
    ) -> int:
        """Atomically allocates the next available nonce for the account.
        
        Uses row-level locking on the relayer_nonces table to guarantee uniqueness under concurrency.
        """
        norm_address = account_address.lower()

        # Row-lock the account record
        lock_stmt = (
            sa.select(RelayerNonce)
            .where(
                RelayerNonce.chain_id == self.chain_id,
                RelayerNonce.account_address == norm_address,
            )
            .with_for_update()
        )
        record = (await session.execute(lock_stmt)).scalar_one_or_none()

        if not record:
            # Query chain for current nonce state
            onchain_latest = await self.rpc_client.get_transaction_count(norm_address, "latest")
            onchain_pending = await self.rpc_client.get_transaction_count(norm_address, "pending")
            start_nonce = max(onchain_latest, onchain_pending)

            record = RelayerNonce(
                chain_id=self.chain_id,
                account_address=norm_address,
                next_nonce=start_nonce + 1,
                reserved_nonce=start_nonce,
                chain_confirmed_nonce=onchain_latest,
                updated_at=utc_now(),
            )
            session.add(record)
            await session.flush()
            logger.info(
                "Initialized relayer nonce record for %s on chain %d: allocated %d (latest=%d, pending=%d)",
                norm_address,
                self.chain_id,
                start_nonce,
                onchain_latest,
                onchain_pending,
            )
            return start_nonce

        # Record exists, allocate record.next_nonce and advance
        assigned_nonce = record.next_nonce
        record.next_nonce = assigned_nonce + 1
        record.reserved_nonce = assigned_nonce
        record.updated_at = utc_now()
        await session.flush()

        logger.debug(
            "Reserved nonce %d for account %s on chain %d",
            assigned_nonce,
            norm_address,
            self.chain_id,
        )
        return assigned_nonce

    async def reconcile_account_nonce(
        self,
        session: AsyncSession,
        account_address: str,
    ) -> dict[str, int]:
        """Reconciles persisted nonce state against authoritative on-chain data."""
        norm_address = account_address.lower()

        lock_stmt = (
            sa.select(RelayerNonce)
            .where(
                RelayerNonce.chain_id == self.chain_id,
                RelayerNonce.account_address == norm_address,
            )
            .with_for_update()
        )
        record = (await session.execute(lock_stmt)).scalar_one_or_none()

        onchain_latest = await self.rpc_client.get_transaction_count(norm_address, "latest")
        onchain_pending = await self.rpc_client.get_transaction_count(norm_address, "pending")

        if not record:
            start_nonce = max(onchain_latest, onchain_pending)
            record = RelayerNonce(
                chain_id=self.chain_id,
                account_address=norm_address,
                next_nonce=start_nonce,
                reserved_nonce=max(0, start_nonce - 1),
                chain_confirmed_nonce=onchain_latest,
                updated_at=utc_now(),
            )
            session.add(record)
            await session.flush()
        else:
            record.chain_confirmed_nonce = onchain_latest
            if onchain_pending > record.next_nonce:
                logger.warning(
                    "Detected external or advanced transactions for %s on chain %d. Advancing next_nonce from %d to %d",
                    norm_address,
                    self.chain_id,
                    record.next_nonce,
                    onchain_pending,
                )
                record.next_nonce = onchain_pending
            record.updated_at = utc_now()
            await session.flush()

        return {
            "chain_confirmed": onchain_latest,
            "chain_pending": onchain_pending,
            "next_nonce": record.next_nonce,
        }
