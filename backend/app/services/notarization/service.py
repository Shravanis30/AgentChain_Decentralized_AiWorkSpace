"""Core business service for Cryptographic Result Notarization."""

import logging
from typing import Any
import uuid
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent, AgentExecution, AgentVersion, ExecutionStatus
from app.models.audit import AuditEventType, AuditLog
from app.models.base import utc_now
from app.models.notarization import (
    NotarizationHistory,
    NotarizationStatus,
    ResultNotarization,
)
from app.models.user import User
from app.schemas.notarization import (
    CanonicalNotarizationPayload,
    NotarizationVerifyResponse,
    VerificationStatus,
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
from app.services.hashing import compute_canonical_hash
from app.services.notarization.errors import (
    ExecutionNotEligibleError,
    ExecutionNotFoundError,
    NotarizationNotFoundError,
    ResultHashImmutabilityViolationError,
)
from app.services.notarization.metrics import NotarizationMetrics
from app.services.notarization.utils import (
    artifact_commitment_to_bytes32,
    build_notarization_idempotency_key,
    execution_id_to_bytes32,
    result_hash_to_bytes32,
)

logger = logging.getLogger(__name__)


class NotarizationService:
    """Manages creation, immutable anchoring, and verification of result notarizations."""

    @staticmethod
    async def create_notarization(
        session: AsyncSession,
        execution_id: uuid.UUID,
        chain_id: int | None = None,
        artifact_reference: str | None = None,
        idempotency_key: str | None = None,
        actor_user: User | None = None,
        client_ip: str | None = None,
    ) -> tuple[ResultNotarization, bool]:
        """Creates an on-chain notarization intent and database projection for an execution.

        Returns:
            (ResultNotarization, was_newly_created)
        """
        target_chain_id = chain_id or CHAIN_ID_ANVIL
        NotarizationMetrics.record_requested(target_chain_id)

        # 1. Base Mainnet Safety Guard
        if target_chain_id == CHAIN_ID_BASE_MAINNET:
            NotarizationMetrics.record_failed(target_chain_id, "mainnet_blocked")
            raise MainnetSubmissionBlockedError(
                "Result notarization on Base Mainnet (8453) is strictly blocked."
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

        # 3. Execution Eligibility Validations (Section 9)
        if execution.status != ExecutionStatus.SUCCEEDED.value:
            NotarizationMetrics.record_failed(target_chain_id, "not_succeeded")
            raise ExecutionNotEligibleError(
                f"Execution {execution_id} status is '{execution.status}'. "
                "Only SUCCEEDED executions are eligible for result notarization."
            )

        if execution.cancellation_requested or execution.cancelled_at:
            NotarizationMetrics.record_failed(target_chain_id, "cancelled")
            raise ExecutionNotEligibleError(
                f"Execution {execution_id} was cancelled. Cannot notarize cancelled executions."
            )

        if not execution.output_hash or len(execution.output_hash.removeprefix("0x")) != 64:
            NotarizationMetrics.record_failed(target_chain_id, "invalid_output_hash")
            raise ExecutionNotEligibleError(
                f"Execution {execution_id} has missing or malformed output_hash: {execution.output_hash}"
            )

        clean_result_hash = execution.output_hash.removeprefix("0x").lower()

        # 4. Resolve Chain Config and ResultNotary contract address
        chain_config = get_chain_config(target_chain_id)
        notary_address = chain_config.contract_addresses.get("ResultNotary", "")
        if not notary_address or notary_address == "0x0000000000000000000000000000000000000000":
            raise ContractMismatchError(
                f"ResultNotary contract address is unconfigured on chain {target_chain_id}"
            )
        norm_notary_addr = notary_address.lower()

        # 5. Build Deterministic Identity & Idempotency Keys (Section 12)
        notarization_key = (
            f"notarization:{target_chain_id}:{norm_notary_addr}:{execution.id}:{clean_result_hash}"
        )
        idemp_key = (
            idempotency_key
            or build_notarization_idempotency_key(
                target_chain_id, norm_notary_addr, execution.id, clean_result_hash
            )
        )

        # 6. Check existing notarization for execution on this chain
        existing_stmt = sa.select(ResultNotarization).where(
            sa.or_(
                ResultNotarization.notarization_key == notarization_key,
                sa.and_(
                    ResultNotarization.chain_id == target_chain_id,
                    ResultNotarization.execution_id == execution.id,
                ),
                sa.and_(
                    ResultNotarization.chain_id == target_chain_id,
                    ResultNotarization.idempotency_key == idemp_key,
                ),
            )
        )
        existing = (await session.execute(existing_stmt)).scalar_one_or_none()
        if existing:
            # Result Hash Immutability (Section 4): verify hash hasn't mutated
            if existing.result_hash.lower() != clean_result_hash:
                NotarizationMetrics.record_verification_mismatch(target_chain_id)
                raise ResultHashImmutabilityViolationError(
                    f"Result hash conflict for execution {execution.id}: "
                    f"existing='{existing.result_hash}', requested='{clean_result_hash}'. "
                    "Canonical result hashes are strictly immutable."
                )

            logger.info("Notarization %s already exists for execution %s", existing.id, execution.id)
            NotarizationMetrics.record_duplicate(target_chain_id)
            return existing, False

        # 7. Construct Canonical Notarization Envelope (Section 3)
        # Fetch agent version metadata
        agent_version_stmt = sa.select(AgentVersion).where(AgentVersion.id == execution.agent_version_id)
        agent_version = (await session.execute(agent_version_stmt)).scalar_one_or_none()
        version_str = agent_version.version if agent_version else "1.0.0"

        canonical_payload = CanonicalNotarizationPayload(
            protocol_version="1.0",
            execution_id=str(execution.id),
            agent_id=str(execution.agent_id),
            agent_version=version_str,
            result_hash=clean_result_hash,
            hash_algorithm="SHA-256",
            canonicalization="RFC-8785",
            artifact_reference=artifact_reference,
            completed_at=execution.completed_at.isoformat() if execution.completed_at else None,
        )

        artifact_commitment = canonical_payload.compute_commitment_hash()

        # 8. Create Blockchain Transaction Intent via standard pipeline
        intent_params = {
            "executionId": execution_id_to_bytes32(execution.id),
            "resultHash": result_hash_to_bytes32(clean_result_hash),
            "artifactCommitment": artifact_commitment_to_bytes32(artifact_commitment),
        }
        intent_idemp_key = f"intent:{idemp_key}"[:128]
        intent, _ = await TransactionIntentService.create_intent(
            session=session,
            chain_id=target_chain_id,
            idempotency_key=intent_idemp_key,
            target_contract=norm_notary_addr,
            operation="notarizeResult",
            parameters=intent_params,
        )

        # 9. Create ResultNotarization record
        now = utc_now()
        notarization = ResultNotarization(
            idempotency_key=idemp_key,
            notarization_key=notarization_key,
            execution_id=execution.id,
            chain_id=target_chain_id,
            contract_address=norm_notary_addr,
            result_hash=clean_result_hash,
            hash_algorithm="SHA-256",
            canonicalization_version="RFC-8785",
            artifact_reference=artifact_reference,
            artifact_commitment=artifact_commitment,
            status=NotarizationStatus.PENDING.value,
            transaction_intent_id=intent.id,
            transaction_hash=None,
            block_number=None,
            block_hash=None,
            confirmations=0,
            is_canonical=True,
            payload_json=canonical_payload.model_dump(),
            created_at=now,
            updated_at=now,
        )
        session.add(notarization)
        await session.flush()

        # 10. Audit History Entry
        history = NotarizationHistory(
            notarization_id=notarization.id,
            from_status=NotarizationStatus.PENDING.value,
            to_status=NotarizationStatus.PENDING.value,
            reason="Result notarization intent created and queued to outbox",
            metadata_json={
                "intent_id": str(intent.id),
                "result_hash": clean_result_hash,
                "artifact_commitment": artifact_commitment,
            },
            created_at=now,
        )
        session.add(history)

        # 11. System Audit Log
        if actor_user:
            audit = AuditLog(
                actor_id=actor_user.id,
                action="RESULT_NOTARIZATION_REQUESTED",
                resource_type="ResultNotarization",
                resource_id=notarization.id,
                ip_address=client_ip,
                details={
                    "execution_id": str(execution.id),
                    "chain_id": target_chain_id,
                    "result_hash": clean_result_hash,
                },
                created_at=now,
            )
            session.add(audit)

        NotarizationMetrics.record_created(target_chain_id)
        return notarization, True

    @staticmethod
    async def get_notarization_by_id(
        session: AsyncSession,
        notarization_id: uuid.UUID,
    ) -> ResultNotarization | None:
        """Retrieves notarization by primary key ID."""
        stmt = sa.select(ResultNotarization).where(ResultNotarization.id == notarization_id)
        return (await session.execute(stmt)).scalar_one_or_none()

    @staticmethod
    async def get_notarization_by_execution_id(
        session: AsyncSession,
        execution_id: uuid.UUID,
        chain_id: int | None = None,
    ) -> ResultNotarization | None:
        """Retrieves notarization by execution ID and optional chain ID."""
        stmt = sa.select(ResultNotarization).where(ResultNotarization.execution_id == execution_id)
        if chain_id is not None:
            stmt = stmt.where(ResultNotarization.chain_id == chain_id)
        return (await session.execute(stmt)).scalar_one_or_none()

    @staticmethod
    async def verify_execution(
        session: AsyncSession,
        execution_id: uuid.UUID,
        result_payload: dict[str, Any] | None = None,
        result_hash: str | None = None,
        chain_id: int | None = None,
    ) -> NotarizationVerifyResponse:
        """Performs cryptographic and on-chain verification of an execution result proof (Section 20)."""
        target_chain_id = chain_id or CHAIN_ID_ANVIL
        now = utc_now()

        notarization = await NotarizationService.get_notarization_by_execution_id(
            session, execution_id, target_chain_id
        )

        if not notarization:
            return NotarizationVerifyResponse(
                execution_id=execution_id,
                verification_status=VerificationStatus.NOT_NOTARIZED,
                result_hash=None,
                computed_hash=None,
                onchain_hash=None,
                hash_algorithm="SHA-256",
                canonicalization_version="RFC-8785",
                chain_id=target_chain_id,
                contract_address="",
                transaction_hash=None,
                block_number=None,
                confirmations=0,
                is_canonical=False,
                notarized_at=None,
                verified_at=now,
                details=f"No notarization proof found for execution {execution_id} on chain {target_chain_id}",
            )

        # Reorg check
        if not notarization.is_canonical or notarization.status == NotarizationStatus.REORGED.value:
            NotarizationMetrics.record_reorged(target_chain_id)
            return NotarizationVerifyResponse(
                execution_id=execution_id,
                verification_status=VerificationStatus.REORGED,
                result_hash=notarization.result_hash,
                computed_hash=None,
                onchain_hash=notarization.result_hash,
                hash_algorithm=notarization.hash_algorithm,
                canonicalization_version=notarization.canonicalization_version,
                chain_id=target_chain_id,
                contract_address=notarization.contract_address,
                transaction_hash=notarization.transaction_hash,
                block_number=notarization.block_number,
                confirmations=notarization.confirmations,
                is_canonical=False,
                notarized_at=notarization.confirmed_at,
                verified_at=now,
                details="Notarization was invalidated by a blockchain reorganization",
            )

        # Confirmation check
        if notarization.status != NotarizationStatus.CONFIRMED.value:
            return NotarizationVerifyResponse(
                execution_id=execution_id,
                verification_status=VerificationStatus.NOT_CONFIRMED,
                result_hash=notarization.result_hash,
                computed_hash=None,
                onchain_hash=notarization.result_hash,
                hash_algorithm=notarization.hash_algorithm,
                canonicalization_version=notarization.canonicalization_version,
                chain_id=target_chain_id,
                contract_address=notarization.contract_address,
                transaction_hash=notarization.transaction_hash,
                block_number=notarization.block_number,
                confirmations=notarization.confirmations,
                is_canonical=notarization.is_canonical,
                notarized_at=notarization.confirmed_at,
                verified_at=now,
                details=f"Proof status is {notarization.status} (confirmations={notarization.confirmations})",
            )

        # Hash computation & comparison
        computed_hash: str | None = None
        if result_payload is not None:
            computed_hash = compute_canonical_hash(result_payload)
            if computed_hash.lower() != notarization.result_hash.lower():
                NotarizationMetrics.record_verification_mismatch(target_chain_id)
                return NotarizationVerifyResponse(
                    execution_id=execution_id,
                    verification_status=VerificationStatus.HASH_MISMATCH,
                    result_hash=notarization.result_hash,
                    computed_hash=computed_hash,
                    onchain_hash=notarization.result_hash,
                    hash_algorithm=notarization.hash_algorithm,
                    canonicalization_version=notarization.canonicalization_version,
                    chain_id=target_chain_id,
                    contract_address=notarization.contract_address,
                    transaction_hash=notarization.transaction_hash,
                    block_number=notarization.block_number,
                    confirmations=notarization.confirmations,
                    is_canonical=notarization.is_canonical,
                    notarized_at=notarization.confirmed_at,
                    verified_at=now,
                    details=f"Recomputed hash '{computed_hash}' does not match on-chain hash '{notarization.result_hash}'",
                )

        if result_hash is not None:
            clean_input_hash = result_hash.removeprefix("0x").lower()
            if clean_input_hash != notarization.result_hash.lower():
                NotarizationMetrics.record_verification_mismatch(target_chain_id)
                return NotarizationVerifyResponse(
                    execution_id=execution_id,
                    verification_status=VerificationStatus.HASH_MISMATCH,
                    result_hash=notarization.result_hash,
                    computed_hash=clean_input_hash,
                    onchain_hash=notarization.result_hash,
                    hash_algorithm=notarization.hash_algorithm,
                    canonicalization_version=notarization.canonicalization_version,
                    chain_id=target_chain_id,
                    contract_address=notarization.contract_address,
                    transaction_hash=notarization.transaction_hash,
                    block_number=notarization.block_number,
                    confirmations=notarization.confirmations,
                    is_canonical=notarization.is_canonical,
                    notarized_at=notarization.confirmed_at,
                    verified_at=now,
                    details=f"Provided hash '{clean_input_hash}' does not match on-chain hash '{notarization.result_hash}'",
                )

        # VERIFIED
        return NotarizationVerifyResponse(
            execution_id=execution_id,
            verification_status=VerificationStatus.VERIFIED,
            result_hash=notarization.result_hash,
            computed_hash=computed_hash or notarization.result_hash,
            onchain_hash=notarization.result_hash,
            hash_algorithm=notarization.hash_algorithm,
            canonicalization_version=notarization.canonicalization_version,
            chain_id=target_chain_id,
            contract_address=notarization.contract_address,
            transaction_hash=notarization.transaction_hash,
            block_number=notarization.block_number,
            confirmations=notarization.confirmations,
            is_canonical=notarization.is_canonical,
            notarized_at=notarization.confirmed_at,
            verified_at=now,
            details="Cryptographic result hash successfully verified against canonical blockchain proof",
        )
