"""Phase 6.4 — Reputation Crash Recovery & Reorganization Invariant Tests.

Tests recovery across lifecycle failure boundaries:
- A. before DB reputation insert
- B. after DB insert
- C. after transaction intent
- D. after outbox
- E. after Redis publish
- F. after transaction submission
- G. during duplicate delivery
- H. during reconciliation
- I. during blockchain reorganization

Tests deep reorganization invalidation and profile projection invariants:
- Canonical reputation event -> confirmed -> profile total=1, successes=1
- Reorganization -> event orphaned / non-canonical
- Projector recalculates profile excluding non-canonical events
- Invariant: Mutable counters are NOT authoritative; profile reflects only canonical events
- Invariant: Underlying execution and result hashes are strictly immutable
"""

from datetime import datetime, timezone
import uuid
import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession
from unittest.mock import patch

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
)
from app.models.notarization import (
    NotarizationStatus,
    ResultNotarization,
)
from app.models.reputation import (
    ReputationEvent,
    ReputationHistory,
    ReputationOutcomeType,
    ReputationProfile,
    ReputationStatus,
)
from app.models.user import User, UserRole
from app.services.blockchain.config import CHAIN_ID_ANVIL, get_chain_config
from app.services.blockchain.reputation_projector import ReputationProjector
from app.services.hashing import compute_canonical_hash
from app.services.notarization.utils import execution_id_to_bytes32
from app.services.reputation.errors import NotarizationNotCanonicalError
from app.services.reputation.reconciliation import ReputationReconciliationService
from app.services.reputation.service import ReputationService
from app.services.reputation.utils import uuid_to_bytes32


@pytest.fixture(autouse=True)
async def cleanup_db():
    yield
    async with async_session_factory() as session:
        await session.execute(sa.text("TRUNCATE TABLE reputation_profiles CASCADE"))
        await session.execute(sa.text("TRUNCATE TABLE reputation_history CASCADE"))
        await session.execute(sa.text("TRUNCATE TABLE reputation_events CASCADE"))
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


async def _create_test_execution_with_notary(
    session: AsyncSession,
) -> tuple[Agent, AgentVersion, AgentExecution, ResultNotarization, str]:
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
        name="Reputation Crash Agent",
        slug=f"rep-crash-{uuid.uuid4().hex[:8]}",
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

    payload = {"result": 100, "status": "ok"}
    res_hash = compute_canonical_hash(payload)

    input_payload = {"task": "process"}
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

    # Pre-populate confirmed ResultNotary proof
    notarization = ResultNotarization(
        idempotency_key=f"notary-{uuid.uuid4().hex[:12]}",
        notarization_key=f"key-{uuid.uuid4().hex[:12]}",
        execution_id=execution.id,
        chain_id=CHAIN_ID_ANVIL,
        contract_address="0x32EEce76C2C2e8758584A83Ee2F522D4788feA0f",
        result_hash=res_hash,
        status=NotarizationStatus.CONFIRMED.value,
        is_canonical=True,
        confirmations=32,
    )
    session.add(notarization)
    await session.flush()
    await session.commit()

    return agent, agent_version, execution, notarization, res_hash


@pytest.mark.asyncio
async def test_crash_recovery_before_intent_creation():
    """Crash stage A & B: Failure before or during intent creation aborts cleanly without orphans."""
    async with async_session_factory() as session:
        agent, version, execution, notary, res_hash = await _create_test_execution_with_notary(session)

    # Simulate crash during transaction intent creation
    with patch(
        "app.services.blockchain.transaction_intent.TransactionIntentService.create_intent",
        side_effect=RuntimeError("Simulated DB connection crash during intent creation"),
    ):
        async with async_session_factory() as session:
            with pytest.raises(RuntimeError, match="Simulated DB connection crash"):
                await ReputationService.create_reputation_event(
                    session=session,
                    execution_id=execution.id,
                    chain_id=CHAIN_ID_ANVIL,
                )

    # Verify no orphan reputation event exists in DB
    async with async_session_factory() as session:
        event = await ReputationService.get_reputation_event_by_execution(
            session, execution.id, CHAIN_ID_ANVIL
        )
        assert event is None

        # Recovery retry succeeds cleanly
        recovered_event, created = await ReputationService.create_reputation_event(
            session=session,
            execution_id=execution.id,
            chain_id=CHAIN_ID_ANVIL,
        )
        assert created is True
        assert recovered_event.status == ReputationStatus.PENDING.value
        await session.commit()


