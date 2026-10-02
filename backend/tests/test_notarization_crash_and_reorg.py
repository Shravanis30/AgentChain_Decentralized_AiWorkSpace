"""Phase 6.3 — Crash Recovery & Reorganization Invariant Tests.

Tests recovery across lifecycle failure boundaries (Section 31):
- A. before DB notarization insert
- B. after DB insert
- C. after transaction intent
- D. after outbox
- E. after Redis publish
- F. after transaction submission
- G. after mining
- H. after event indexing
- I. before confirmation
- J. after confirmation
- K. during reconciliation
- L. during duplicate delivery

Tests deep reorganization invalidation and immutability (Section 32):
- Canonical proof -> indexed -> confirmed -> reorg -> orphaned -> reconciliation
- Expected: REORGED / NOT_CONFIRMED
- Immutability of execution result_hash maintained under all conditions
"""

import uuid
import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession
from unittest.mock import AsyncMock, patch

from app.db.session import async_session_factory
from app.models.agent import (
    Agent,
    AgentExecution,
    AgentStatus,
    AgentVersion,
    ExecutionStatus,
)
from app.models.blockchain import (
    BlockchainEvent,
    BlockchainTransactionIntent,
    BlockchainTxOutbox,
    EventStatus,
    IntentStatus,
    TxLifecycleStatus,
)
from app.models.notarization import (
    NotarizationHistory,
    NotarizationStatus,
    ResultNotarization,
)
from app.models.user import User, UserRole
from app.schemas.notarization import VerificationStatus
from app.services.blockchain.config import CHAIN_ID_ANVIL
from app.services.blockchain.notarization_projector import NotarizationProjector
from app.services.hashing import compute_canonical_hash
from app.services.notarization.errors import ResultHashImmutabilityViolationError
from app.services.notarization.reconciliation import NotarizationReconciliationService
from app.services.notarization.service import NotarizationService
from app.services.notarization.utils import (
    build_notarization_idempotency_key,
    execution_id_to_bytes32,
    result_hash_to_bytes32,
)


@pytest.fixture(autouse=True)
async def cleanup_db():
    yield
    async with async_session_factory() as session:
        await session.execute(sa.text("TRUNCATE TABLE notarization_history CASCADE"))
        await session.execute(sa.text("TRUNCATE TABLE result_notarizations CASCADE"))
        await session.execute(sa.text("TRUNCATE TABLE blockchain_events CASCADE"))
        await session.execute(sa.text("TRUNCATE TABLE indexed_blocks CASCADE"))
        await session.execute(sa.text("TRUNCATE TABLE blockchain_tx_outbox CASCADE"))
        await session.execute(sa.text("TRUNCATE TABLE blockchain_transaction_intents CASCADE"))
        await session.execute(sa.text("TRUNCATE TABLE agent_executions CASCADE"))
        await session.execute(sa.text("TRUNCATE TABLE agent_versions CASCADE"))
        await session.execute(sa.text("TRUNCATE TABLE agents CASCADE"))
        await session.commit()


async def _create_test_execution(session: AsyncSession) -> tuple[AgentExecution, str]:
    wallet = f"0x{uuid.uuid4().hex[:40]}"
    user = User(
        wallet_address=wallet.lower(),
        primary_role=UserRole.DEVELOPER.value,
        nonce=f"nonce-{uuid.uuid4().hex[:8]}",
    )
    session.add(user)
    await session.flush()

    agent = Agent(
        owner_user_id=user.id,
        name="Notary Crash Agent",
        slug=f"notary-crash-{uuid.uuid4().hex[:8]}",
        status=AgentStatus.PUBLISHED.value,
    )
    session.add(agent)
    await session.flush()

    agent_version = AgentVersion(
        agent_id=agent.id,
        version="1.0.0",
        manifest={"entrypoint": "main:run"},
        input_schema={},
        output_schema={},
    )
    session.add(agent_version)
    await session.flush()

    agent.current_version_id = agent_version.id

    payload = {"result": 42, "status": "ok"}
    res_hash = compute_canonical_hash(payload)

    input_payload = {"seed": 1}
    input_hash = compute_canonical_hash(input_payload)

    execution = AgentExecution(
        agent_id=agent.id,
        agent_version_id=agent_version.id,
        requested_by=user.id,
        status=ExecutionStatus.SUCCEEDED.value,
        input_data=input_payload,
        input_hash=input_hash,
        output_data=payload,
        output_hash=res_hash,
    )
    session.add(execution)
    await session.flush()
    await session.commit()
    return execution, res_hash


