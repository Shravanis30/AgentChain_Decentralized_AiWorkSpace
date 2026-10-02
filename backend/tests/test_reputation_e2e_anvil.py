"""Phase 6.4 — Full End-to-End Integration Test on Live Local Anvil.

Exercises the complete 20-step verified reputation evidence lifecycle:
1. Create agent.
2. Publish agent version.
3. Execute agent.
4. Produce deterministic result output.
5. Calculate canonical SHA-256 result hash.
6. Notarize execution result on ResultNotary.
7. Confirm ResultNotary proof on-chain (depth >= 1).
8. Create reputation event for the terminal execution.
9. Create BlockchainTransactionIntent for registerReputationEvent.
10. Persist to BlockchainTxOutbox.
11. Publish outbox payload to Redis Stream.
12. Relayer consumes intent and signs transaction.
13. ReputationRegistry contract executes on Anvil.
14. ReputationEventRegistered event emitted.
15. EventIndexer indexes event into PostgreSQL.
16. ReputationProjector updates ReputationEvent state.
17. Confirm reputation event on-chain (depth advances).
18. Derive canonical ReputationProfile for the agent.
19. Call verification API to audit cryptographic proof path.
20. Verify complete evidence chain: Agent -> Version -> Execution -> ResultNotary -> ReputationRegistry -> Event -> Profile.
"""

import os
import time
import uuid
import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession
from web3 import Web3