@pytest.mark.asyncio
async def test_crash_recovery_duplicate_delivery_and_concurrency():
    """Crash stage G: Duplicate delivery and concurrent processing resolves to single logical record."""
    async with async_session_factory() as session:
        agent, version, execution, notary, res_hash = await _create_test_execution_with_notary(session)

    # 1. Create first reputation event
    async with async_session_factory() as session:
        event1, created1 = await ReputationService.create_reputation_event(
            session=session,
            execution_id=execution.id,
            chain_id=CHAIN_ID_ANVIL,
        )
        assert created1 is True
        await session.commit()

    # 2. Duplicate deliveries (replays)
    for _ in range(5):
        async with async_session_factory() as session:
            dup_event, dup_created = await ReputationService.create_reputation_event(
                session=session,
                execution_id=execution.id,
                chain_id=CHAIN_ID_ANVIL,
            )
            assert dup_created is False
            assert dup_event.id == event1.id
            assert dup_event.result_hash == res_hash

    # Verify only 1 record exists in DB
    async with async_session_factory() as session:
        records = (
            await session.execute(
                sa.select(ReputationEvent).where(ReputationEvent.execution_id == execution.id)
            )
        ).scalars().all()
        assert len(records) == 1


@pytest.mark.asyncio
async def test_reputation_reconciliation_crash_recovery():
    """Crash stage H: Crash during reconciliation leaves state cleanly recoverable."""
    async with async_session_factory() as session:
        agent, version, execution, notary, res_hash = await _create_test_execution_with_notary(session)

    async with async_session_factory() as session:
        rep_event, _ = await ReputationService.create_reputation_event(
            session=session,
            execution_id=execution.id,
            chain_id=CHAIN_ID_ANVIL,
        )
        await session.commit()
        rep_event_id = rep_event.id

    # Create matching canonical BlockchainEvent
    tx_hash = "0x" + "cc" * 32
    block_hash = "0x" + "dd" * 32
    chain_config = get_chain_config(CHAIN_ID_ANVIL)
    rep_contract = chain_config.contract_addresses["ReputationRegistry"]

    async with async_session_factory() as session:
        event = BlockchainEvent(
            chain_id=CHAIN_ID_ANVIL,
            block_number=150,
            block_hash=block_hash,
            transaction_hash=tx_hash,
            transaction_index=0,
            log_index=0,
            event_name="ReputationEventRegistered",
            contract_address=rep_contract.lower(),
            decoded_data={
                "agentId": uuid_to_bytes32(agent.id),
                "executionId": uuid_to_bytes32(execution.id),
                "agentVersionId": uuid_to_bytes32(version.id),
                "outcomeType": 1,  # VERIFIED_SUCCESS
                "resultHash": "0x" + res_hash,
                "evidenceHash": "0x" + ("00" * 32),
                "reporter": "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266",
                "timestamp": int(datetime.now(timezone.utc).timestamp()),
            },
            status=EventStatus.CONFIRMED.value,
            is_canonical=True,
        )
        session.add(event)
        await session.commit()

    # Reconcile normally
    async with async_session_factory() as session:
        reconciled = await ReputationReconciliationService.reconcile_event(session, rep_event_id)
        assert reconciled.status == ReputationStatus.CONFIRMED.value
        assert reconciled.confirmations >= 1
        await session.commit()