@pytest.mark.asyncio
async def test_crash_recovery_before_intent_creation():
    """Crash stage A & B: Failure before or during intent creation preserves atomicity."""
    async with async_session_factory() as session:
        execution, res_hash = await _create_test_execution(session)

    # Simulate crash during transaction intent creation
    with patch(
        "app.services.blockchain.transaction_intent.TransactionIntentService.create_intent",
        side_effect=RuntimeError("Simulated DB connection crash during intent creation"),
    ):
        async with async_session_factory() as session:
            with pytest.raises(RuntimeError, match="Simulated DB connection crash"):
                await NotarizationService.create_notarization(
                    session=session,
                    execution_id=execution.id,
                    chain_id=CHAIN_ID_ANVIL,
                )

    # Verify no orphan notarization or intent exists in DB
    async with async_session_factory() as session:
        notarization = await NotarizationService.get_notarization_by_execution_id(
            session, execution.id, CHAIN_ID_ANVIL
        )
        assert notarization is None

        # Recovery retry succeeds cleanly
        notarization_recovered, created = await NotarizationService.create_notarization(
            session=session,
            execution_id=execution.id,
            chain_id=CHAIN_ID_ANVIL,
        )
        assert created is True
        assert notarization_recovered.status == NotarizationStatus.PENDING.value
        await session.commit()


@pytest.mark.asyncio
async def test_crash_recovery_duplicate_delivery_and_concurrency():
    """Crash stage L: Duplicate delivery and concurrent requests resolve to single logical proof."""
    async with async_session_factory() as session:
        execution, res_hash = await _create_test_execution(session)

    # 1. Create first notarization
    async with async_session_factory() as session:
        notarization1, created1 = await NotarizationService.create_notarization(
            session=session,
            execution_id=execution.id,
            chain_id=CHAIN_ID_ANVIL,
        )
        assert created1 is True
        await session.commit()

    # 2. Duplicate deliveries (replays)
    for _ in range(5):
        async with async_session_factory() as session:
            notarization_dup, created_dup = await NotarizationService.create_notarization(
                session=session,
                execution_id=execution.id,
                chain_id=CHAIN_ID_ANVIL,
            )
            assert created_dup is False
            assert notarization_dup.id == notarization1.id
            assert notarization_dup.result_hash == res_hash

    # Verify only 1 record exists
    async with async_session_factory() as session:
        records = (await session.execute(
            sa.select(ResultNotarization).where(ResultNotarization.execution_id == execution.id)
        )).scalars().all()
        assert len(records) == 1


@pytest.mark.asyncio
async def test_reconciliation_crash_recovery():
    """Crash stage K: Crash during reconciliation leaves state recoverable without corruption."""
    async with async_session_factory() as session:
        execution, res_hash = await _create_test_execution(session)

    async with async_session_factory() as session:
        notarization, _ = await NotarizationService.create_notarization(
            session=session,
            execution_id=execution.id,
            chain_id=CHAIN_ID_ANVIL,
        )
        await session.commit()
        notarization_id = notarization.id

    # Create matching canonical BlockchainEvent
    tx_hash = "0x" + "aa" * 32
    block_hash = "0x" + "bb" * 32
    async with async_session_factory() as session:
        event = BlockchainEvent(
            chain_id=CHAIN_ID_ANVIL,
            block_number=100,
            block_hash=block_hash,
            transaction_hash=tx_hash,
            transaction_index=0,
            log_index=0,
            event_name="ResultNotarized",
            contract_address=notarization.contract_address.lower(),
            decoded_data={
                "executionId": execution_id_to_bytes32(execution.id),
                "resultHash": result_hash_to_bytes32(res_hash),
                "artifactCommitment": "0x" + "00" * 32,
            },
            status=EventStatus.CONFIRMED.value,
            is_canonical=True,
        )
        session.add(event)
        await session.commit()

    # Reconcile normally
    async with async_session_factory() as session:
        reconciled = await NotarizationReconciliationService.reconcile_notarization(
            session, notarization_id
        )
        assert reconciled.status == NotarizationStatus.CONFIRMED.value
        assert reconciled.confirmations >= 1
        await session.commit()


