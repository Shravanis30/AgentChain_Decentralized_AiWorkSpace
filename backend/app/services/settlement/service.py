"""Core settlement service implementing the full settlement lifecycle for Phase 6.2B.

Coordinates:
- Idempotent settlement creation and authorization.
- Escrow verification against canonical indexed blockchain state.
- Policy authorization (RBAC, smart contract rules, orchestration checks).
- Atomic blockchain transaction intent creation via TransactionIntentService.
- State history recording and immutable audit trail.
- Fail-closed security boundaries.
"""

from datetime import datetime, timezone
import logging
import time
from typing import Any
import uuid
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession
from web3 import Web3

from app.models.audit import AuditEventType, AuditLog
from app.models.base import utc_now
from app.models.blockchain import (
    BlockchainTransactionIntent,
    ChainReorganization,
    EscrowChainState,
    IntentStatus,
    ReorgStatus,
)
from app.models.settlement import (
    Settlement,
    SettlementAction,
    SettlementHistory,
    SettlementStatus,
)
from app.models.user import User
from app.services.blockchain.config import (
    CHAIN_ID_BASE_MAINNET,
    get_chain_config,
)
from app.services.blockchain.transaction_intent import TransactionIntentService
from app.services.settlement.authorizer import (
    AuthorizationDecision,
    SettlementAuthorizationEngine,
)
from app.services.settlement.config import (
    BLOCK_MAINNET_SETTLEMENT,
    is_chain_supported,
)
from app.services.settlement.errors import (
    SettlementAlreadyExistsError,
    SettlementAuthorizationError,
    SettlementMainnetBlockedError,
    SettlementNotFoundError,
    SettlementStateConflictError,
)
from app.services.settlement.idempotency import generate_settlement_idempotency_key
from app.services.settlement.metrics import SettlementMetrics
from app.services.settlement.verifier import (
    EscrowVerificationBoundary,
    EscrowVerificationResult,
)

logger = logging.getLogger(__name__)