from app.core.config import settings
from app.db.session import async_session_factory
from app.models.agent import (
    Agent,
    AgentExecution,
    AgentStatus,
    AgentVersion,
    ExecutionStatus,
)
from app.models.blockchain import (
    BlockStatus,
    BlockchainEvent,
    BlockchainTransaction,
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
from app.models.reputation import (
    ReputationEvent,
    ReputationHistory,
    ReputationOutcomeType,
    ReputationProfile,
    ReputationStatus,
)
from app.models.user import User, UserRole
from app.schemas.notarization import VerificationStatus
from app.services.blockchain.abi import (
    REPUTATION_REGISTRY_ABI,
    RESULT_NOTARY_ABI,
)
from app.services.blockchain.block_tracker import BlockTracker
from app.services.blockchain.config import (
    CHAIN_ID_ANVIL,
    ChainConfig,
    get_chain_config,
)
from app.services.blockchain.escrow_state_projector import EscrowStateProjector
from app.services.blockchain.event_indexer import EventIndexer
from app.services.blockchain.gas_estimator import GasEstimator
from app.services.blockchain.nonce_manager import NonceManager
from app.services.blockchain.notarization_projector import NotarizationProjector
from app.services.blockchain.relayer import BlockchainRelayer, LocalAccountSigner
from app.services.blockchain.reorg_handler import ReorgHandler
from app.services.blockchain.reputation_projector import ReputationProjector
from app.services.blockchain.rpc_client import BlockchainRpcClient
from app.services.blockchain.transaction_intent import TransactionIntentService
from app.services.blockchain.tx_outbox import BlockchainTxOutboxDispatcher
from app.services.hashing import compute_canonical_hash
from app.services.notarization.reconciliation import NotarizationReconciliationService
from app.services.notarization.service import NotarizationService
from app.services.rate_limiter import get_redis_client
from app.services.reputation.reconciliation import ReputationReconciliationService
from app.services.reputation.service import ReputationService
from app.services.reputation.utils import uuid_to_bytes32

ANVIL_RPC_URL = os.getenv("ANVIL_RPC_URL", "http://127.0.0.1:8545")
DEPLOYER_PK = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
DEPLOYER_ADDR = "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266"


@pytest.fixture(autouse=True)
async def clean_database_after_reputation_e2e():
    yield
    async with async_session_factory() as cleanup_session:
        for t in [
            "reputation_profiles",
            "reputation_history",
            "reputation_events",
            "notarization_history",
            "result_notarizations",
            "blockchain_events",
            "indexed_blocks",
            "blockchain_transaction_attempts",
            "blockchain_transactions",
            "blockchain_tx_outbox",
            "blockchain_transaction_intents",
            "relayer_nonces",
            "agent_executions",
            "agent_versions",
            "agents",
        ]:
            try:
                await cleanup_session.execute(sa.text(f"TRUNCATE TABLE {t} CASCADE"))
            except Exception:
                pass
        await cleanup_session.commit()


@pytest.mark.asyncio
async def test_verified_reputation_e2e_full_lifecycle():
    w3 = Web3(Web3.HTTPProvider(ANVIL_RPC_URL))
    assert w3.is_connected(), f"Failed to connect to Anvil RPC at {ANVIL_RPC_URL}"

    from tests.anvil_bootstrap import bootstrap_anvil
    bootstrap_anvil(rpc_url=ANVIL_RPC_URL)

    chain_config = get_chain_config(CHAIN_ID_ANVIL)
    notary_addr = chain_config.contract_addresses.get("ResultNotary")
    rep_addr = chain_config.contract_addresses.get("ReputationRegistry")
    assert notary_addr and notary_addr != "0x0000000000000000000000000000000000000000"
    assert rep_addr and rep_addr != "0x0000000000000000000000000000000000000000"

    notary_contract = w3.eth.contract(address=Web3.to_checksum_address(notary_addr), abi=RESULT_NOTARY_ABI)
    rep_contract = w3.eth.contract(address=Web3.to_checksum_address(rep_addr), abi=REPUTATION_REGISTRY_ABI)

    start_block = w3.eth.block_number

    # -------------------------------------------------------------------------
    # STEP 1: Create agent
    # -------------------------------------------------------------------------
    async with async_session_factory() as session:
        author_wallet = f"0x{uuid.uuid4().hex[:40]}"
        client_wallet = f"0x{uuid.uuid4().hex[:40]}"

        author = User(
            wallet_address=author_wallet.lower(),
            primary_role=UserRole.DEVELOPER.value,
            nonce=f"nonce-author-{uuid.uuid4().hex[:8]}",
        )
        client_user = User(
            wallet_address=client_wallet.lower(),
            primary_role=UserRole.CLIENT.value,
            nonce=f"nonce-client-{uuid.uuid4().hex[:8]}",
        )
        session.add_all([author, client_user])
        await session.flush()

        agent = Agent(
            name="Reputation Verified Test Agent",
            slug=f"rep-agent-{uuid.uuid4().hex[:8]}",
            description="Agent used to test end-to-end verified reputation evidence chain",
            owner_user_id=author.id,
            status=AgentStatus.DRAFT.value,
        )
        session.add(agent)
        await session.flush()

        # -------------------------------------------------------------------------
        # STEP 2: Publish agent version
        # -------------------------------------------------------------------------
        agent_version = AgentVersion(
            agent_id=agent.id,
            version="1.0.0",
            manifest={"entrypoint": "main.py"},
            input_schema={},
            output_schema={},
        )
        session.add(agent_version)
        await session.flush()

        agent.current_version_id = agent_version.id
        agent.status = AgentStatus.PUBLISHED.value

        # -------------------------------------------------------------------------
        # STEP 3: Execute agent
        # -------------------------------------------------------------------------
        # -------------------------------------------------------------------------
        # STEP 4: Produce deterministic result output
        # -------------------------------------------------------------------------
        deterministic_output = {
            "agent": "Reputation Verified Test Agent",
            "version": "1.0.0",
            "execution_metrics": {"accuracy": 0.99, "latency_ms": 120},
            "status": "COMPLETED",
        }

        # -------------------------------------------------------------------------
        # STEP 5: Calculate canonical SHA-256 result hash
        # -------------------------------------------------------------------------
        canonical_result_hash = compute_canonical_hash(deterministic_output)

        exec_input = {"task": "benchmark"}
        execution = AgentExecution(
            agent_id=agent.id,
            agent_version_id=agent_version.id,
            requested_by=client_user.id,
            input_data=exec_input,
            input_hash=compute_canonical_hash(exec_input),
            output_data=deterministic_output,
            output_hash=canonical_result_hash,
            status=ExecutionStatus.SUCCEEDED.value,
        )
        session.add(execution)
        await session.commit()

    # -------------------------------------------------------------------------
    # STEP 6: Notarize execution result on ResultNotary
    # -------------------------------------------------------------------------
    async with async_session_factory() as session:
        notarization, was_created = await NotarizationService.create_notarization(
            session=session,
            execution_id=execution.id,
            chain_id=CHAIN_ID_ANVIL,
        )
        assert was_created is True
        await session.commit()

    # Broadcast notarization intent via relayer
    rpc_client = BlockchainRpcClient(chain_config)
    nonce_mgr = NonceManager(rpc_client)
    gas_est = GasEstimator(rpc_client)
    signer = LocalAccountSigner(DEPLOYER_PK, chain_id=CHAIN_ID_ANVIL)
    relayer = BlockchainRelayer(chain_config, rpc_client, nonce_mgr, gas_est, signer)

    async with async_session_factory() as session:
        await nonce_mgr.reconcile_account_nonce(session, DEPLOYER_ADDR)
        intent_stmt = sa.select(BlockchainTransactionIntent).where(
            BlockchainTransactionIntent.id == notarization.transaction_intent_id
        )
        notary_intent = (await session.execute(intent_stmt)).scalar_one()
        tx = await relayer.submit_intent(session, notary_intent)
        tx_hash = tx.current_tx_hash
        assert tx_hash is not None and tx_hash.startswith("0x")
        await session.commit()

    notary_receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=10)
    assert notary_receipt["status"] == 1

    # -------------------------------------------------------------------------
    # STEP 7: Confirm ResultNotary proof on-chain (depth >= 1)
    # -------------------------------------------------------------------------
    # Index notarization event & advance confirmation
    reorg_h = ReorgHandler(rpc_client)
    block_tracker = BlockTracker(chain_config, reorg_h)
    escrow_proj = EscrowStateProjector(CHAIN_ID_ANVIL)
    notary_proj = NotarizationProjector(CHAIN_ID_ANVIL)
    rep_proj = ReputationProjector(CHAIN_ID_ANVIL)
    indexer = EventIndexer(chain_config, rpc_client, block_tracker, escrow_proj, notary_proj, rep_proj)

    current_block = w3.eth.block_number
    async with async_session_factory() as session:
        await indexer.index_block_range(session, start_block, current_block)
        w3.provider.make_request("evm_mine", [])
        await indexer.advance_event_confirmations(session, w3.eth.block_number)
        await session.commit()

    # Reconcile notarization to confirmed
    async with async_session_factory() as session:
        reconciled_notary = await NotarizationReconciliationService.reconcile_notarization(
            session, notarization.id
        )
        assert reconciled_notary.status == NotarizationStatus.CONFIRMED.value
        await session.commit()

    # -------------------------------------------------------------------------
    # STEP 8: Create reputation event for the terminal execution
    # -------------------------------------------------------------------------
    # -------------------------------------------------------------------------
    # STEP 9: Create BlockchainTransactionIntent for registerReputationEvent
    # -------------------------------------------------------------------------
    # -------------------------------------------------------------------------
    # STEP 10: Persist to BlockchainTxOutbox
    # -------------------------------------------------------------------------
    async with async_session_factory() as session:
        rep_event, was_rep_created = await ReputationService.create_reputation_event(
            session=session,
            execution_id=execution.id,
            chain_id=CHAIN_ID_ANVIL,
            evidence_payload={"eval_mode": "strict_crypto"},
        )
        assert was_rep_created is True
        assert rep_event.status == ReputationStatus.PENDING.value
        assert rep_event.outcome_type == ReputationOutcomeType.VERIFIED_SUCCESS.value
        assert rep_event.result_hash == canonical_result_hash
        assert rep_event.transaction_intent_id is not None
        await session.commit()

    # -------------------------------------------------------------------------
    # STEP 11: Publish outbox payload to Redis Stream
    # -------------------------------------------------------------------------
    redis = await get_redis_client()
    dispatcher = BlockchainTxOutboxDispatcher(redis_client=redis)
    async with async_session_factory() as session:
        dispatched_count = await dispatcher.dispatch_pending(session, batch_size=10)
        assert dispatched_count >= 1
        await session.commit()

    # -------------------------------------------------------------------------
    # STEP 12: Relayer consumes intent and signs transaction
    # -------------------------------------------------------------------------
    # -------------------------------------------------------------------------
    # STEP 13: ReputationRegistry contract executes on Anvil
    # -------------------------------------------------------------------------
    async with async_session_factory() as session:
        await nonce_mgr.reconcile_account_nonce(session, DEPLOYER_ADDR)
        intent_stmt = sa.select(BlockchainTransactionIntent).where(
            BlockchainTransactionIntent.id == rep_event.transaction_intent_id
        )
        rep_intent = (await session.execute(intent_stmt)).scalar_one()
        rep_tx = await relayer.submit_intent(session, rep_intent)
        rep_tx_hash = rep_tx.current_tx_hash
        assert rep_tx_hash is not None and rep_tx_hash.startswith("0x")
        await session.commit()

    rep_receipt = w3.eth.wait_for_transaction_receipt(rep_tx_hash, timeout=10)
    assert rep_receipt["status"] == 1, "ReputationRegistry transaction reverted on Anvil!"

    # -------------------------------------------------------------------------
    # STEP 14: ReputationEventRegistered event emitted
    # -------------------------------------------------------------------------
    rep_logs = rep_contract.events.ReputationEventRegistered().process_receipt(rep_receipt)
    assert len(rep_logs) == 1, "Expected exactly 1 ReputationEventRegistered log"
    event_args = rep_logs[0]["args"]
    assert event_args["outcomeType"] == 1  # VERIFIED_SUCCESS

    # Verify on-chain read method hasRecord / getRecord
    exec_b32 = uuid_to_bytes32(execution.id)
    has_rec = rep_contract.functions.hasRecord(exec_b32).call()
    assert has_rec is True
    onchain_rec = rep_contract.functions.getRecord(exec_b32).call()
    assert onchain_rec[0].hex().lower() == uuid_to_bytes32(agent.id).removeprefix("0x").lower()
    assert onchain_rec[1].hex().lower() == exec_b32.removeprefix("0x").lower()
    assert onchain_rec[3] == 1  # outcomeType

    # -------------------------------------------------------------------------
    # STEP 15: EventIndexer indexes event into PostgreSQL
    # -------------------------------------------------------------------------
    # -------------------------------------------------------------------------
    # STEP 16: ReputationProjector updates ReputationEvent state
    # -------------------------------------------------------------------------
    before_mine_block = w3.eth.block_number
    async with async_session_factory() as session:
        indexed_count = await indexer.index_block_range(session, start_block, before_mine_block)
        assert indexed_count >= 1
        await session.commit()

    # -------------------------------------------------------------------------
    # STEP 17: Confirm reputation event on-chain (depth advances)
    # -------------------------------------------------------------------------
    w3.provider.make_request("evm_mine", [])
    w3.provider.make_request("evm_mine", [])
    after_mine_block = w3.eth.block_number

    async with async_session_factory() as session:
        await indexer.advance_event_confirmations(session, after_mine_block)
        await session.commit()

    async with async_session_factory() as session:
        reconciled_rep = await ReputationReconciliationService.reconcile_event(
            session, rep_event.id
        )
        assert reconciled_rep.status == ReputationStatus.CONFIRMED.value
        assert reconciled_rep.confirmations >= 1
        assert reconciled_rep.is_canonical is True
        await session.commit()

    # -------------------------------------------------------------------------
    # STEP 18: Derive canonical ReputationProfile for the agent
    # -------------------------------------------------------------------------
    async with async_session_factory() as session:
        profile = await ReputationService.get_agent_profile(
            session, agent.id, chain_id=CHAIN_ID_ANVIL
        )
        assert profile.total_verified_executions == 1
        assert profile.verified_successes == 1
        assert profile.verified_failures == 0
        assert profile.verified_timeouts == 0
        assert profile.verified_cancellations == 0
        assert profile.canonical_event_count == 1
        assert profile.first_verified_execution_id == execution.id
        assert profile.latest_verified_execution_id == execution.id
        assert profile.latest_verified_outcome == ReputationOutcomeType.VERIFIED_SUCCESS.value
        assert float(profile.success_rate) == 1.0

    # -------------------------------------------------------------------------
    # STEP 19: Call verification API to audit cryptographic proof path
    # -------------------------------------------------------------------------
    async with async_session_factory() as session:
        verify_resp = await ReputationService.verify_reputation_event(
            session, rep_event.id
        )
        assert verify_resp.is_verified is True
        assert verify_resp.verification_status == "CONFIRMED"
        assert verify_resp.agent_id == agent.id
        assert verify_resp.agent_version_id == agent_version.id
        assert verify_resp.execution_id == execution.id
        assert verify_resp.outcome_type == ReputationOutcomeType.VERIFIED_SUCCESS.value
        assert verify_resp.has_valid_notarization is True
        assert verify_resp.is_canonical is True

    # -------------------------------------------------------------------------
    # STEP 20: Verify complete evidence chain
    # -------------------------------------------------------------------------
    # Agent -> Version -> Execution -> ResultNotary -> ReputationRegistry -> Event -> Profile
    async with async_session_factory() as session:
        # Load all entities
        db_agent = (await session.execute(sa.select(Agent).where(Agent.id == agent.id))).scalar_one()
        db_exec = (await session.execute(sa.select(AgentExecution).where(AgentExecution.id == execution.id))).scalar_one()
        db_notary = (await session.execute(sa.select(ResultNotarization).where(ResultNotarization.execution_id == execution.id))).scalar_one()
        db_rep = (await session.execute(sa.select(ReputationEvent).where(ReputationEvent.execution_id == execution.id))).scalar_one()
        db_profile = (await session.execute(sa.select(ReputationProfile).where(ReputationProfile.agent_id == agent.id))).scalar_one()

        assert db_exec.agent_id == db_agent.id
        assert db_exec.agent_version_id == db_agent.current_version_id
        assert db_exec.output_hash == db_notary.result_hash
        assert db_rep.execution_id == db_exec.id
        assert db_rep.notarization_id == db_notary.id
        assert db_rep.result_hash == db_notary.result_hash
        assert db_profile.agent_id == db_agent.id
        assert db_profile.latest_verified_execution_id == db_exec.id
        assert db_rep.is_canonical is True
        assert db_notary.is_canonical is True
