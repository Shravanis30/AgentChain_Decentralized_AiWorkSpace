"""Authoritative backend service for Verified Reputation Foundation.

Enforces:
- Fail-closed cryptographic evidence chain:
  Agent -> Agent Version -> Agent Execution -> Canonical Result Hash -> ResultNotary Proof -> Verified Outcome.
- Reorg-safe state derivation and strictly objective outcome classifications.
- Idempotent registration with deterministic collision-resistant keys.
- Integration with BlockchainTransactionIntent, BlockchainTxOutbox, and Redis.
"""

import logging
from typing import Any
import uuid
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent, AgentExecution, AgentVersion, ExecutionStatus
from app.models.audit import AuditEventType, AuditLog
from app.models.base import utc_now
from app.models.notarization import NotarizationStatus, ResultNotarization
from app.models.reputation import (
    ReputationEvent,
    ReputationHistory,
    ReputationOutcomeType,
    ReputationProfile,
    ReputationStatus,
)
from app.models.user import User
from app.schemas.reputation import (
    ReputationEventResponse,
    ReputationProfileResponse,
    ReputationVerifyResponse,
)
from app.services.blockchain.config import (
    CHAIN_ID_ANVIL,
    CHAIN_ID_BASE_MAINNET,
    get_chain_config,
)
from app.services.blockchain.errors import (
    ContractMismatchError,
    MainnetSubmissionBlockedError,
)
from app.services.blockchain.transaction_intent import TransactionIntentService
from app.services.reputation.errors import (
    AgentIdentityMismatchError,
    ConflictingReputationEventError,
    ExecutionNotFoundError,
    ExecutionNotTerminalError,
    InsufficientConfirmationsError,
    InvalidOutcomeClassificationError,
    MainnetReputationBlockedError,
    NotarizationNotCanonicalError,
    ReputationError,
    ResultHashMismatchError,
    ResultNotNotarizedError,
)
from app.services.reputation.metrics import ReputationMetrics
from app.services.reputation.utils import (
    canonicalize_evidence,
    compute_evidence_hash,
    compute_reputation_key,
    uuid_to_bytes32,
)

logger = logging.getLogger(__name__)

# Mapping from string outcome to Solidity uint8 enum
OUTCOME_TO_SOL_ENUM: dict[str, int] = {
    ReputationOutcomeType.VERIFIED_SUCCESS.value: 1,
    ReputationOutcomeType.VERIFIED_FAILURE.value: 2,
    ReputationOutcomeType.VERIFIED_TIMEOUT.value: 3,
    ReputationOutcomeType.VERIFIED_CANCELLATION.value: 4,
}