class SettlementService:
    """Manages the creation, authorization, and lifecycle of escrow settlements."""

    @staticmethod
    async def create_settlement(
        session: AsyncSession,
        chain_id: int,
        escrow_id: str,
        action: SettlementAction | str,
        user_id: uuid.UUID | None = None,
        orchestration_id: uuid.UUID | None = None,
        execution_id: uuid.UUID | None = None,
        authorization_version: int = 1,
        beneficiary_amount: int | None = None,
        client_refund_amount: int | None = None,
        metadata_json: dict[str, Any] | None = None,
        actor_user: User | None = None,
        client_ip: str | None = None,
    ) -> tuple[Settlement, bool]:
        """Creates a settlement record in PENDING_AUTHORIZATION state idempotently.
        
        Returns:
            (settlement, was_created)
        """
        start_time = time.perf_counter()
        action_enum = SettlementAction(action) if isinstance(action, str) else action
        action_str = action_enum.value

        # 1. Mainnet safety check
        if chain_id == CHAIN_ID_BASE_MAINNET and BLOCK_MAINNET_SETTLEMENT:
            SettlementMetrics.record_mainnet_blocked()
            raise SettlementMainnetBlockedError(
                "Base Mainnet settlement transaction submission is hard-disabled"
            )

        if not is_chain_supported(chain_id):
            raise SettlementAuthorizationError(f"Unsupported chain ID: {chain_id}")

        clean_escrow = escrow_id.strip()
        idempotency_key = generate_settlement_idempotency_key(
            chain_id=chain_id,
            escrow_id=clean_escrow,
            action=action_str,
            authorization_version=authorization_version,
        )

        # 2. Check for existing settlement record (Idempotency)
        existing_stmt = (
            sa.select(Settlement)
            .where(
                Settlement.chain_id == chain_id,
                Settlement.idempotency_key == idempotency_key,
            )
        )
        existing = (await session.execute(existing_stmt)).scalar_one_or_none()
        if existing:
            SettlementMetrics.record_duplicate(chain_id, action_str)
            logger.info(
                "Duplicate settlement request: returning existing settlement %s for idempotency key %s",
                existing.id,
                idempotency_key,
            )
            return existing, False

        # Check for existing conflicting active settlement on the same escrow
        conflicting_stmt = (
            sa.select(Settlement)
            .where(
                Settlement.chain_id == chain_id,
                Settlement.escrow_id == clean_escrow,
                Settlement.status.in_([
                    SettlementStatus.AUTHORIZED.value,
                    SettlementStatus.SUBMITTED.value,
                    SettlementStatus.CONFIRMED.value,
                ]),
            )
        )
        conflicting = (await session.execute(conflicting_stmt)).scalar_one_or_none()
        if conflicting and conflicting.action != action_str:
            raise SettlementStateConflictError(
                f"Conflicting settlement {conflicting.id} with action '{conflicting.action}' "
                f"is already {conflicting.status} for escrow {clean_escrow}"
            )

        # 3. Preliminary escrow verification
        escrow_res = await EscrowVerificationBoundary.verify_escrow_for_settlement(
            session=session,
            chain_id=chain_id,
            escrow_id=clean_escrow,
            action=action_enum,
        )

        chain_config = get_chain_config(chain_id)
        escrow_contract = chain_config.contract_addresses.get("Escrow", "").lower()

        # 4. Insert Settlement record
        now = utc_now()
        settlement = Settlement(
            idempotency_key=idempotency_key,
            chain_id=chain_id,
            escrow_contract=escrow_contract,
            escrow_id=clean_escrow,
            client_address=escrow_res.client.lower(),
            beneficiary_address=escrow_res.developer.lower(),
            token_address=escrow_res.token.lower(),
            amount=escrow_res.amount,
            action=action_str,
            status=SettlementStatus.PENDING_AUTHORIZATION.value,
            authorization_version=authorization_version,
            user_id=user_id,
            orchestration_id=orchestration_id,
            execution_id=execution_id,
            beneficiary_amount=beneficiary_amount,
            client_refund_amount=client_refund_amount,
            metadata_json=metadata_json or {},
            created_at=now,
            updated_at=now,
        )
        session.add(settlement)
        await session.flush()

        # 5. Insert initial history
        history = SettlementHistory(
            settlement_id=settlement.id,
            from_status="NONE",
            to_status=SettlementStatus.PENDING_AUTHORIZATION.value,
            reason="Settlement created in PENDING_AUTHORIZATION",
            actor_id=actor_user.id if actor_user else user_id,
            actor_role=actor_user.primary_role if actor_user else None,
            metadata_json={"idempotency_key": idempotency_key},
            created_at=now,
        )
        session.add(history)

        # 6. Audit log
        audit = AuditLog(
            user_id=actor_user.id if actor_user else user_id,
            wallet_address=actor_user.wallet_address if actor_user else None,
            event_type=AuditEventType.SETTLEMENT_CREATED.value,
            timestamp=now,
            ip_address=client_ip,
            metadata_json={
                "settlement_id": str(settlement.id),
                "escrow_id": clean_escrow,
                "chain_id": chain_id,
                "action": action_str,
                "amount": str(escrow_res.amount),
            },
        )
        session.add(audit)
        await session.flush()

        duration = time.perf_counter() - start_time
        SettlementMetrics.observe_latency(action_str, "creation", duration)
        logger.info(
            "Created settlement %s for escrow %s on chain %d (action: %s)",
            settlement.id,
            clean_escrow,
            chain_id,
            action_str,
        )
        return settlement, True

    @staticmethod
    async def authorize_settlement(
        session: AsyncSession,
        settlement_id: uuid.UUID,
        actor_user: User | None = None,
        caller_wallet: str | None = None,
        reason: str | None = None,
        client_ip: str | None = None,
    ) -> Settlement:
        """Evaluates policy and authorizes a settlement, creating the on-chain transaction intent atomically."""
        start_time = time.perf_counter()

        # 1. Fetch settlement with row lock
        stmt = (
            sa.select(Settlement)
            .where(Settlement.id == settlement_id)
            .with_for_update()
        )
        settlement = (await session.execute(stmt)).scalar_one_or_none()
        if not settlement:
            raise SettlementNotFoundError(f"Settlement {settlement_id} not found")

        # 2. State machine checks
        if settlement.status in (
            SettlementStatus.AUTHORIZED.value,
            SettlementStatus.SUBMITTED.value,
            SettlementStatus.CONFIRMED.value,
        ):
            logger.info("Settlement %s is already in state %s; returning existing", settlement.id, settlement.status)
            return settlement

        if settlement.status in (
            SettlementStatus.FAILED.value,
            SettlementStatus.CANCELLED.value,
            SettlementStatus.BLOCKED.value,
        ):
            raise SettlementStateConflictError(
                f"Cannot authorize settlement {settlement.id} in terminal/blocked status '{settlement.status}'"
            )

        now = utc_now()
        action_enum = SettlementAction(settlement.action)

        # 3. Check if chain has an unresolved deep reorg failure (fail-closed immediately)
        deep_reorg_stmt = sa.select(ChainReorganization).where(
            ChainReorganization.chain_id == settlement.chain_id,
            ChainReorganization.status.in_([ReorgStatus.FAILED, ReorgStatus.FAILED.value, "FAILED"]),
        )
        deep_reorg = (await session.execute(deep_reorg_stmt)).scalars().first()
        if deep_reorg:
            raise SettlementAuthorizationError(
                f"Active unrecovered chain reorganization detected on chain {settlement.chain_id}. "
                "Authorizations are halted until manual operational recovery is completed."
            )

        # 4. Escrow verification against latest canonical indexed state
        try:
            escrow_res = await EscrowVerificationBoundary.verify_escrow_for_settlement(
                session=session,
                chain_id=settlement.chain_id,
                escrow_id=settlement.escrow_id,
                action=action_enum,
                expected_client=settlement.client_address,
                expected_beneficiary=settlement.beneficiary_address,
                expected_amount=int(settlement.amount),
            )
        except Exception as exc:
            settlement.status = SettlementStatus.BLOCKED.value
            settlement.blocked_reason = f"Escrow verification failed: {exc}"
            settlement.updated_at = now

            session.add(
                SettlementHistory(
                    settlement_id=settlement.id,
                    from_status=SettlementStatus.PENDING_AUTHORIZATION.value,
                    to_status=SettlementStatus.BLOCKED.value,
                    reason=settlement.blocked_reason,
                    actor_id=actor_user.id if actor_user else None,
                    actor_role=actor_user.primary_role if actor_user else None,
                    created_at=now,
                )
            )
            session.add(
                AuditLog(
                    user_id=actor_user.id if actor_user else None,
                    wallet_address=caller_wallet,
                    event_type=AuditEventType.SETTLEMENT_BLOCKED.value,
                    timestamp=now,
                    ip_address=client_ip,
                    metadata_json={
                        "settlement_id": str(settlement.id),
                        "reason": settlement.blocked_reason,
                    },
                )
            )
            SettlementMetrics.record_authorization_blocked(settlement.action, str(exc))
            raise

        # 5. Evaluate authorization policy engine
        try:
            decision: AuthorizationDecision = await SettlementAuthorizationEngine.evaluate_authorization(
                session=session,
                settlement=settlement,
                escrow_result=escrow_res,
                caller_user=actor_user,
                caller_wallet=caller_wallet,
            )
        except Exception as exc:
            settlement.status = SettlementStatus.BLOCKED.value
            settlement.blocked_reason = f"Policy authorization failed: {exc}"
            settlement.updated_at = now

            session.add(
                SettlementHistory(
                    settlement_id=settlement.id,
                    from_status=SettlementStatus.PENDING_AUTHORIZATION.value,
                    to_status=SettlementStatus.BLOCKED.value,
                    reason=settlement.blocked_reason,
                    actor_id=actor_user.id if actor_user else None,
                    actor_role=actor_user.primary_role if actor_user else None,
                    created_at=now,
                )
            )
            session.add(
                AuditLog(
                    user_id=actor_user.id if actor_user else None,
                    wallet_address=caller_wallet,
                    event_type=AuditEventType.SETTLEMENT_BLOCKED.value,
                    timestamp=now,
                    ip_address=client_ip,
                    metadata_json={
                        "settlement_id": str(settlement.id),
                        "reason": settlement.blocked_reason,
                    },
                )
            )
            SettlementMetrics.record_authorization_blocked(settlement.action, str(exc))
            raise

        # Check if another settlement for this escrow is already active
        conflicting_auth_stmt = (
            sa.select(Settlement)
            .where(
                Settlement.chain_id == settlement.chain_id,
                Settlement.escrow_id == settlement.escrow_id,
                Settlement.id != settlement.id,
                Settlement.status.in_([
                    SettlementStatus.AUTHORIZED.value,
                    SettlementStatus.SUBMITTED.value,
                    SettlementStatus.CONFIRMED.value,
                ]),
            )
            .with_for_update()
        )
        conflicting_auth = (await session.execute(conflicting_auth_stmt)).scalars().first()
        if conflicting_auth:
            raise SettlementStateConflictError(
                f"Escrow {settlement.escrow_id} already has active settlement {conflicting_auth.id} "
                f"with action '{conflicting_auth.action}' in status '{conflicting_auth.status}'"
            )

        # 5. Create atomic Transaction Intent via TransactionIntentService
        tx_idempotency_key = f"tx:{settlement.idempotency_key}"
        chain_config = get_chain_config(settlement.chain_id)
        escrow_contract = chain_config.contract_addresses["Escrow"].lower()

        intent, _ = await TransactionIntentService.create_intent(
            session=session,
            chain_id=settlement.chain_id,
            idempotency_key=tx_idempotency_key,
            target_contract=escrow_contract,
            operation=decision.target_operation,
            parameters=decision.parameters,
        )

        # 6. Update settlement state
        prev_status = settlement.status
        settlement.status = SettlementStatus.AUTHORIZED.value
        settlement.authorized_by = actor_user.id if actor_user else None
        settlement.authorized_at = now
        settlement.authorization_reason = reason or decision.reason
        settlement.transaction_intent_id = intent.id
        settlement.updated_at = now

        # 7. Record history
        session.add(
            SettlementHistory(
                settlement_id=settlement.id,
                from_status=prev_status,
                to_status=SettlementStatus.AUTHORIZED.value,
                reason=settlement.authorization_reason,
                actor_id=actor_user.id if actor_user else None,
                actor_role=actor_user.primary_role if actor_user else None,
                metadata_json={"intent_id": str(intent.id), "operation": decision.target_operation},
                created_at=now,
            )
        )

        # 8. Audit log
        session.add(
            AuditLog(
                user_id=actor_user.id if actor_user else None,
                wallet_address=caller_wallet,
                event_type=AuditEventType.SETTLEMENT_AUTHORIZED.value,
                timestamp=now,
                ip_address=client_ip,
                metadata_json={
                    "settlement_id": str(settlement.id),
                    "intent_id": str(intent.id),
                    "action": settlement.action,
                    "operation": decision.target_operation,
                },
            )
        )

        duration = time.perf_counter() - start_time
        SettlementMetrics.record_authorization(settlement.action, SettlementStatus.AUTHORIZED.value)
        SettlementMetrics.observe_latency(settlement.action, "authorization", duration)

        logger.info(
            "Authorized settlement %s with transaction intent %s",
            settlement.id,
            intent.id,
        )
        await session.flush()
        return settlement

    @staticmethod
    async def cancel_settlement(
        session: AsyncSession,
        settlement_id: uuid.UUID,
        actor_user: User | None = None,
        reason: str | None = None,
        client_ip: str | None = None,
    ) -> Settlement:
        """Cancels a settlement prior to blockchain submission."""
        stmt = (
            sa.select(Settlement)
            .where(Settlement.id == settlement_id)
            .with_for_update()
        )
        settlement = (await session.execute(stmt)).scalar_one_or_none()
        if not settlement:
            raise SettlementNotFoundError(f"Settlement {settlement_id} not found")

        if settlement.status in (
            SettlementStatus.SUBMITTED.value,
            SettlementStatus.CONFIRMED.value,
        ):
            raise SettlementStateConflictError(
                f"Cannot cancel settlement in active/terminal status '{settlement.status}'"
            )

        now = utc_now()
        prev_status = settlement.status
        settlement.status = SettlementStatus.CANCELLED.value
        settlement.cancelled_at = now
        settlement.error_message = reason or "Cancelled by user"
        settlement.updated_at = now

        session.add(
            SettlementHistory(
                settlement_id=settlement.id,
                from_status=prev_status,
                to_status=SettlementStatus.CANCELLED.value,
                reason=settlement.error_message,
                actor_id=actor_user.id if actor_user else None,
                actor_role=actor_user.primary_role if actor_user else None,
                created_at=now,
            )
        )

        session.add(
            AuditLog(
                user_id=actor_user.id if actor_user else None,
                wallet_address=actor_user.wallet_address if actor_user else None,
                event_type=AuditEventType.SETTLEMENT_CANCELLED.value,
                timestamp=now,
                ip_address=client_ip,
                metadata_json={
                    "settlement_id": str(settlement.id),
                    "reason": settlement.error_message,
                },
            )
        )

        logger.info("Cancelled settlement %s", settlement.id)
        await session.flush()
        return settlement
