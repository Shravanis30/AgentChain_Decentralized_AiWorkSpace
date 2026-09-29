"""Periodic blockchain state reconciliation service.

Guarantees:
- Safe to execute concurrently and repeatedly.
- The blockchain is authoritative for on-chain state.
- Detects stuck transactions, missing receipts, block gaps, and un-dispatched outbox records.
"""

from datetime import datetime, timedelta
import logging
from typing import Any
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.base import utc_now
from app.models.blockchain import (
    AttemptStatus,
    BlockStatus,
    BlockchainTransaction,
    BlockchainTransactionAttempt,
    BlockchainTransactionIntent,
    BlockchainTxOutbox,
    IndexedBlock,
    IntentStatus,
    TxLifecycleStatus,
)
from app.services.blockchain.config import ChainConfig
from app.services.blockchain.relayer import BlockchainRelayer
from app.services.blockchain.rpc_client import BlockchainRpcClient

logger = logging.getLogger(__name__)


class BlockchainReconciliationService:
    """Detects and repairs divergence between PostgreSQL records and blockchain reality."""

    def __init__(
        self,
        config: ChainConfig,
        rpc_client: BlockchainRpcClient,
        relayer: BlockchainRelayer,
        stuck_threshold_seconds: int = 120,
        max_replacement_attempts: int = 4,
    ) -> None:
        self.config = config
        self.chain_id = config.chain_id
        self.rpc_client = rpc_client
        self.relayer = relayer
        self.stuck_threshold = timedelta(seconds=stuck_threshold_seconds)
        self.max_replacement_attempts = max_replacement_attempts

    async def reconcile_pending_transactions(self, session: AsyncSession) -> dict[str, int]:
        """Safely reconciles pending transactions according to the strict replacement decision flow.
        
        Decision Flow:
        1. Acquire pending transactions with row-level locking (with_for_update).
        2. Query receipts for all attempts of the transaction.
        3. If receipt exists and canonical -> finalize CONFIRMED/FAILED.
        4. If receipt query fails with RPC_ERROR or NETWORK_UNAVAILABLE -> classify UNKNOWN, defer.
        5. If receipt cleanly NOT_FOUND, check if stuck past threshold.
        6. Query transaction object via eth_getTransactionByHash:
           - If blockNumber is populated -> transaction is MINED! Lagging receipt; defer replacement.
        7. Inspect account nonce state on chain (latest and pending):
           - If latest_nonce > tx.nonce: nonce consumed on-chain! Reconcile as DROPPED, never replace.
           - If latest_nonce < tx.nonce: nonce gap; defer.
           - If latest_nonce == tx.nonce: unmined.
        8. Check replacement limits (tx.attempts_count < max_replacement_attempts).
        9. Check concurrency: verify no other worker added an attempt.
        10. Execute safe replacement using the SAME nonce and higher fee.
        """
        stmt = (
            sa.select(BlockchainTransaction)
            .where(
                BlockchainTransaction.chain_id == self.chain_id,
                BlockchainTransaction.status.in_([TxLifecycleStatus.SUBMITTED, TxLifecycleStatus.PENDING]),
            )
            .with_for_update(skip_locked=True)
        )
        pending_txs = (await session.execute(stmt)).scalars().all()

        confirmed_count = 0
        bumped_count = 0
        now = utc_now()

        from app.services.blockchain.metrics import BlockchainMetrics

        for tx in pending_txs:
            # Concurrency check: Ensure transaction hasn't transitioned concurrently
            if tx.status not in (TxLifecycleStatus.SUBMITTED, TxLifecycleStatus.PENDING):
                BlockchainMetrics.record_replacement_race_prevented(self.chain_id)
                continue

            # Fetch all attempts for this transaction
            attempts_stmt = (
                sa.select(BlockchainTransactionAttempt)
                .where(BlockchainTransactionAttempt.transaction_id == tx.id)
                .order_by(BlockchainTransactionAttempt.attempt_number.desc())
            )
            attempts = (await session.execute(attempts_stmt)).scalars().all()
            hashes_to_check = [a.tx_hash for a in attempts]
            if tx.current_tx_hash and tx.current_tx_hash not in hashes_to_check:
                hashes_to_check.insert(0, tx.current_tx_hash)

            # --- STEP A: Query receipts across all attempts ---
            receipt_found = False
            rpc_error_occurred = False
            for h in hashes_to_check:
                receipt_query = await self.rpc_client.get_transaction_receipt_safe(h)
                if receipt_query.is_found:
                    receipt_found = True
                    break
                elif receipt_query.is_error:
                    rpc_error_occurred = True

            if receipt_found:
                # Receipt exists -> poll and finalize
                resolved = await self.relayer.poll_and_update_receipt(session, tx)
                if resolved:
                    confirmed_count += 1
                    BlockchainMetrics.record_replacement_safety_check(self.chain_id, "RECEIPT_RESOLVED")
                    continue

            # If receipt query failed due to RPC or network error, defer immediately
            if rpc_error_occurred:
                logger.warning(
                    "Receipt check for tx %s (nonce %d) encountered RPC/network error. Deferring replacement.",
                    tx.id,
                    tx.nonce,
                )
                BlockchainMetrics.record_replacement_deferred_unknown_rpc(self.chain_id)
                BlockchainMetrics.record_replacement_safety_check(self.chain_id, "DEFERRED_RPC_ERROR")
                continue

            # --- STEP B: Receipt cleanly NOT_FOUND. Check if stuck past threshold ---
            latest_submitted_at = attempts[0].submitted_at if (attempts and attempts[0].submitted_at) else tx.submitted_at
            elapsed = (now - latest_submitted_at) if latest_submitted_at else timedelta(0)
            if elapsed <= self.stuck_threshold:
                # Still within normal confirmation window; give mempool time
                continue

            # --- STEP C: Stuck beyond threshold. Query eth_getTransactionByHash ---
            latest_hash = tx.current_tx_hash or (hashes_to_check[0] if hashes_to_check else None)
            if not latest_hash:
                continue

            tx_query = await self.rpc_client.get_transaction_safe(latest_hash)
            if tx_query.is_error:
                logger.warning(
                    "Transaction lookup for %s encountered RPC error (%s). Deferring replacement.",
                    latest_hash,
                    tx_query.error_message,
                )
                BlockchainMetrics.record_replacement_deferred_unknown_rpc(self.chain_id)
                BlockchainMetrics.record_replacement_safety_check(self.chain_id, "DEFERRED_RPC_ERROR")
                continue

            tx_obj = tx_query.data
            if tx_obj is not None:
                # Transaction found in node
                block_num = tx_obj.get("blockNumber")
                if block_num is not None:
                    # Transaction is ALREADY MINED! (Receipt lagging)
                    logger.info(
                        "Transaction %s is mined in block %s; receipt temporarily lagging. Do not replace.",
                        latest_hash,
                        block_num,
                    )
                    BlockchainMetrics.record_replacement_safety_check(self.chain_id, "DEFERRED_ALREADY_MINED")
                    continue

            # --- STEP D: Inspect account nonce state on chain ---
            latest_nonce_q = await self.rpc_client.get_transaction_count_safe(tx.from_address, "latest")
            pending_nonce_q = await self.rpc_client.get_transaction_count_safe(tx.from_address, "pending")

            if latest_nonce_q.is_error or pending_nonce_q.is_error:
                logger.warning("Nonce lookup failed with RPC error for %s. Deferring.", tx.from_address)
                BlockchainMetrics.record_replacement_deferred_unknown_rpc(self.chain_id)
                BlockchainMetrics.record_replacement_safety_check(self.chain_id, "DEFERRED_RPC_ERROR")
                continue

            latest_nonce = latest_nonce_q.data
            pending_nonce = pending_nonce_q.data

            # Nonce safety verification:
            if latest_nonce > tx.nonce:
                # Nonce consumed on-chain externally or by an earlier attempt
                logger.error(
                    "Nonce %d for transaction %s consumed on-chain (latest nonce=%d). Reconciling as DROPPED.",
                    tx.nonce,
                    tx.id,
                    latest_nonce,
                )
                tx.status = TxLifecycleStatus.DROPPED
                tx.error_message = f"Nonce {tx.nonce} consumed on-chain externally (latest nonce={latest_nonce})"
                if tx.intent_id:
                    intent_stmt = sa.select(BlockchainTransactionIntent).where(
                        BlockchainTransactionIntent.id == tx.intent_id
                    )
                    intent = (await session.execute(intent_stmt)).scalar_one_or_none()
                    if intent:
                        intent.status = IntentStatus.FAILED
                        intent.error_message = tx.error_message
                        intent.updated_at = now
                for att in attempts:
                    if att.status in (AttemptStatus.SUBMITTED, AttemptStatus.PENDING):
                        att.status = AttemptStatus.DROPPED
                        att.updated_at = now
                tx.updated_at = now
                await session.flush()
                BlockchainMetrics.record_replacement_safety_check(self.chain_id, "NONCE_CONSUMED_EXTERNALLY")
                continue

            if latest_nonce < tx.nonce:
                # Nonce gap: previous transaction must confirm first
                logger.warning(
                    "Nonce gap detected for tx %s: tx.nonce=%d, latest on-chain=%d. Deferring replacement.",
                    tx.id,
                    tx.nonce,
                    latest_nonce,
                )
                BlockchainMetrics.record_replacement_safety_check(self.chain_id, "NONCE_GAP_DEFERRED")
                continue

            # --- STEP E: Check maximum replacement attempts ---
            if tx.attempts_count >= self.max_replacement_attempts:
                logger.error(
                    "Transaction %s (nonce %d) exhausted maximum %d replacement attempts.",
                    tx.id,
                    tx.nonce,
                    self.max_replacement_attempts,
                )
                BlockchainMetrics.record_replacement_safety_check(self.chain_id, "MAX_ATTEMPTS_EXHAUSTED")
                continue

            # --- STEP F: Concurrency double-check ---
            fresh_attempts_stmt = sa.select(sa.func.count(BlockchainTransactionAttempt.id)).where(
                BlockchainTransactionAttempt.transaction_id == tx.id
            )
            fresh_count = (await session.execute(fresh_attempts_stmt)).scalar() or 0
            if fresh_count > len(attempts):
                logger.warning("Concurrent replacement worker created an attempt for %s; aborting duplicate bump.", tx.id)
                BlockchainMetrics.record_replacement_race_prevented(self.chain_id)
                continue

            # --- STEP G: Execute safe replacement with SAME nonce and higher fee ---
            BlockchainMetrics.record_replacement_safety_check(self.chain_id, "REPLACEMENT_APPROVED")
            logger.warning(
                "Transaction %s (nonce %d) stuck past %s and verified eligible. Initiating safe replacement (attempt %d).",
                tx.id,
                tx.nonce,
                self.stuck_threshold,
                tx.attempts_count + 1,
            )
            await self.relayer.bump_and_replace_transaction(session, tx)
            bumped_count += 1

        return {"confirmed": confirmed_count, "bumped": bumped_count}

    async def detect_block_gaps(self, session: AsyncSession, lookback_blocks: int = 100) -> list[int]:
        """Identifies missing block heights in indexed_blocks."""
        stmt = (
            sa.select(sa.func.max(IndexedBlock.block_number), sa.func.min(IndexedBlock.block_number))
            .where(
                IndexedBlock.chain_id == self.chain_id,
                IndexedBlock.status != BlockStatus.ORPHANED,
            )
        )
        max_num, min_num = (await session.execute(stmt)).one()
        if max_num is None or min_num is None:
            return []

        start_num = max(min_num, max_num - lookback_blocks)
        existing_blocks_stmt = (
            sa.select(IndexedBlock.block_number)
            .where(
                IndexedBlock.chain_id == self.chain_id,
                IndexedBlock.block_number >= start_num,
                IndexedBlock.block_number <= max_num,
                IndexedBlock.status != BlockStatus.ORPHANED,
            )
        )
        existing_numbers = set((await session.execute(existing_blocks_stmt)).scalars().all())

        missing = [num for num in range(start_num, max_num + 1) if num not in existing_numbers]
        if missing:
            logger.warning("Detected %d missing indexed blocks on chain %d: %s", len(missing), self.chain_id, missing[:10])
        return missing

    async def reconcile_stuck_outbox(self, session: AsyncSession, stuck_threshold_minutes: int = 10) -> int:
        """Resets outbox records stuck in intermediate dispatch states."""
        cutoff = utc_now() - timedelta(minutes=stuck_threshold_minutes)
        stmt = (
            sa.select(BlockchainTxOutbox)
            .where(
                BlockchainTxOutbox.chain_id == self.chain_id,
                BlockchainTxOutbox.status == "PENDING",
                BlockchainTxOutbox.next_attempt_at < cutoff,
            )
        )
        stuck_items = (await session.execute(stmt)).scalars().all()
        for item in stuck_items:
            item.next_attempt_at = utc_now()
            item.updated_at = utc_now()

        return len(stuck_items)