class ReputationService:
    """Manages creation, verification, and read-model projections of reputation evidence."""

    @staticmethod
    async def create_reputation_event(
        session: AsyncSession,
        execution_id: uuid.UUID,
        chain_id: int | None = None,
        idempotency_key: str | None = None,
        evidence_payload: dict[str, Any] | None = None,
        actor_user: User | None = None,
        client_ip: str | None = None,
    ) -> tuple[ReputationEvent, bool]:
        """Creates an on-chain reputation evidence intent and DB record for an execution.

        Returns:
            (ReputationEvent, was_newly_created)
        """
        target_chain_id = chain_id or CHAIN_ID_ANVIL

        # 1. Base Mainnet Safety Guard
        if target_chain_id == CHAIN_ID_BASE_MAINNET:
            ReputationMetrics.record_failed(target_chain_id, "mainnet_blocked")
            raise MainnetReputationBlockedError(
                "SECURITY VIOLATION: Reputation registration on Base Mainnet (8453) is strictly blocked."
            )

        # 2. Fetch execution with row lock
        stmt = (
            sa.select(AgentExecution)
            .where(AgentExecution.id == execution_id)
            .with_for_update()
        )
        execution = (await session.execute(stmt)).scalar_one_or_none()
        if not execution:
            raise ExecutionNotFoundError(f"AgentExecution {execution_id} not found")

        # 3. Execution Terminal State Verification (Section 5)
        exec_status = execution.status
        terminal_statuses = {
            ExecutionStatus.SUCCEEDED.value,
            ExecutionStatus.FAILED.value,
            ExecutionStatus.TIMED_OUT.value,
            ExecutionStatus.CANCELLED.value,
        }
        if exec_status not in terminal_statuses:
            ReputationMetrics.record_failed(target_chain_id, "not_terminal")
            raise ExecutionNotTerminalError(
                f"Execution {execution_id} status is '{exec_status}'. "
                "Only terminal executions can produce reputation evidence."
            )

        # 4. Objective Outcome Classification & ResultNotary Verification
        notarization_id: uuid.UUID | None = None
        result_hash: str | None = None

        if exec_status == ExecutionStatus.SUCCEEDED.value:
            outcome_type = ReputationOutcomeType.VERIFIED_SUCCESS.value
            if not execution.output_hash or len(execution.output_hash.removeprefix("0x")) != 64:
                raise ResultHashMismatchError(
                    f"Execution {execution_id} has missing or malformed output_hash: {execution.output_hash}"
                )
            clean_output_hash = execution.output_hash.removeprefix("0x").lower()

            # Query ResultNotary proof
            notary_stmt = (
                sa.select(ResultNotarization)
                .where(
                    ResultNotarization.chain_id == target_chain_id,
                    ResultNotarization.execution_id == execution_id,
                )
                .with_for_update()
            )
            notarization = (await session.execute(notary_stmt)).scalar_one_or_none()
            if not notarization:
                ReputationMetrics.record_failed(target_chain_id, "not_notarized")
                raise ResultNotNotarizedError(
                    f"Execution {execution_id} has no ResultNotary proof on chain {target_chain_id}. "
                    "VERIFIED_SUCCESS requires confirmed on-chain ResultNotary proof."
                )

            # Cryptographic proof integrity checks
            if notarization.result_hash.lower() != clean_output_hash:
                ReputationMetrics.record_failed(target_chain_id, "hash_mismatch")
                raise ResultHashMismatchError(
                    f"ResultNotary hash {notarization.result_hash} does not match execution hash {clean_output_hash}"
                )

            if not notarization.is_canonical or notarization.status == NotarizationStatus.REORGED.value:
                ReputationMetrics.record_failed(target_chain_id, "not_canonical")
                raise NotarizationNotCanonicalError(
                    f"ResultNotary proof for execution {execution_id} is non-canonical or reorged."
                )

            if notarization.status != NotarizationStatus.CONFIRMED.value:
                ReputationMetrics.record_failed(target_chain_id, "not_confirmed")
                raise InsufficientConfirmationsError(
                    f"ResultNotary proof status is '{notarization.status}'. Must be CONFIRMED before reputation registration."
                )

            result_hash = clean_output_hash
            notarization_id = notarization.id

        elif exec_status == ExecutionStatus.FAILED.value:
            outcome_type = ReputationOutcomeType.VERIFIED_FAILURE.value
            result_hash = execution.output_hash.removeprefix("0x").lower() if execution.output_hash else None
        elif exec_status == ExecutionStatus.TIMED_OUT.value:
            outcome_type = ReputationOutcomeType.VERIFIED_TIMEOUT.value
        elif exec_status == ExecutionStatus.CANCELLED.value:
            outcome_type = ReputationOutcomeType.VERIFIED_CANCELLATION.value
        else:
            raise InvalidOutcomeClassificationError(f"Unsupported execution status: {exec_status}")

        # 5. Build Canonical Evidence Payload and SHA-256 Evidence Hash
        base_evidence = {
            "agent_id": str(execution.agent_id),
            "agent_version_id": str(execution.agent_version_id),
            "execution_id": str(execution.id),
            "outcome_type": outcome_type,
            "result_hash": result_hash,
            "status": exec_status,
        }
        if evidence_payload:
            base_evidence["diagnostics"] = evidence_payload
        evidence_hash = compute_evidence_hash(base_evidence)

        # 6. Build Deterministic Event Identity & Idempotency Keys (Section 6)
        rep_key = compute_reputation_key(
            chain_id=target_chain_id,
            agent_id=execution.agent_id,
            execution_id=execution.id,
            outcome_type=outcome_type,
            result_hash=result_hash,
            evidence_hash=evidence_hash,
        )
        final_idempotency_key = idempotency_key or f"rep:{target_chain_id}:{execution.id}"

        # 7. Check for Existing Reputation Event
        existing_stmt = (
            sa.select(ReputationEvent)
            .where(
                ReputationEvent.chain_id == target_chain_id,
                ReputationEvent.execution_id == execution_id,
            )
            .with_for_update()
        )
        existing = (await session.execute(existing_stmt)).scalar_one_or_none()
        if existing:
            if existing.reputation_key == rep_key:
                logger.info(
                    "Idempotent duplicate reputation event request for exec %s on chain %d",
                    execution_id,
                    target_chain_id,
                )
                ReputationMetrics.record_duplicate(target_chain_id)
                return existing, False
            else:
                ReputationMetrics.record_failed(target_chain_id, "conflict")
                raise ConflictingReputationEventError(
                    f"Execution {execution_id} already has a conflicting reputation event ({existing.reputation_key} != {rep_key})"
                )

        # 8. Resolve ReputationRegistry Contract Address
        chain_config = get_chain_config(target_chain_id)
        rep_contract_addr = chain_config.contract_addresses.get("ReputationRegistry", "")
        if not rep_contract_addr or rep_contract_addr == "0x0000000000000000000000000000000000000000":
            raise ContractMismatchError(
                f"ReputationRegistry contract address unconfigured on chain {target_chain_id}"
            )
        norm_rep_contract = rep_contract_addr.lower()

        # 9. Create BlockchainTransactionIntent & Outbox
        sol_outcome = OUTCOME_TO_SOL_ENUM[outcome_type]
        call_params = {
            "agentId": uuid_to_bytes32(execution.agent_id),
            "executionId": uuid_to_bytes32(execution.id),
            "agentVersionId": uuid_to_bytes32(execution.agent_version_id),
            "outcomeType": sol_outcome,
            "resultHash": "0x" + (result_hash or ("00" * 32)),
            "evidenceHash": "0x" + evidence_hash,
        }

        intent_idemp_key = f"intent:{final_idempotency_key}"[:128]
        intent, _ = await TransactionIntentService.create_intent(
            session=session,
            chain_id=target_chain_id,
            idempotency_key=intent_idemp_key,
            target_contract=norm_rep_contract,
            operation="registerReputationEvent",
            parameters=call_params,
        )

        # 10. Persist ReputationEvent Record
        rep_event = ReputationEvent(
            idempotency_key=final_idempotency_key,
            reputation_key=rep_key,
            agent_id=execution.agent_id,
            agent_version_id=execution.agent_version_id,
            execution_id=execution.id,
            outcome_type=outcome_type,
            result_hash=result_hash,
            notarization_id=notarization_id,
            evidence_hash=evidence_hash,
            chain_id=target_chain_id,
            contract_address=norm_rep_contract,
            status=ReputationStatus.PENDING.value,
            transaction_intent_id=intent.id,
            is_canonical=True,
            metadata_json={"evidence": base_evidence},
        )
        session.add(rep_event)
        await session.flush()

        # 11. Initial History Record
        history = ReputationHistory(
            reputation_event_id=rep_event.id,
            from_status="NONE",
            to_status=ReputationStatus.PENDING.value,
            reason="Reputation event intent queued for broadcast",
            metadata_json={"intent_id": str(intent.id)},
            created_at=utc_now(),
        )
        session.add(history)

        # 12. Write Structured Audit Log
        audit_log = AuditLog(
            user_id=actor_user.id if actor_user else None,
            event_type=AuditEventType.REPUTATION_EVENT_CREATED.value,
            ip_address=client_ip,
            metadata_json={
                "action": "reputation.event_created",
                "status": "SUCCESS",
                "reputation_event_id": str(rep_event.id),
                "agent_id": str(execution.agent_id),
                "execution_id": str(execution.id),
                "outcome_type": outcome_type,
                "result_hash": result_hash,
                "evidence_hash": evidence_hash,
                "chain_id": target_chain_id,
                "contract_address": norm_rep_contract,
            },
        )
        session.add(audit_log)

        ReputationMetrics.record_created(target_chain_id, outcome_type)
        logger.info(
            "Created reputation event %s for agent %s execution %s outcome %s on chain %d",
            rep_event.id,
            execution.agent_id,
            execution.id,
            outcome_type,
            target_chain_id,
        )
        return rep_event, True

    @staticmethod
    async def verify_reputation_event(
        session: AsyncSession,
        reputation_event_id: uuid.UUID,
    ) -> ReputationVerifyResponse:
        """Performs comprehensive verification of the reputation evidence chain."""
        stmt = (
            sa.select(ReputationEvent)
            .where(ReputationEvent.id == reputation_event_id)
        )
        rep_event = (await session.execute(stmt)).scalar_one_or_none()
        if not rep_event:
            raise ReputationError(f"ReputationEvent {reputation_event_id} not found")

        chain_config = get_chain_config(rep_event.chain_id)
        required_conf = chain_config.confirmations_required

        details: dict[str, Any] = {
            "reputation_key": rep_event.reputation_key,
            "chain_id": rep_event.chain_id,
            "contract_address": rep_event.contract_address,
            "outcome_type": rep_event.outcome_type,
            "status": rep_event.status,
            "is_canonical": rep_event.is_canonical,
            "confirmations": rep_event.confirmations,
            "confirmations_required": required_conf,
        }

        # Check execution
        exec_stmt = sa.select(AgentExecution).where(AgentExecution.id == rep_event.execution_id)
        execution = (await session.execute(exec_stmt)).scalar_one_or_none()
        if not execution:
            details["execution_check"] = "EXECUTION_NOT_FOUND"
            ReputationMetrics.record_verification_failed(rep_event.chain_id, "execution_not_found")
            return ReputationVerifyResponse(
                reputation_event_id=rep_event.id,
                verification_status="INVALIDATED",
                is_verified=False,
                agent_id=rep_event.agent_id,
                agent_version_id=rep_event.agent_version_id,
                execution_id=rep_event.execution_id,
                outcome_type=rep_event.outcome_type,
                result_hash=rep_event.result_hash,
                has_valid_notarization=False,
                chain_id=rep_event.chain_id,
                contract_address=rep_event.contract_address,
                confirmations=rep_event.confirmations,
                confirmations_required=required_conf,
                is_canonical=rep_event.is_canonical,
                details=details,
            )

        details["execution_status"] = execution.status
        details["agent_match"] = execution.agent_id == rep_event.agent_id
        details["version_match"] = execution.agent_version_id == rep_event.agent_version_id

        # Check ResultNotary proof if VERIFIED_SUCCESS
        has_valid_notarization = False
        notarization_status_val: str | None = None

        if rep_event.outcome_type == ReputationOutcomeType.VERIFIED_SUCCESS.value:
            notary_stmt = sa.select(ResultNotarization).where(
                ResultNotarization.chain_id == rep_event.chain_id,
                ResultNotarization.execution_id == rep_event.execution_id,
            )
            notarization = (await session.execute(notary_stmt)).scalar_one_or_none()
            if notarization:
                notarization_status_val = notarization.status
                has_valid_notarization = (
                    notarization.is_canonical
                    and notarization.status == NotarizationStatus.CONFIRMED.value
                    and (notarization.result_hash.lower() == (rep_event.result_hash or "").lower())
                )
            details["has_valid_notarization"] = has_valid_notarization
            details["notarization_status"] = notarization_status_val

        # Determine verification status
        if not rep_event.is_canonical or rep_event.status == ReputationStatus.REORGED.value:
            v_status = "REORGED"
            is_verified = False
        elif (
            details["agent_match"]
            and details["version_match"]
            and (
                rep_event.outcome_type != ReputationOutcomeType.VERIFIED_SUCCESS.value
                or has_valid_notarization
            )
            and rep_event.status == ReputationStatus.CONFIRMED.value
            and rep_event.confirmations >= required_conf
        ):
            v_status = "CONFIRMED"
            is_verified = True
        elif rep_event.status in (ReputationStatus.PENDING.value, ReputationStatus.SUBMITTED.value, ReputationStatus.CONFIRMING.value):
            v_status = "PENDING"
            is_verified = False
        else:
            v_status = "INVALIDATED"
            is_verified = False

        return ReputationVerifyResponse(
            reputation_event_id=rep_event.id,
            verification_status=v_status,
            is_verified=is_verified,
            agent_id=rep_event.agent_id,
            agent_version_id=rep_event.agent_version_id,
            execution_id=rep_event.execution_id,
            outcome_type=rep_event.outcome_type,
            result_hash=rep_event.result_hash,
            has_valid_notarization=has_valid_notarization,
            notarization_status=notarization_status_val,
            chain_id=rep_event.chain_id,
            contract_address=rep_event.contract_address,
            transaction_hash=str(rep_event.transaction_hash) if rep_event.transaction_hash else None,
            confirmations=rep_event.confirmations,
            confirmations_required=required_conf,
            is_canonical=rep_event.is_canonical,
            details=details,
        )

    @staticmethod
    async def get_reputation_event_by_id(
        session: AsyncSession,
        event_id: uuid.UUID,
    ) -> ReputationEvent | None:
        """Retrieves a ReputationEvent by its primary key ID."""
        stmt = sa.select(ReputationEvent).where(ReputationEvent.id == event_id)
        return (await session.execute(stmt)).scalar_one_or_none()

    @staticmethod
    async def get_reputation_event_by_execution(
        session: AsyncSession,
        execution_id: uuid.UUID,
        chain_id: int | None = None,
    ) -> ReputationEvent | None:
        """Retrieves a ReputationEvent by execution ID and optional chain ID."""
        target_chain_id = chain_id or CHAIN_ID_ANVIL
        stmt = sa.select(ReputationEvent).where(
            ReputationEvent.execution_id == execution_id,
            ReputationEvent.chain_id == target_chain_id,
        )
        return (await session.execute(stmt)).scalar_one_or_none()

    @staticmethod
    async def get_agent_profile(
        session: AsyncSession,
        agent_id: uuid.UUID,
        chain_id: int | None = None,
    ) -> ReputationProfile:
        """Retrieves or derives the current reputation profile for an agent."""
        target_chain_id = chain_id or CHAIN_ID_ANVIL
        from app.services.blockchain.reputation_projector import ReputationProjector

        projector = ReputationProjector(target_chain_id)
        profile = await projector.recalculate_profile(session, agent_id)
        return profile
