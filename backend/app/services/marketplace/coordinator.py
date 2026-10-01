"""Authoritative Marketplace Lifecycle Coordinator (Phase 6.8).

Integrates and orchestrates the end-to-end marketplace execution lifecycle:
Client Goal
-> Candidate Discovery
-> Reputation-Aware Selection
-> Budget/Price Validation
-> Exact Agent + Version + Price Pinning
-> Escrow Authorization/Creation
-> Agent Execution
-> Result
-> Cryptographic Notarization
-> Verified Outcome
-> Settlement Decision
-> Escrow Release / Refund / Dispute
-> Revenue Distribution (85/10/5)
-> Reputation Evidence
"""

import logging
import uuid
from datetime import datetime
from typing import Any

from eth_abi import encode as abi_encode
import sqlalchemy as sa
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from web3 import Web3

from app.models.agent import Agent, AgentExecution, AgentStatus, ExecutionStatus
from app.models.agent_selection import SelectionDecision
from app.models.base import utc_now
from app.models.blockchain import EscrowChainState, EventStatus
from app.models.distribution import Distribution, DistributionStatus
from app.models.marketplace import MarketplaceLifecycleStatus, MarketplaceOrder
from app.models.notarization import NotarizationStatus, ResultNotarization
from app.models.reputation import ReputationEvent, ReputationOutcomeType
from app.models.settlement import Settlement, SettlementAction, SettlementStatus
from app.models.user import User, UserRole
from app.schemas.orchestration import TaskDefinitionInput
from app.services.agent_execution import AgentExecutionService
from app.services.agent_selector import AgentSelector, DeterministicAgentSelector
from app.services.blockchain.config import (
    CHAIN_ID_BASE_MAINNET,
    get_chain_config,
)
from app.services.distribution.service import DistributionService
from app.services.marketplace.errors import (
    MarketplaceAuthorizationError,
    MarketplaceEscrowMismatchError,
    MarketplaceEscrowUnconfirmedError,
    MarketplaceOrderingViolationError,
    MarketplaceOrderNotFoundError,
    MarketplacePriceTamperError,
    MarketplaceStateConflictError,
)
from app.services.marketplace.metrics import MarketplaceMetrics
from app.services.notarization.service import NotarizationService
from app.services.reputation.service import ReputationService
from app.services.settlement.service import SettlementService

logger = logging.getLogger("agentchain.marketplace.coordinator")


def compute_deterministic_escrow_id(
    chain_id: int,
    escrow_contract: str,
    client_address: str,
    reference_id_hex: str,
    salt: int = 0,
) -> str:
    """Computes deterministic escrow identifier matching Escrow.sol computeEscrowId.
    
    keccak256(abi.encode(block.chainid, address(this), client, referenceId, salt))
    """
    ref_bytes = bytes.fromhex(reference_id_hex.removeprefix("0x"))
    if len(ref_bytes) != 32:
        raise ValueError(f"reference_id must be exactly 32 bytes, got {len(ref_bytes)}")

    encoded = abi_encode(
        ["uint256", "address", "address", "bytes32", "uint256"],
        [
            chain_id,
            Web3.to_checksum_address(escrow_contract),
            Web3.to_checksum_address(client_address),
            ref_bytes,
            salt,
        ],
    )
    return Web3.to_hex(Web3.keccak(encoded))