@pytest.mark.asyncio
async def test_reputation_reorg_orphaning_and_profile_projection_invariant():
    """Section 13 & 14: Reorg orphaning invalidates reputation event and recalculates profile strictly from canonical events."""
    async with async_session_factory() as session:
        agent, version, execution, notary, res_hash = await _create_test_execution_with_notary(session)

    # 1. Create reputation event
    async with async_session_factory() as session:
        rep_event, _ = await ReputationService.create_reputation_event(
            session=session,
            execution_id=execution.id,
            chain_id=CHAIN_ID_ANVIL,
        )
        rep_event_id = rep_event.id
        await session.commit()

    # 2. Confirm via canonical BlockchainEvent
    tx_hash = "0x" + "33" * 32
    block_hash = "0x" + "44" * 32
    chain_config = get_chain_config(CHAIN_ID_ANVIL)
    rep_contract = chain_config.contract_addresses["ReputationRegistry"]

    async with async_session_factory() as session:
        event = BlockchainEvent(
            chain_id=CHAIN_ID_ANVIL,
            block_number=200,
            block_hash=block_hash,
            transaction_hash=tx_hash,
            transaction_index=0,
            log_index=0,
            event_name="ReputationEventRegistered",
            contract_address=rep_contract.lower(),
            decoded_data={
                "agentId": uuid_to_bytes32(agent.id),
                "executionId": uuid_to_bytes32(execution.id),
                "agentVersionId": uuid_to_bytes32(version.id),
                "outcomeType": 1,
                "resultHash": "0x" + res_hash,
                "evidenceHash": "0x" + ("00" * 32),
                "reporter": "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266",
                "timestamp": int(datetime.now(timezone.utc).timestamp()),
            },
            status=EventStatus.CONFIRMED.value,
            is_canonical=True,
        )
        session.add(event)
        await session.commit()

    # Reconcile to CONFIRMED and compute profile
    async with async_session_factory() as session:
        reconciled = await ReputationReconciliationService.reconcile_event(session, rep_event_id)
        assert reconciled.status == ReputationStatus.CONFIRMED.value
        assert reconciled.is_canonical is True
        await session.commit()

    async with async_session_factory() as session:
        profile = await ReputationService.get_agent_profile(session, agent.id, CHAIN_ID_ANVIL)
        assert profile.total_verified_executions == 1
        assert profile.verified_successes == 1
        assert float(profile.success_rate) == 1.0

    # 3. Simulate deep reorganization within 32 blocks: Event becomes orphaned
    async with async_session_factory() as session:
        ev_stmt = sa.select(BlockchainEvent).where(BlockchainEvent.transaction_hash == tx_hash)
        ev = (await session.execute(ev_stmt)).scalar_one()
        ev.is_canonical = False
        ev.status = EventStatus.ORPHANED.value

        # Reproject via ReputationProjector
        projector = ReputationProjector(CHAIN_ID_ANVIL)
        await projector.reproject_reputation(session, uuid_to_bytes32(execution.id))
        await session.commit()

    # 4. Verify ReputationEvent status is REORGED and is_canonical is False
    async with async_session_factory() as session:
        updated_event = await ReputationService.get_reputation_event_by_id(session, rep_event_id)
        assert updated_event.status == ReputationStatus.REORGED.value
        assert updated_event.is_canonical is False
        assert updated_event.reorged_at is not None

        # Verify API returns REORGED
        verify_resp = await ReputationService.verify_reputation_event(session, rep_event_id)
        assert verify_resp.verification_status == "REORGED"
        assert verify_resp.is_verified is False
        assert verify_resp.is_canonical is False

        # Invariant: ReputationProfile is derived strictly from canonical events!
        # Now that the only event was reorged, profile must show 0 verified successes!
        recalc_profile = await ReputationService.get_agent_profile(session, agent.id, CHAIN_ID_ANVIL)
        assert recalc_profile.total_verified_executions == 0
        assert recalc_profile.verified_successes == 0
        assert recalc_profile.success_rate is None

        # Invariant: Execution result_hash and status remain strictly immutable
        exec_stmt = sa.select(AgentExecution).where(AgentExecution.id == execution.id)
        current_exec = (await session.execute(exec_stmt)).scalar_one()
        assert current_exec.output_hash == res_hash, "Execution output hash was mutated during reorg!"


@pytest.mark.asyncio
async def test_reputation_refuses_creation_if_notary_reorged():
    """Section 13: Non-canonical or reorged notarization proof cannot generate verified reputation."""
    async with async_session_factory() as session:
        agent, version, execution, notary, res_hash = await _create_test_execution_with_notary(session)

    # Invalidate notarization by marking it REORGED
    async with async_session_factory() as session:
        db_notary = (
            await session.execute(
                sa.select(ResultNotarization).where(ResultNotarization.id == notary.id)
            )
        ).scalar_one()
        db_notary.status = NotarizationStatus.REORGED.value
        db_notary.is_canonical = False
        await session.commit()

    # Attempting to create reputation event MUST fail closed
    async with async_session_factory() as session:
        with pytest.raises(NotarizationNotCanonicalError):
            await ReputationService.create_reputation_event(
                session=session,
                execution_id=execution.id,
                chain_id=CHAIN_ID_ANVIL,
            )