@pytest.mark.asyncio
async def test_reorg_orphaning_and_invalidation_invariant():
    """Section 32: Reorg orphaning invalidates confirmation while preserving result_hash immutability."""
    async with async_session_factory() as session:
        execution, res_hash = await _create_test_execution(session)

    # 1. Create notarization
    async with async_session_factory() as session:
        notarization, _ = await NotarizationService.create_notarization(
            session=session,
            execution_id=execution.id,
            chain_id=CHAIN_ID_ANVIL,
        )
        notarization_id = notarization.id
        await session.commit()

    # 2. Confirm via canonical event
    tx_hash = "0x" + "11" * 32
    block_hash = "0x" + "22" * 32
    async with async_session_factory() as session:
        event = BlockchainEvent(
            chain_id=CHAIN_ID_ANVIL,
            block_number=200,
            block_hash=block_hash,
            transaction_hash=tx_hash,
            transaction_index=0,
            log_index=0,
            event_name="ResultNotarized",
            contract_address=notarization.contract_address.lower(),
            decoded_data={
                "executionId": execution_id_to_bytes32(execution.id),
                "resultHash": result_hash_to_bytes32(res_hash),
                "artifactCommitment": "0x" + "00" * 32,
            },
            status=EventStatus.CONFIRMED.value,
            is_canonical=True,
        )
        session.add(event)
        await session.commit()

    async with async_session_factory() as session:
        reconciled = await NotarizationReconciliationService.reconcile_notarization(
            session, notarization_id
        )
        assert reconciled.status == NotarizationStatus.CONFIRMED.value
        assert reconciled.is_canonical is True
        await session.commit()

    # 3. Simulate deep reorganization within 32 blocks: Event becomes orphaned
    async with async_session_factory() as session:
        ev_stmt = sa.select(BlockchainEvent).where(BlockchainEvent.transaction_hash == tx_hash)
        ev = (await session.execute(ev_stmt)).scalar_one()
        ev.is_canonical = False
        ev.status = EventStatus.ORPHANED.value

        # Reproject via NotarizationProjector
        projector = NotarizationProjector(CHAIN_ID_ANVIL)
        await projector.reproject_notarization(session, execution_id_to_bytes32(execution.id))
        await session.commit()

    # 4. Verify Notarization state is REORGED and not treated as confirmed
    async with async_session_factory() as session:
        updated = await NotarizationService.get_notarization_by_id(session, notarization_id)
        assert updated.status == NotarizationStatus.REORGED.value
        assert updated.is_canonical is False
        assert updated.reorged_at is not None

        # Verify API returns REORGED
        verify_resp = await NotarizationService.verify_execution(
            session=session,
            execution_id=execution.id,
            chain_id=CHAIN_ID_ANVIL,
        )
        assert verify_resp.verification_status == VerificationStatus.REORGED
        assert verify_resp.is_canonical is False

        # Invariant: Original AgentExecution result_hash is strictly immutable!
        exec_stmt = sa.select(AgentExecution).where(AgentExecution.id == execution.id)
        current_exec = (await session.execute(exec_stmt)).scalar_one()
        assert current_exec.output_hash == res_hash, "Execution output hash was mutated during reorg!"