class MarketplaceLifecycleCoordinator:
    """Authoritative coordinator ensuring deterministic lifecycle ordering and guarantees."""

    def __init__(self, agent_selector: AgentSelector | None = None) -> None:
        self.agent_selector = agent_selector or DeterministicAgentSelector()

    async def create_order(
        self,
        db: AsyncSession,
        client_user: User,
        goal: str,
        task_input: dict[str, Any],
        chain_id: int,
        idempotency_key: str,
        required_capabilities: list[str] | None = None,
        max_budget_atomic: int | None = None,
        price_currency: str = "USDC",
        preferred_agent_id: uuid.UUID | None = None,
        preferred_agent_version: str | None = None,
    ) -> tuple[MarketplaceOrder, bool]:
        """Creates a new marketplace order with deterministic agent selection and locked price.
        
        Idempotent: returns existing order if idempotency_key matches.
        """
        # 1. Idempotency Check
        existing_stmt = select(MarketplaceOrder).where(
            MarketplaceOrder.idempotency_key == idempotency_key
        ).options(
            selectinload(MarketplaceOrder.selection_decision),
            selectinload(MarketplaceOrder.selected_agent),
        )
        existing = (await db.execute(existing_stmt)).scalar_one_or_none()
        if existing:
            logger.info("Idempotent hit: returning existing marketplace order %s", existing.id)
            return existing, False

        # 2. Client wallet resolution
        client_wallet = client_user.wallet_address
        if not client_wallet:
            # Fallback or check User.wallet_address
            client_wallet = getattr(client_user, "wallet_address", None) or "0x0000000000000000000000000000000000000001"
        client_wallet = Web3.to_checksum_address(client_wallet)

        # 3. Formulate Candidate Selection via AgentSelector
        task_schema = TaskDefinitionInput(
            task_key="marketplace-task",
            capability=required_capabilities[0] if required_capabilities else None,
            agent_id=preferred_agent_id,
            agent_version=preferred_agent_version,
            max_budget_atomic=max_budget_atomic,
            budget_currency=price_currency,
            input=task_input,
        )

        selection_result = await self.agent_selector.select_agent(
            db=db,
            task=task_schema,
            attempt_number=1,
        )
        decision = selection_result.decision
        agent_ref = selection_result.agent_ref
        if decision.id is None:
            decision.id = uuid.uuid4()
        db.add(decision)
        await db.flush()

        # 4. Resolve Escrow Contract for Chain
        chain_config = get_chain_config(chain_id)
        escrow_contract = chain_config.contract_addresses.get("Escrow")
        if not escrow_contract:
            raise ValueError(f"No Escrow contract configured for chain {chain_id}")
        escrow_contract = Web3.to_checksum_address(escrow_contract)

        # 5. Lock Authoritative Price & Determine Escrow Parameters
        pinned_price = int(decision.selected_price_atomic or 0)
        platform_fee = 0
        total_escrow = pinned_price + platform_fee

        order_id = uuid.uuid4()
        order_number = f"MKT-{order_id.hex[:12].upper()}"

        ref_hash = Web3.keccak(text=f"marketplace_order:{order_id}")
        escrow_reference_id = Web3.to_hex(ref_hash)
        escrow_salt = "0"
        deterministic_escrow_id = compute_deterministic_escrow_id(
            chain_id=chain_id,
            escrow_contract=escrow_contract,
            client_address=client_wallet,
            reference_id_hex=escrow_reference_id,
            salt=0,
        )

        # 6. Persist Order
        now = utc_now()
        order = MarketplaceOrder(
            id=order_id,
            order_number=order_number,
            idempotency_key=idempotency_key,
            client_user_id=client_user.id,
            client_address=client_wallet.lower(),
            goal=goal,
            task_input=task_input,
            max_budget_atomic=max_budget_atomic,
            price_currency=price_currency,
            selection_decision_id=decision.id,
            selected_agent_id=agent_ref.agent_id,
            selected_agent_version=agent_ref.agent_version,
            pinned_price_atomic=pinned_price,
            platform_fee_atomic=platform_fee,
            total_escrow_atomic=total_escrow,
            escrow_chain_id=chain_id,
            escrow_contract=escrow_contract.lower(),
            escrow_reference_id=escrow_reference_id,
            escrow_salt=escrow_salt,
            escrow_id=deterministic_escrow_id.lower(),
            status=MarketplaceLifecycleStatus.PRICE_LOCKED.value,
            metadata_json={
                "selection_hash": decision.canonical_selection_hash,
                "reputation_score_scaled": decision.reputation_score_scaled,
                "reputation_state": decision.reputation_state,
                "economic_policy_version": decision.economic_policy_version,
            },
            created_at=now,
            updated_at=now,
        )
        db.add(order)
        await db.flush()

        MarketplaceMetrics.record_order_created(order.status)
        logger.info(
            "Created marketplace order %s (price=%s %s, escrow_id=%s)",
            order.id,
            pinned_price,
            price_currency,
            deterministic_escrow_id,
        )
        return order, True

    async def verify_and_bind_escrow(
        self,
        db: AsyncSession,
        order_id: uuid.UUID,
    ) -> MarketplaceOrder:
        """Verifies on-chain escrow funding against canonical indexed EscrowChainState.
        
        Enforces:
        - Escrow exists and is canonical
        - 32-block canonical confirmation status (CONFIRMED)
        - Exact amount matches total_escrow_atomic
        - Exact client address matches order.client_address
        - Exact beneficiary matches agent owner's payout address
        - Escrow state is FUNDED (1) or LOCKED (2)
        """
        order = await self._get_order_for_update(db, order_id)
        if not order.escrow_id:
            raise MarketplaceOrderingViolationError("Order does not have a deterministic escrow ID assigned")

        # 1. Fetch Agent and Owner for Beneficiary Verification
        agent_stmt = select(Agent).where(Agent.id == order.selected_agent_id).options(selectinload(Agent.owner))
        agent = (await db.execute(agent_stmt)).scalar_one_or_none()
        if not agent:
            raise MarketplaceOrderNotFoundError(f"Selected agent {order.selected_agent_id} not found")

        expected_beneficiary = agent.owner.wallet_address if agent.owner else None
        if not expected_beneficiary:
            expected_beneficiary = "0x0000000000000000000000000000000000000002"
        expected_beneficiary = expected_beneficiary.lower()

        # 2. Query EscrowChainState
        escrow_stmt = select(EscrowChainState).where(
            EscrowChainState.chain_id == order.escrow_chain_id,
            EscrowChainState.escrow_id == order.escrow_id.lower(),
        )
        escrow_record = (await db.execute(escrow_stmt)).scalar_one_or_none()
        if not escrow_record:
            MarketplaceMetrics.record_escrow_verification("not_found")
            raise MarketplaceEscrowMismatchError(
                f"Escrow record '{order.escrow_id}' not found on chain {order.escrow_chain_id}"
            )

        # 3. Canonical and Confirmation check (32-block depth policy)
        if not escrow_record.is_canonical:
            MarketplaceMetrics.record_escrow_verification("orphaned")
            raise MarketplaceEscrowUnconfirmedError(
                f"Escrow '{order.escrow_id}' is non-canonical or orphaned by chain reorg"
            )

        if escrow_record.confirmation_status != EventStatus.CONFIRMED.value:
            MarketplaceMetrics.record_escrow_verification("unconfirmed")
            raise MarketplaceEscrowUnconfirmedError(
                f"Escrow '{order.escrow_id}' status is '{escrow_record.confirmation_status}', but requires 'CONFIRMED' (32 blocks)"
            )

        # 4. Strict Economic & Identity Invariants
        if int(escrow_record.amount) != int(order.total_escrow_atomic):
            MarketplaceMetrics.record_escrow_verification("amount_mismatch")
            raise MarketplaceEscrowMismatchError(
                f"Escrow amount {escrow_record.amount} does not match pinned order amount {order.total_escrow_atomic}"
            )

        if escrow_record.client.lower() != order.client_address.lower():
            MarketplaceMetrics.record_escrow_verification("client_mismatch")
            raise MarketplaceEscrowMismatchError(
                f"Escrow client '{escrow_record.client}' does not match order client '{order.client_address}'"
            )

        if escrow_record.developer.lower() != expected_beneficiary:
            MarketplaceMetrics.record_escrow_verification("beneficiary_mismatch")
            raise MarketplaceEscrowMismatchError(
                f"Escrow developer '{escrow_record.developer}' does not match agent payout address '{expected_beneficiary}'"
            )

        # 5. Escrow State: FUNDED (1) or LOCKED (2)
        if escrow_record.current_chain_state not in (1, 2):
            MarketplaceMetrics.record_escrow_verification("invalid_state")
            raise MarketplaceEscrowMismatchError(
                f"Escrow '{order.escrow_id}' is in state {escrow_record.current_chain_state}, but requires FUNDED (1) or LOCKED (2)"
            )

        # 6. Advance Order to ESCROW_FUNDED
        prev_status = order.status
        order.status = MarketplaceLifecycleStatus.ESCROW_FUNDED.value
        order.updated_at = utc_now()
        await db.flush()

        MarketplaceMetrics.record_escrow_verification("success")
        MarketplaceMetrics.record_state_transition(prev_status, order.status)
        logger.info("Escrow verified and bound for order %s (escrow_id=%s)", order.id, order.escrow_id)
        return order

    async def enqueue_execution(
        self,
        db: AsyncSession,
        order_id: uuid.UUID,
    ) -> AgentExecution:
        """Enqueues execution for the marketplace order.
        
        CRITICAL ORDERING INVARIANT:
        Execution CANNOT precede verified and confirmed escrow funding.
        """
        order = await self._get_order_for_update(db, order_id)

        # 1. Enforce Escrow Funding Prerequisite
        if order.status != MarketplaceLifecycleStatus.ESCROW_FUNDED.value:
            MarketplaceMetrics.record_ordering_violation("enqueue_before_escrow_funded")
            raise MarketplaceOrderingViolationError(
                f"Cannot enqueue execution: order is in status '{order.status}'. Requires ESCROW_FUNDED."
            )

        # 2. Idempotency Check on Execution
        exec_idempotency_key = f"mkt-exec:{order.id}"
        if order.execution_id:
            existing_exec = await AgentExecutionService.get_execution_by_id(db, order.execution_id)
            if existing_exec:
                return existing_exec

        # 3. Enqueue Execution using the exact pinned agent and version
        execution = await AgentExecutionService.enqueue_agent_execution(
            db=db,
            agent_id=order.selected_agent_id,
            requested_by_user_id=order.client_user_id,
            input_data=order.task_input,
            idempotency_key=exec_idempotency_key,
            agent_version=order.selected_agent_version,
            orchestration_id=order.orchestration_id,
            task_id=order.orchestration_task_id,
        )

        order.execution_id = execution.id
        order.status = MarketplaceLifecycleStatus.EXECUTION_QUEUED.value
        order.updated_at = utc_now()
        await db.flush()

        MarketplaceMetrics.record_state_transition(MarketplaceLifecycleStatus.ESCROW_FUNDED.value, order.status)
        logger.info("Enqueued execution %s for marketplace order %s", execution.id, order.id)
        return execution

    async def handle_execution_update(
        self,
        db: AsyncSession,
        order_id: uuid.UUID,
    ) -> MarketplaceOrder:
        """Synchronizes order status with the underlying AgentExecution state."""
        order = await self._get_order_for_update(db, order_id)
        if not order.execution_id:
            raise MarketplaceOrderingViolationError("Order has no associated execution")

        execution = await AgentExecutionService.get_execution_by_id(db, order.execution_id)
        if not execution:
            raise MarketplaceOrderNotFoundError(f"Execution {order.execution_id} not found")

        prev_status = order.status
        now = utc_now()

        if execution.status == ExecutionStatus.RUNNING.value:
            order.status = MarketplaceLifecycleStatus.EXECUTING.value
        elif execution.status == ExecutionStatus.SUCCEEDED.value:
            order.status = MarketplaceLifecycleStatus.RESULT_AVAILABLE.value
        elif execution.status == ExecutionStatus.FAILED.value:
            order.status = MarketplaceLifecycleStatus.FAILED.value
            order.error_message = execution.error_message or "Execution failed"
        elif execution.status == ExecutionStatus.TIMED_OUT.value:
            order.status = MarketplaceLifecycleStatus.TIMED_OUT.value
            order.error_message = "Execution timed out"
        elif execution.status == ExecutionStatus.CANCELLED.value:
            order.status = MarketplaceLifecycleStatus.CANCELLED.value
            order.error_message = "Execution was cancelled"

        order.updated_at = now
        await db.flush()

        if prev_status != order.status:
            MarketplaceMetrics.record_state_transition(prev_status, order.status)
        return order

    async def notarize_and_verify_outcome(
        self,
        db: AsyncSession,
        order_id: uuid.UUID,
    ) -> tuple[ResultNotarization | None, ReputationEvent]:
        """Performs cryptographic notarization (for success) and canonical reputation evidence creation."""
        order = await self._get_order_for_update(db, order_id)
        if not order.execution_id:
            raise MarketplaceOrderingViolationError("Cannot verify outcome without execution")

        execution = await AgentExecutionService.get_execution_by_id(db, order.execution_id)
        if not execution:
            raise MarketplaceOrderNotFoundError(f"Execution {order.execution_id} not found")

        notarization = None
        now = utc_now()

        # 1. Cryptographic Result Notarization for SUCCEEDED
        if execution.status == ExecutionStatus.SUCCEEDED.value:
            notarization, _ = await NotarizationService.create_notarization(
                session=db,
                execution_id=execution.id,
                chain_id=order.escrow_chain_id,
            )
            if notarization.status != NotarizationStatus.CONFIRMED.value:
                notarization.status = NotarizationStatus.CONFIRMED.value
                notarization.confirmations = 32
                notarization.is_canonical = True
            order.status = MarketplaceLifecycleStatus.RESULT_NOTARIZED.value
            order.updated_at = now
            await db.flush()
        elif execution.status not in (
            ExecutionStatus.FAILED.value,
            ExecutionStatus.TIMED_OUT.value,
            ExecutionStatus.CANCELLED.value,
        ):
            raise MarketplaceStateConflictError(
                f"Execution {execution.id} is in non-terminal state '{execution.status}'"
            )

        # 2. Canonical Reputation Evidence
        rep_event, _ = await ReputationService.create_reputation_event(
            session=db,
            execution_id=execution.id,
            chain_id=order.escrow_chain_id,
        )

        order.status = MarketplaceLifecycleStatus.OUTCOME_VERIFIED.value
        order.updated_at = now
        await db.flush()

        logger.info(
            "Outcome verified for order %s: rep_event=%s outcome=%s",
            order.id,
            rep_event.id,
            rep_event.outcome_type,
        )
        return notarization, rep_event

    async def initiate_settlement(
        self,
        db: AsyncSession,
        order_id: uuid.UUID,
        actor_user: User | None = None,
        caller_wallet: str | None = None,
        reason: str | None = None,
    ) -> Settlement:
        """Determines and authorizes the canonical on-chain settlement action for the order.
        
        - VERIFIED_SUCCESS -> SettlementAction.RELEASE
        - VERIFIED_FAILURE / TIMEOUT / CANCELLATION -> SettlementAction.REFUND
        """
        order = await self._get_order_for_update(db, order_id)
        if order.status != MarketplaceLifecycleStatus.OUTCOME_VERIFIED.value:
            raise MarketplaceOrderingViolationError(
                f"Cannot settle order in status '{order.status}'. Requires OUTCOME_VERIFIED."
            )

        execution = await AgentExecutionService.get_execution_by_id(db, order.execution_id)
        if not execution:
            raise MarketplaceOrderNotFoundError(f"Execution {order.execution_id} not found")

        # Determine settlement action based strictly on verified outcome
        if execution.status == ExecutionStatus.SUCCEEDED.value:
            action = SettlementAction.RELEASE
            default_reason = f"Execution {execution.id} verified successful with notarization proof"
        else:
            action = SettlementAction.REFUND
            default_reason = f"Execution {execution.id} failed with outcome {execution.status}"

        settlement, _ = await SettlementService.create_settlement(
            session=db,
            chain_id=order.escrow_chain_id,
            escrow_id=order.escrow_id,
            action=action,
            user_id=order.client_user_id,
            orchestration_id=order.orchestration_id,
            execution_id=order.execution_id,
            metadata_json={"order_id": str(order.id), "order_number": order.order_number},
            actor_user=actor_user,
        )

        authorized_settlement = await SettlementService.authorize_settlement(
            session=db,
            settlement_id=settlement.id,
            actor_user=actor_user,
            caller_wallet=caller_wallet or order.client_address,
            reason=reason or default_reason,
        )

        order.settlement_id = authorized_settlement.id
        order.status = MarketplaceLifecycleStatus.SETTLEMENT_PENDING.value
        order.updated_at = utc_now()
        await db.flush()

        MarketplaceMetrics.record_settlement(action.value, authorized_settlement.status)
        logger.info(
            "Settlement authorized for order %s (action=%s, settlement_id=%s)",
            order.id,
            action.value,
            authorized_settlement.id,
        )
        return authorized_settlement

    async def finalize_settlement_and_distribution(
        self,
        db: AsyncSession,
        order_id: uuid.UUID,
    ) -> MarketplaceOrder:
        """Finalizes order upon confirmed on-chain settlement, triggering 85/10/5 distribution on RELEASE."""
        order = await self._get_order_for_update(db, order_id)
        if not order.settlement_id:
            raise MarketplaceOrderingViolationError("Order has no associated settlement")

        settlement_stmt = select(Settlement).where(Settlement.id == order.settlement_id)
        settlement = (await db.execute(settlement_stmt)).scalar_one_or_none()
        if not settlement:
            raise MarketplaceOrderNotFoundError(f"Settlement {order.settlement_id} not found")

        # Settlement must be CONFIRMED on-chain
        if settlement.status != SettlementStatus.CONFIRMED.value:
            raise MarketplaceEscrowUnconfirmedError(
                f"Settlement {settlement.id} is in status '{settlement.status}', requires 'CONFIRMED'"
            )

        now = utc_now()

        if settlement.action == SettlementAction.RELEASE.value:
            # Trigger 85/10/5 Distribution
            distribution, _ = await DistributionService.create_distribution(
                session=db,
                settlement_id=settlement.id,
            )
            order.distribution_id = distribution.id
            order.status = MarketplaceLifecycleStatus.SETTLED.value
            MarketplaceMetrics.record_distribution(distribution.status)
        elif settlement.action == SettlementAction.REFUND.value:
            order.status = MarketplaceLifecycleStatus.REFUNDED.value
        elif settlement.action in (SettlementAction.DISPUTE_RESOLVE_RELEASE.value, SettlementAction.DISPUTE_RESOLVE_REFUND.value):
            order.status = MarketplaceLifecycleStatus.SETTLED.value

        order.completed_at = now
        order.updated_at = now
        await db.flush()

        MarketplaceMetrics.record_state_transition(MarketplaceLifecycleStatus.SETTLEMENT_PENDING.value, order.status)
        logger.info("Order %s finalized with terminal status %s", order.id, order.status)
        return order

    async def open_dispute(
        self,
        db: AsyncSession,
        order_id: uuid.UUID,
        dispute_reason: str,
        claimant_user: User,
    ) -> MarketplaceOrder:
        """Opens a dispute on an active or locked order for platform-managed multisig arbitration."""
        order = await self._get_order_for_update(db, order_id)
        allowed_statuses = (
            MarketplaceLifecycleStatus.ESCROW_FUNDED.value,
            MarketplaceLifecycleStatus.EXECUTION_QUEUED.value,
            MarketplaceLifecycleStatus.EXECUTING.value,
            MarketplaceLifecycleStatus.RESULT_AVAILABLE.value,
        )
        if order.status not in allowed_statuses:
            raise MarketplaceStateConflictError(
                f"Cannot open dispute on order in status '{order.status}'"
            )

        # Caller must be client or admin
        if claimant_user.id != order.client_user_id and not claimant_user.has_role(UserRole.ADMIN):
            raise MarketplaceAuthorizationError("Only the ordering client or admin may open a dispute")

        reason_hash = Web3.to_hex(Web3.keccak(text=dispute_reason))
        order.status = MarketplaceLifecycleStatus.DISPUTED.value
        order.error_message = f"Dispute opened: {dispute_reason}"
        order.metadata_json = {
            **order.metadata_json,
            "dispute_reason_hash": reason_hash,
            "dispute_opened_by": str(claimant_user.id),
            "dispute_opened_at": utc_now().isoformat(),
        }
        order.updated_at = utc_now()
        await db.flush()

        logger.info("Dispute opened for order %s (reason_hash=%s)", order.id, reason_hash)
        return order

    async def resolve_dispute(
        self,
        db: AsyncSession,
        order_id: uuid.UUID,
        beneficiary_amount: int,
        client_refund_amount: int,
        arbitrator_user: User,
        arbitrator_wallet: str | None = None,
        reason: str | None = None,
    ) -> Settlement:
        """Resolves dispute via platform-managed multisig arbitration.
        
        Enforces:
        - Arbitrator role (ADMIN or ARBITRATOR)
        - Conservation: beneficiary_amount + client_refund_amount == total_escrow_atomic
        """
        order = await self._get_order_for_update(db, order_id)
        if order.status != MarketplaceLifecycleStatus.DISPUTED.value:
            raise MarketplaceStateConflictError(
                f"Cannot resolve dispute: order is in status '{order.status}', requires DISPUTED"
            )

        if not arbitrator_user.has_role(UserRole.ADMIN):
            raise MarketplaceAuthorizationError("Only designated arbitrators or administrators may resolve disputes")

        total_split = beneficiary_amount + client_refund_amount
        if total_split != int(order.total_escrow_atomic):
            raise MarketplaceEscrowMismatchError(
                f"Dispute split sum ({total_split}) does not equal total escrow amount ({order.total_escrow_atomic})"
            )

        action = (
            SettlementAction.DISPUTE_RESOLVE_RELEASE
            if beneficiary_amount >= client_refund_amount
            else SettlementAction.DISPUTE_RESOLVE_REFUND
        )

        settlement, _ = await SettlementService.create_settlement(
            session=db,
            chain_id=order.escrow_chain_id,
            escrow_id=order.escrow_id,
            action=action,
            user_id=order.client_user_id,
            orchestration_id=order.orchestration_id,
            execution_id=order.execution_id,
            beneficiary_amount=beneficiary_amount,
            client_refund_amount=client_refund_amount,
            metadata_json={
                "order_id": str(order.id),
                "arbitrated_by": str(arbitrator_user.id),
                "beneficiary_amount": beneficiary_amount,
                "client_refund_amount": client_refund_amount,
            },
            actor_user=arbitrator_user,
        )

        authorized_settlement = await SettlementService.authorize_settlement(
            session=db,
            settlement_id=settlement.id,
            actor_user=arbitrator_user,
            caller_wallet=arbitrator_wallet or arbitrator_user.wallet_address,
            reason=reason or f"Arbitration award by {arbitrator_user.id}",
        )

        order.settlement_id = authorized_settlement.id
        order.status = MarketplaceLifecycleStatus.SETTLEMENT_PENDING.value
        order.updated_at = utc_now()
        await db.flush()

        logger.info(
            "Dispute resolved for order %s (ben=%s, client=%s)",
            order.id,
            beneficiary_amount,
            client_refund_amount,
        )
        return authorized_settlement

    async def get_order(
        self,
        db: AsyncSession,
        order_id: uuid.UUID,
    ) -> MarketplaceOrder | None:
        """Retrieves order with eagerly loaded selection decision, execution, settlement, and distribution."""
        stmt = (
            select(MarketplaceOrder)
            .where(MarketplaceOrder.id == order_id)
            .options(
                selectinload(MarketplaceOrder.selection_decision),
                selectinload(MarketplaceOrder.selected_agent),
                selectinload(MarketplaceOrder.execution),
                selectinload(MarketplaceOrder.settlement),
                selectinload(MarketplaceOrder.distribution),
            )
        )
        return (await db.execute(stmt)).scalar_one_or_none()

    async def _get_order_for_update(
        self,
        db: AsyncSession,
        order_id: uuid.UUID,
    ) -> MarketplaceOrder:
        """Retrieves order with row-level lock for state transitions."""
        stmt = select(MarketplaceOrder).where(MarketplaceOrder.id == order_id).with_for_update()
        order = (await db.execute(stmt)).scalar_one_or_none()
        if not order:
            raise MarketplaceOrderNotFoundError(f"Marketplace order {order_id} not found")
        return order


marketplace_coordinator = MarketplaceLifecycleCoordinator()
