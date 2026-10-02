"""Phase 6.3 — Full End-to-End Integration Test on Live Local Anvil.

Exercises the complete 18-step result notarization lifecycle:
1. Create agent.
2. Publish agent.
3. Execute agent.
4. Produce deterministic result.
5. Persist canonical result hash.
6. Request notarization.
7. Create blockchain transaction intent.
8. Persist outbox.
9. Publish to existing Redis stream.
10. Existing relayer submits transaction.
11. ResultNotary contract records proof.
12. ResultNotarized event emitted.
13. Existing indexer ingests event.
14. Canonical event projection updates.
15. Confirmation depth advances.
16. Reconciliation verifies proof.
17. Verification API returns VERIFIED.
18. Recompute result hash and compare against on-chain proof.
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
from app.models.user import User, UserRole
from app.schemas.notarization import VerificationStatus
from app.services.blockchain.abi import RESULT_NOTARY_ABI
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
from app.services.blockchain.rpc_client import BlockchainRpcClient
from app.services.blockchain.transaction_intent import TransactionIntentService
from app.services.blockchain.tx_outbox import BlockchainTxOutboxDispatcher
from app.services.hashing import compute_canonical_hash
from app.services.notarization.reconciliation import NotarizationReconciliationService
from app.services.notarization.service import NotarizationService
from app.services.notarization.utils import (
    bytes32_to_execution_id,
    bytes32_to_result_hash,
    execution_id_to_bytes32,
    result_hash_to_bytes32,
)
from app.services.rate_limiter import get_redis_client
from app.services.state_machine import ExecutionStateMachine

ANVIL_RPC_URL = os.getenv("ANVIL_RPC_URL", "http://127.0.0.1:8545")
DEPLOYER_PK = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
DEPLOYER_ADDR = "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266"


@pytest.fixture(autouse=True)
async def clean_database_after_e2e():
    yield
    async with async_session_factory() as cleanup_session:
        for t in [
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
async def test_result_notarization_e2e_full_lifecycle():
    w3 = Web3(Web3.HTTPProvider(ANVIL_RPC_URL))
    assert w3.is_connected(), f"Failed to connect to Anvil RPC at {ANVIL_RPC_URL}"

    from tests.anvil_bootstrap import bootstrap_anvil
    bootstrap_anvil(rpc_url=ANVIL_RPC_URL)

    # Verify ResultNotary contract address
    chain_config = get_chain_config(CHAIN_ID_ANVIL)
    notary_addr = chain_config.contract_addresses.get("ResultNotary")
    assert notary_addr and notary_addr != "0x0000000000000000000000000000000000000000"
    notary_contract = w3.eth.contract(address=Web3.to_checksum_address(notary_addr), abi=RESULT_NOTARY_ABI)

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
            name="Notarization Verification Agent",
            slug=f"notary-agent-{uuid.uuid4().hex[:8]}",
            description="Agent used to test end-to-end cryptographic result notarization",
            owner_user_id=author.id,
            status=AgentStatus.DRAFT.value,
        )
        session.add(agent)
        await session.flush()

        # -------------------------------------------------------------------------
        # STEP 2: Publish agent
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

        agent.status = AgentStatus.PUBLISHED.value
        agent.current_version_id = agent_version.id
        await session.flush()

        # -------------------------------------------------------------------------
        # STEP 3: Execute agent
        # -------------------------------------------------------------------------
        execution_input = {"query": "compute prime factor 42", "seed": 12345}
        input_hash = compute_canonical_hash(execution_input)
        execution = AgentExecution(
            agent_id=agent.id,
            agent_version_id=agent_version.id,
            requested_by=client_user.id,
            status=ExecutionStatus.RUNNING.value,
            input_data=execution_input,
            input_hash=input_hash,
        )
        session.add(execution)
        await session.flush()

        # -------------------------------------------------------------------------
        # STEP 4: Produce deterministic result
        # -------------------------------------------------------------------------
        deterministic_output = {
            "factors": [2, 3, 7],
            "computed_at_epoch": 1727500000,
            "status": "COMPLETED",
        }

        # -------------------------------------------------------------------------
        # STEP 5: Persist canonical result hash
        # -------------------------------------------------------------------------
        canonical_result_hash = compute_canonical_hash(deterministic_output)
        await ExecutionStateMachine.transition_to_succeeded(
            db=session,
            execution=execution,
            output_data=deterministic_output,
            output_hash=canonical_result_hash,
            execution_time_ms=85,
        )
        await session.commit()

    # -------------------------------------------------------------------------
    # STEP 6: Request notarization
    # -------------------------------------------------------------------------
    artifact_ref = "ipfs://bafybeic7n6y2b66d4m73x5g5yq7fey7r3v5sxgvx"
    async with async_session_factory() as session:
        notarization, created = await NotarizationService.create_notarization(
            session=session,
            execution_id=execution.id,
            chain_id=CHAIN_ID_ANVIL,
            artifact_reference=artifact_ref,
        )
        assert created is True
        assert notarization.status == NotarizationStatus.PENDING.value
        assert notarization.result_hash == canonical_result_hash

        # -------------------------------------------------------------------------
        # STEP 7: Verify blockchain transaction intent created
        # -------------------------------------------------------------------------
        intent_stmt = sa.select(BlockchainTransactionIntent).where(
            BlockchainTransactionIntent.id == notarization.transaction_intent_id
        )
        intent = (await session.execute(intent_stmt)).scalar_one()
        assert intent.operation == "notarizeResult"
        assert intent.target_contract == notary_addr.lower()
        assert intent.status == IntentStatus.CREATED.value

        # -------------------------------------------------------------------------
        # STEP 8: Verify outbox persisted
        # -------------------------------------------------------------------------
        outbox_stmt = sa.select(BlockchainTxOutbox).where(
            BlockchainTxOutbox.intent_id == intent.id
        )
        outbox = (await session.execute(outbox_stmt)).scalar_one()
        assert outbox.status == "PENDING"
        await session.commit()

    # -------------------------------------------------------------------------
    # STEP 9: Publish to existing Redis stream
    # -------------------------------------------------------------------------
    redis_client = get_redis_client()
    dispatcher = BlockchainTxOutboxDispatcher(redis_client)
    async with async_session_factory() as session:
        dispatched_count = await dispatcher.dispatch_pending(session, batch_size=10)
        assert dispatched_count >= 1
        await session.commit()

    # Verify stream entry
    messages = await redis_client.xrange(dispatcher.stream_name, "-", "+")
    assert len(messages) >= 1
    stream_msg_id, stream_fields = messages[-1]

    # -------------------------------------------------------------------------
    # STEP 10: Existing relayer submits transaction to Anvil
    # -------------------------------------------------------------------------
    rpc_client = BlockchainRpcClient(chain_config)
    nonce_mgr = NonceManager(rpc_client)
    gas_est = GasEstimator(rpc_client)
    signer = LocalAccountSigner(DEPLOYER_PK, CHAIN_ID_ANVIL)
    relayer = BlockchainRelayer(chain_config, rpc_client, nonce_mgr, gas_est, signer)

    async with async_session_factory() as session:
        await nonce_mgr.reconcile_account_nonce(session, DEPLOYER_ADDR)
        intent_to_submit = (await session.execute(
            sa.select(BlockchainTransactionIntent).where(BlockchainTransactionIntent.id == intent.id)
        )).scalar_one()

        bc_tx = await relayer.submit_intent(
            session=session,
            intent=intent_to_submit,
        )
        tx_hash = bc_tx.current_tx_hash
        assert tx_hash is not None and tx_hash.startswith("0x")
        await session.commit()

    # Wait for transaction receipt on Anvil
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=10)
    assert receipt.status == 1, "Result notarization transaction reverted on-chain"

    # -------------------------------------------------------------------------
    # STEP 11: ResultNotary contract records proof
    # -------------------------------------------------------------------------
    exec_id_b32 = execution_id_to_bytes32(execution.id)
    has_proof = notary_contract.functions.hasProof(exec_id_b32).call()
    assert has_proof is True, "ResultNotary contract did not record proof"

    onchain_proof = notary_contract.functions.getProof(exec_id_b32).call()
    assert onchain_proof[0].hex().lower() == exec_id_b32.removeprefix("0x").lower()
    assert onchain_proof[1].hex().lower() == canonical_result_hash.lower()

    # -------------------------------------------------------------------------
    # STEP 12: ResultNotarized event emitted on Anvil
    # -------------------------------------------------------------------------
    logs = notary_contract.events.ResultNotarized().process_receipt(receipt)
    assert len(logs) == 1, "Expected exactly 1 ResultNotarized event"
    event_args = logs[0]["args"]
    assert event_args["resultHash"].hex().lower() == canonical_result_hash.lower()

    # -------------------------------------------------------------------------
    # STEP 13: Existing indexer ingests event
    # -------------------------------------------------------------------------
    current_block = w3.eth.block_number
    reorg_h = ReorgHandler(rpc_client)
    block_tracker = BlockTracker(chain_config, reorg_h)
    escrow_proj = EscrowStateProjector(CHAIN_ID_ANVIL)
    notary_proj = NotarizationProjector(CHAIN_ID_ANVIL)
    indexer = EventIndexer(chain_config, rpc_client, block_tracker, escrow_proj, notary_proj)

    async with async_session_factory() as session:
        indexed_count = await indexer.index_block_range(session, start_block, current_block)
        assert indexed_count >= 1

        # Verify BlockchainEvent in PostgreSQL
        bc_event = (await session.execute(
            sa.select(BlockchainEvent).where(
                BlockchainEvent.chain_id == CHAIN_ID_ANVIL,
                BlockchainEvent.transaction_hash == tx_hash.lower(),
                BlockchainEvent.event_name == "ResultNotarized",
            )
        )).scalar_one_or_none()
        assert bc_event is not None
        assert bc_event.is_canonical is True

        # -------------------------------------------------------------------------
        # STEP 14: Canonical event projection updates ResultNotarization
        # -------------------------------------------------------------------------
        updated_notary = (await session.execute(
            sa.select(ResultNotarization).where(ResultNotarization.id == notarization.id)
        )).scalar_one()
        assert updated_notary.is_canonical is True
        assert updated_notary.transaction_hash == tx_hash.lower()
        await session.commit()

    # -------------------------------------------------------------------------
    # STEP 15: Confirmation depth advances
    # -------------------------------------------------------------------------
    # Mine 2 blocks on Anvil to advance confirmations
    w3.provider.make_request("evm_mine", [])
    w3.provider.make_request("evm_mine", [])
    advanced_head = w3.eth.block_number

    async with async_session_factory() as session:
        await indexer.advance_event_confirmations(session, advanced_head)
        await session.commit()

    # -------------------------------------------------------------------------
    # STEP 16: Reconciliation verifies proof
    # -------------------------------------------------------------------------
    async with async_session_factory() as session:
        reconciled = await NotarizationReconciliationService.reconcile_notarization(
            session, notarization.id
        )
        assert reconciled.status == NotarizationStatus.CONFIRMED.value
        assert reconciled.confirmations >= 1
        assert reconciled.is_canonical is True
        await session.commit()

    # -------------------------------------------------------------------------
    # STEP 17: Verification API returns VERIFIED
    # -------------------------------------------------------------------------
    async with async_session_factory() as session:
        verify_resp = await NotarizationService.verify_execution(
            session=session,
            execution_id=execution.id,
            result_payload=deterministic_output,
            chain_id=CHAIN_ID_ANVIL,
        )
        assert verify_resp.verification_status == VerificationStatus.VERIFIED
        assert verify_resp.computed_hash == canonical_result_hash
        assert verify_resp.onchain_hash == canonical_result_hash
        assert verify_resp.is_canonical is True
        assert verify_resp.confirmations >= 1

    # -------------------------------------------------------------------------
    # STEP 18: Recompute result hash and compare against on-chain proof
    # -------------------------------------------------------------------------
    fresh_onchain_proof = notary_contract.functions.getProof(exec_id_b32).call()
    onchain_hash_hex = fresh_onchain_proof[1].hex().lower()
    locally_recomputed = compute_canonical_hash(deterministic_output).lower()

    assert locally_recomputed == onchain_hash_hex == canonical_result_hash.lower()
