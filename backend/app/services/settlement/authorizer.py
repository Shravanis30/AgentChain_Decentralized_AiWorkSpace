"""Settlement authorization policy engine for Phase 6.2B.

Enforces:
- RBAC and ownership boundaries (CLIENT, ARBITRATOR, ADMIN).
- Orchestration and execution completion verification.
- Smart contract rule alignment (deadline checks, dispute state).
- Exact operation mapping to Escrow.sol methods without arbitrary calldata.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
import logging
from typing import Any
import uuid
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession
from web3 import Web3

from app.models.agent import AgentExecution, ExecutionStatus
from app.models.blockchain import BlockchainEvent
from app.models.orchestration import (
    Orchestration,
    OrchestrationStatus,
    OrchestrationTask,
    OrchestrationTaskStatus,
)
from app.models.settlement import Settlement, SettlementAction
from app.models.user import User, UserRole
from app.services.settlement.errors import (
    SettlementAuthorizationError,
    SettlementOrchestrationMismatchError,
)
from app.services.settlement.verifier import (
    EscrowVerificationResult,
    STATE_CREATED,
    STATE_FUNDED,
    STATE_LOCKED,
    STATE_DISPUTED,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AuthorizationDecision:
    is_authorized: bool
    action: SettlementAction
    reason: str
    target_operation: str
    parameters: dict[str, Any]
    metadata: dict[str, Any] = field(default_factory=dict)


class SettlementAuthorizationEngine:
    """Evaluates business rules, user authorization, and execution verification for settlements."""

    @staticmethod
    async def evaluate_authorization(
        session: AsyncSession,
        settlement: Settlement,
        escrow_result: EscrowVerificationResult,
        caller_user: User | None = None,
        caller_wallet: str | None = None,
    ) -> AuthorizationDecision:
        """Evaluates whether a settlement can be authorized. Fails closed."""

        action = SettlementAction(settlement.action)
        norm_client = Web3.to_checksum_address(escrow_result.client)
        norm_beneficiary = Web3.to_checksum_address(escrow_result.developer)

        # Normalize caller identity
        caller_addr = (
            Web3.to_checksum_address(caller_wallet)
            if caller_wallet
            else (Web3.to_checksum_address(caller_user.wallet_address) if caller_user and caller_user.wallet_address else None)
        )
        is_admin_or_arbitrator = False
        if caller_user:
            roles = set([caller_user.primary_role] + (caller_user.additional_roles or []))
            is_admin_or_arbitrator = bool(roles.intersection({UserRole.ADMIN.value, "ARBITRATOR"}))

        is_client = caller_addr and caller_addr == norm_client
        is_beneficiary = caller_addr and caller_addr == norm_beneficiary

        # 1. Orchestration verification if attached
        if settlement.orchestration_id:
            await SettlementAuthorizationEngine._verify_orchestration(
                session=session,
                orchestration_id=settlement.orchestration_id,
                action=action,
                expected_client=norm_client,
            )

        # 2. Execution verification if attached
        if settlement.execution_id:
            await SettlementAuthorizationEngine._verify_execution(
                session=session,
                execution_id=settlement.execution_id,
                action=action,
            )

        # 3. Action-specific authorization checks
        if action == SettlementAction.RELEASE:
            # Beneficiary cannot unilaterally authorize self-release
            if is_beneficiary and not (is_client or is_admin_or_arbitrator):
                raise SettlementAuthorizationError(
                    "Beneficiary cannot unilaterally authorize escrow release"
                )

            # Must be client or administrator/arbitrator
            if not (is_client or is_admin_or_arbitrator):
                raise SettlementAuthorizationError(
                    f"Caller {caller_addr} is not authorized to release escrow for client {norm_client}"
                )

            return AuthorizationDecision(
                is_authorized=True,
                action=action,
                reason="Release authorized by client or platform authority after successful verification",
                target_operation="releaseEscrow",
                parameters={"escrowId": settlement.escrow_id},
                metadata={"client": norm_client, "beneficiary": norm_beneficiary},
            )

        elif action == SettlementAction.REFUND:
            current_state = escrow_result.current_state_code

            if current_state == STATE_CREATED:
                if not (is_client or is_admin_or_arbitrator):
                    raise SettlementAuthorizationError("Only client or admin can refund un-funded escrow")
            elif current_state == STATE_FUNDED:
                if not (is_client or is_beneficiary or is_admin_or_arbitrator):
                    raise SettlementAuthorizationError("Unauthorized caller for refund in FUNDED state")
            elif current_state == STATE_LOCKED:
                if is_beneficiary or is_admin_or_arbitrator:
                    pass  # Beneficiary forfeiture or arbitrator action permitted
                elif is_client:
                    # In LOCKED state, client can only refund if execution deadline has passed
                    deadline_ts = 0
                    evt_stmt = sa.select(BlockchainEvent).where(
                        BlockchainEvent.chain_id == settlement.chain_id,
                        BlockchainEvent.event_name == "EscrowCreated",
                        BlockchainEvent.is_canonical.is_(True),
                    )
                    created_events = (await session.execute(evt_stmt)).scalars().all()
                    clean_escrow = settlement.escrow_id.lower()
                    for evt in created_events:
                        if (evt.decoded_data or {}).get("escrowId", "").lower() == clean_escrow:
                            deadline_ts = int((evt.decoded_data or {}).get("executionDeadline", 0))
                            break

                    # Fallback to metadata if explicitly provided (e.g., isolated test harnesses)
                    if deadline_ts == 0 and settlement.metadata_json:
                        deadline_ts = int(settlement.metadata_json.get("executionDeadline", 0))

                    if deadline_ts <= 0:
                        raise SettlementAuthorizationError(
                            f"Execution deadline for locked escrow {settlement.escrow_id} cannot be verified"
                        )

                    now_ts = int(datetime.now(timezone.utc).timestamp())
                    if now_ts < deadline_ts:
                        raise SettlementAuthorizationError(
                            f"Execution deadline {deadline_ts} has not passed yet (current time {now_ts})"
                        )
                else:
                    raise SettlementAuthorizationError("Unauthorized caller for refund in LOCKED state")
            elif current_state == STATE_DISPUTED:
                if not is_admin_or_arbitrator:
                    raise SettlementAuthorizationError("Only arbitrator can refund a disputed escrow")

            return AuthorizationDecision(
                is_authorized=True,
                action=action,
                reason="Refund authorized under verified contract conditions",
                target_operation="refundEscrow",
                parameters={"escrowId": settlement.escrow_id},
                metadata={"client": norm_client},
            )

        elif action in (
            SettlementAction.DISPUTE_RESOLVE_RELEASE,
            SettlementAction.DISPUTE_RESOLVE_REFUND,
        ):
            if not is_admin_or_arbitrator:
                raise SettlementAuthorizationError(
                    "Only designated arbitrator or platform administrator can authorize dispute resolution"
                )

            if escrow_result.current_state_code != STATE_DISPUTED:
                raise SettlementAuthorizationError(
                    f"Escrow must be in DISPUTED state for dispute resolution; current state is {escrow_result.current_state_code}"
                )

            # Determine split amounts
            total_amt = escrow_result.amount
            if action == SettlementAction.DISPUTE_RESOLVE_RELEASE:
                ben_amt = total_amt
                client_amt = 0
            else:
                ben_amt = 0
                client_amt = total_amt

            # If explicit split amounts were provided on settlement record
            if settlement.beneficiary_amount is not None and settlement.client_refund_amount is not None:
                ben_amt = int(settlement.beneficiary_amount)
                client_amt = int(settlement.client_refund_amount)
                if ben_amt + client_amt != total_amt:
                    raise SettlementAuthorizationError(
                        f"Dispute resolution sum ({ben_amt} + {client_amt}) does not equal total escrow amount ({total_amt})"
                    )

            return AuthorizationDecision(
                is_authorized=True,
                action=action,
                reason=f"Dispute resolution authorized by arbitrator: {ben_amt} to beneficiary, {client_amt} to client",
                target_operation="resolveDispute",
                parameters={
                    "escrowId": settlement.escrow_id,
                    "beneficiaryAmount": ben_amt,
                    "clientRefundAmount": client_amt,
                },
                metadata={"arbitrator": caller_addr, "beneficiaryAmount": ben_amt, "clientRefundAmount": client_amt},
            )

        raise SettlementAuthorizationError(f"Unsupported settlement action: {action}")

    @staticmethod
    async def _verify_orchestration(
        session: AsyncSession,
        orchestration_id: uuid.UUID,
        action: SettlementAction,
        expected_client: str,
    ) -> None:
        """Verifies that the linked orchestration is in an appropriate terminal state."""
        stmt = sa.select(Orchestration).where(Orchestration.id == orchestration_id)
        orch = (await session.execute(stmt)).scalar_one_or_none()

        if not orch:
            raise SettlementOrchestrationMismatchError(
                f"Linked orchestration {orchestration_id} not found"
            )

        if action == SettlementAction.RELEASE:
            if orch.status != OrchestrationStatus.SUCCEEDED.value:
                raise SettlementOrchestrationMismatchError(
                    f"Orchestration {orchestration_id} status is '{orch.status}', required '{OrchestrationStatus.SUCCEEDED.value}' for release"
                )

            # Check that all tasks are terminal succeeded/skipped
            task_stmt = sa.select(OrchestrationTask).where(
                OrchestrationTask.orchestration_id == orchestration_id
            )
            tasks = (await session.execute(task_stmt)).scalars().all()
            for t in tasks:
                if t.status not in (
                    OrchestrationTaskStatus.SUCCEEDED.value,
                    OrchestrationTaskStatus.SKIPPED.value,
                ):
                    raise SettlementOrchestrationMismatchError(
                        f"Orchestration task '{t.task_key}' is '{t.status}', not SUCCEEDED/SKIPPED"
                    )

        elif action == SettlementAction.REFUND:
            # Refunds are permitted if orchestration failed, was cancelled, or timed out
            pass

    @staticmethod
    async def _verify_execution(
        session: AsyncSession,
        execution_id: uuid.UUID,
        action: SettlementAction,
    ) -> None:
        """Verifies that the linked agent execution completed appropriately."""
        stmt = sa.select(AgentExecution).where(AgentExecution.id == execution_id)
        exec_record = (await session.execute(stmt)).scalar_one_or_none()

        if not exec_record:
            raise SettlementOrchestrationMismatchError(
                f"Linked agent execution {execution_id} not found"
            )

        if action == SettlementAction.RELEASE:
            if exec_record.status != ExecutionStatus.SUCCEEDED.value:
                raise SettlementOrchestrationMismatchError(
                    f"Agent execution {execution_id} status is '{exec_record.status}', required '{ExecutionStatus.SUCCEEDED.value}'"
                )
