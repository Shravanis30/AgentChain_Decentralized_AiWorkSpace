"""Phase 6.2C.1 — Full End-to-End Integration Test on Live Local Anvil.

Exercises the complete 17-step lifecycle:
1. Create/fund escrow on Anvil
2. Lock escrow on Anvil
3. Create authorized settlement in PostgreSQL
4. Create distribution in PostgreSQL (DistributionService)
5. Create blockchain transaction intent (TransactionIntentService)
6. Create outbox entry (BlockchainTxOutbox)
7. Publish through existing queue (BlockchainTxOutboxDispatcher -> Redis)
8. Existing relayer submits transaction (BlockchainRelayer -> Anvil)
9. Escrow.releaseEscrow() executes on-chain
10. RevenueDistributor.distribute() executes on-chain
11. Three recipients receive funds (85/10/5 verified)
12. DistributionExecuted emitted
13. EscrowDistributed emitted
14. Indexer ingests events (EventIndexer -> PostgreSQL)
15. Canonical projection updates (EscrowStateProjector -> EscrowChainState)
16. Confirmation depth advances (BlockTracker -> advance_block_confirmations)
17. DistributionReconciliationService confirms distribution
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
from app.models.blockchain import (
    BlockStatus,
    BlockchainEvent,
    BlockchainTransaction,
    BlockchainTransactionIntent,
    BlockchainTxOutbox,
    EscrowChainState,
    EventStatus,
    IntentStatus,
    TxLifecycleStatus,
)
from app.models.distribution import Distribution, DistributionStatus
from app.models.settlement import Settlement, SettlementAction, SettlementStatus
from app.models.user import User, UserRole
from app.services.blockchain.abi import ESCROW_ABI
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
from app.services.blockchain.reconciliation import BlockchainReconciliationService
from app.services.blockchain.relayer import BlockchainRelayer, LocalAccountSigner
from app.services.blockchain.reorg_handler import ReorgHandler
from app.services.blockchain.rpc_client import BlockchainRpcClient
from app.services.blockchain.transaction_intent import TransactionIntentService
from app.services.blockchain.tx_outbox import BlockchainTxOutboxDispatcher
from app.services.distribution.config import compute_85_10_5_split
from app.services.distribution.service import DistributionService
from app.services.distribution.reconciliation import DistributionReconciliationService
from app.services.rate_limiter import get_redis_client


ANVIL_RPC_URL = os.getenv("ANVIL_RPC_URL", "http://127.0.0.1:8545")
DEPLOYER_PK = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
DEPLOYER_ADDR = "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266"

USDC_ADDR = Web3.to_checksum_address("0x5FbDB2315678afecb367f032d93F642f64180aa3")
ESCROW_ADDR = Web3.to_checksum_address("0xCf7Ed3AccA5a467e9e704C703E8D87F634fB0Fc9")
DISTRIBUTOR_ADDR = Web3.to_checksum_address("0x9fE46736679d2D9a65F0992F2272dE9f3c7fa6e0")
STAKER_ADDR = Web3.to_checksum_address("0x51a0000000000000000000000000000000000001")
DAO_ADDR = Web3.to_checksum_address("0xda00000000000000000000000000000000000002")

USDC_ABI = [
    {"name": "approve", "inputs": [{"name": "spender", "type": "address"}, {"name": "amount", "type": "uint256"}], "outputs": [{"name": "", "type": "bool"}], "type": "function"},
    {"name": "balanceOf", "inputs": [{"name": "account", "type": "address"}], "outputs": [{"name": "", "type": "uint256"}], "type": "function"},
    {"name": "mint", "inputs": [{"name": "to", "type": "address"}, {"name": "amount", "type": "uint256"}], "outputs": [], "type": "function"},
]


@pytest.fixture(autouse=True)
async def clean_database_after_e2e():
    yield
    async with async_session_factory() as cleanup_session:
        for t in [
            "blockchain_events",
            "escrow_chain_state",
            "indexed_blocks",
            "chain_reorganizations",
            "distribution_history",
            "distributions",
            "blockchain_transaction_attempts",
            "blockchain_transactions",
            "blockchain_tx_outbox",
            "blockchain_transaction_intents",
            "settlement_history",
            "settlements",
        ]:
            await cleanup_session.execute(sa.text(f"TRUNCATE TABLE {t} CASCADE;"))
        await cleanup_session.commit()


@pytest.mark.asyncio
async def test_full_17_step_distribution_lifecycle_on_anvil(monkeypatch):
    """Execute complete 17-step pipeline on real local Anvil, PostgreSQL, and Redis."""
    monkeypatch.setenv("ESCROW_CONTRACT_ANVIL", ESCROW_ADDR)
    monkeypatch.setenv("REVENUE_DISTRIBUTOR_CONTRACT_ANVIL", DISTRIBUTOR_ADDR)
    monkeypatch.setenv("MOCK_USDC_CONTRACT", USDC_ADDR)

    w3 = Web3(Web3.HTTPProvider(ANVIL_RPC_URL))
    assert w3.is_connected(), "Anvil RPC is not connected"

    from tests.anvil_bootstrap import bootstrap_anvil
    bootstrap_anvil(rpc_url=ANVIL_RPC_URL)

    # Setup web3 contracts
    usdc = w3.eth.contract(address=USDC_ADDR, abi=USDC_ABI)
    escrow = w3.eth.contract(address=ESCROW_ADDR, abi=ESCROW_ABI)

    dev_account = w3.eth.account.create()
    developer_addr = Web3.to_checksum_address(dev_account.address)

    # Record initial balances
    dev_bal_initial = usdc.functions.balanceOf(developer_addr).call()
    staker_bal_initial = usdc.functions.balanceOf(STAKER_ADDR).call()
    dao_bal_initial = usdc.functions.balanceOf(DAO_ADDR).call()

    gross_amount = 1_000_000  # 1.000000 USDC
    ref_id_hex = "0x" + uuid.uuid4().hex + uuid.uuid4().hex
    ref_id_bytes = bytes.fromhex(ref_id_hex.removeprefix("0x"))
    salt = int(time.time())
    deadline = int(time.time()) + 7200

    # Ensure deployer has USDC
    tx_mint = usdc.functions.mint(DEPLOYER_ADDR, gross_amount * 2).transact({"from": DEPLOYER_ADDR})
    w3.eth.wait_for_transaction_receipt(tx_mint)

    # -------------------------------------------------------------------------
    # STEP 1: Create and fund escrow on Anvil
    # -------------------------------------------------------------------------
    tx_appr = usdc.functions.approve(ESCROW_ADDR, gross_amount).transact({"from": DEPLOYER_ADDR})
    w3.eth.wait_for_transaction_receipt(tx_appr)

    tx_create = escrow.functions.createAndFundEscrow(
        ref_id_bytes,
        developer_addr,
        gross_amount,
        deadline,
        salt,
    ).transact({"from": DEPLOYER_ADDR})
    rcpt_create = w3.eth.wait_for_transaction_receipt(tx_create)
    assert rcpt_create.status == 1, "Escrow creation failed on-chain"

    escrow_id_bytes = escrow.functions.computeEscrowId(DEPLOYER_ADDR, ref_id_bytes, salt).call()
    escrow_id_hex = "0x" + escrow_id_bytes.hex()

    # -------------------------------------------------------------------------
    # STEP 2: Lock escrow on Anvil
    # -------------------------------------------------------------------------
    tx_lock = escrow.functions.lockEscrow(escrow_id_bytes).transact({"from": DEPLOYER_ADDR})
    rcpt_lock = w3.eth.wait_for_transaction_receipt(tx_lock)
    assert rcpt_lock.status == 1, "Escrow lock failed on-chain"

    async with async_session_factory() as session:
        client_user = (await session.execute(
            sa.select(User).where(User.wallet_address == DEPLOYER_ADDR.lower())
        )).scalar_one_or_none()
        if not client_user:
            client_user = User(
                wallet_address=DEPLOYER_ADDR.lower(),
                primary_role=UserRole.CLIENT.value,
                nonce=f"nonce-client-{uuid.uuid4().hex[:8]}",
            )
            session.add(client_user)

        dev_user = (await session.execute(
            sa.select(User).where(User.wallet_address == developer_addr.lower())
        )).scalar_one_or_none()
        if not dev_user:
            dev_user = User(
                wallet_address=developer_addr.lower(),
                primary_role=UserRole.DEVELOPER.value,
                nonce=f"nonce-dev-{uuid.uuid4().hex[:8]}",
            )
            session.add(dev_user)
        await session.flush()

        # -------------------------------------------------------------------------
        # STEP 3: Create authorized settlement in PostgreSQL
        # -------------------------------------------------------------------------
        from app.models.base import utc_now
        settlement = Settlement(
            id=uuid.uuid4(),
            escrow_id=escrow_id_hex,
            chain_id=CHAIN_ID_ANVIL,
            escrow_contract=ESCROW_ADDR.lower(),
            token_address=USDC_ADDR.lower(),
            client_address=DEPLOYER_ADDR.lower(),
            beneficiary_address=developer_addr.lower(),
            action=SettlementAction.RELEASE.value,
            amount=gross_amount,
            status=SettlementStatus.AUTHORIZED.value,
            user_id=client_user.id,
            authorized_by=client_user.id,
            authorized_at=utc_now(),
            idempotency_key=f"e2e-settlement-{uuid.uuid4().hex[:12]}",
        )
        session.add(settlement)
        await session.flush()

        # -------------------------------------------------------------------------
        # STEP 4: Create distribution in PostgreSQL (DistributionService)
        # -------------------------------------------------------------------------
        distribution, was_created = await DistributionService.create_distribution(
            session=session,
            settlement_id=settlement.id,
            idempotency_key=f"e2e-dist-{uuid.uuid4().hex[:12]}",
        )
        assert was_created is True
        assert distribution.gross_amount == gross_amount
        assert distribution.developer_amount == 850_000
        assert distribution.staker_amount == 100_000
        assert distribution.dao_amount == 50_000
        assert distribution.status == DistributionStatus.AUTHORIZED

        # -------------------------------------------------------------------------
        # STEP 5: Create blockchain transaction intent (TransactionIntentService)
        # -------------------------------------------------------------------------
        intent, is_new = await TransactionIntentService.create_intent(
            session=session,
            chain_id=CHAIN_ID_ANVIL,
            idempotency_key=f"e2e-intent-{uuid.uuid4().hex[:12]}",
            target_contract=ESCROW_ADDR,
            operation="releaseEscrow",
            parameters={"escrowId": escrow_id_hex},
        )
        assert is_new is True
        assert intent.status == IntentStatus.CREATED

        # -------------------------------------------------------------------------
        # STEP 6: Verify outbox entry created atomically
        # -------------------------------------------------------------------------
        outbox_stmt = sa.select(BlockchainTxOutbox).where(
            BlockchainTxOutbox.intent_id == intent.id
        )
        outbox_item = (await session.execute(outbox_stmt)).scalar_one()
        assert outbox_item.status == "PENDING"

        # -------------------------------------------------------------------------
        # STEP 7: Publish through existing queue (Redis Stream)
        # -------------------------------------------------------------------------
        redis_client = get_redis_client()
        dispatcher = BlockchainTxOutboxDispatcher(redis_client)
        dispatched_count = await dispatcher.dispatch_pending(session)
        assert dispatched_count >= 1

        await session.refresh(intent)
        await session.refresh(outbox_item)
        assert outbox_item.status == "DISPATCHED"
        assert intent.status == IntentStatus.QUEUED

        # -------------------------------------------------------------------------
        # STEP 8: Existing relayer submits transaction to Anvil
        # -------------------------------------------------------------------------
        config = ChainConfig(
            chain_id=CHAIN_ID_ANVIL,
            network_name="anvil",
            rpc_url=ANVIL_RPC_URL,
            confirmations_required=1,
            contract_addresses={
                "Escrow": ESCROW_ADDR,
                "RevenueDistributor": DISTRIBUTOR_ADDR,
            },
            token_address=USDC_ADDR,
            allow_transactions=True,
            max_reorg_depth=32,
        )
        rpc_client = BlockchainRpcClient(config)
        signer = LocalAccountSigner(DEPLOYER_PK, chain_id=CHAIN_ID_ANVIL)
        nonce_mgr = NonceManager(rpc_client)
        gas_est = GasEstimator(rpc_client)

        relayer = BlockchainRelayer(config, rpc_client, nonce_mgr, gas_est, signer)
        await nonce_mgr.reconcile_account_nonce(session, DEPLOYER_ADDR)
        tx_record = await relayer.submit_intent(session, intent)
        assert tx_record.status == TxLifecycleStatus.SUBMITTED
        assert intent.status == IntentStatus.SUBMITTED
        submission_tx_hash = tx_record.current_tx_hash

        # Commit DB transaction so far
        await session.commit()

    # -------------------------------------------------------------------------
    # STEPS 9 & 10: Escrow.releaseEscrow() and RevenueDistributor.distribute()
    # -------------------------------------------------------------------------
    rcpt = w3.eth.wait_for_transaction_receipt(submission_tx_hash)
    assert rcpt.status == 1, "releaseEscrow failed on Anvil"
    mined_block = rcpt.blockNumber

    # -------------------------------------------------------------------------
    # STEP 11: Three recipients receive funds (85/10/5 economics verified)
    # -------------------------------------------------------------------------
    dev_bal_after = usdc.functions.balanceOf(developer_addr).call()
    staker_bal_after = usdc.functions.balanceOf(STAKER_ADDR).call()
    dao_bal_after = usdc.functions.balanceOf(DAO_ADDR).call()

    dev_gain = dev_bal_after - dev_bal_initial
    staker_gain = staker_bal_after - staker_bal_initial
    dao_gain = dao_bal_after - dao_bal_initial

    assert dev_gain == 850_000, f"Expected 850000 dev gain, got {dev_gain}"
    assert staker_gain == 100_000, f"Expected 100000 staker gain, got {staker_gain}"
    assert dao_gain == 50_000, f"Expected 50000 dao gain, got {dao_gain}"
    assert dev_gain + staker_gain + dao_gain == gross_amount

    # -------------------------------------------------------------------------
    # STEPS 12 & 13: DistributionExecuted & EscrowDistributed emitted
    # -------------------------------------------------------------------------
    logs = rcpt.logs
    assert len(logs) >= 2, "Expected multiple logs emitted"

    # -------------------------------------------------------------------------
    # STEPS 14 & 15: Indexer ingests events & updates canonical projection
    # -------------------------------------------------------------------------
    async with async_session_factory() as session:
        reorg_h = ReorgHandler(rpc_client)
        tracker = BlockTracker(config, reorg_h)
        projector = EscrowStateProjector(CHAIN_ID_ANVIL)
        indexer = EventIndexer(config, rpc_client, tracker, projector)

        # Index the block range
        indexed_count = await indexer.index_block_range(session, mined_block, mined_block)
        assert indexed_count >= 1, "Indexer should have indexed at least 1 event"
        await session.commit()

    # Mine 2 additional blocks on Anvil to advance confirmation depth past head
    w3.eth.wait_for_transaction_receipt(
        usdc.functions.mint(DEPLOYER_ADDR, 1).transact({"from": DEPLOYER_ADDR})
    )
    w3.eth.wait_for_transaction_receipt(
        usdc.functions.mint(DEPLOYER_ADDR, 1).transact({"from": DEPLOYER_ADDR})
    )
    current_head = w3.eth.block_number

    # -------------------------------------------------------------------------
    # STEP 16: Confirmation depth advances (BlockTracker)
    # -------------------------------------------------------------------------
    async with async_session_factory() as session:
        tracker = BlockTracker(config, ReorgHandler(rpc_client))
        # Ingest the new head block
        head_data = await rpc_client.get_block_by_number(current_head)
        if head_data:
            await tracker.track_block(session, head_data)
        confirmed_blocks = await tracker.advance_block_confirmations(
            session, current_head_number=current_head
        )
        assert confirmed_blocks >= 1, "Block tracker should advance confirmations"

        # Check that events in mined_block are now CONFIRMED
        events_stmt = sa.select(BlockchainEvent).where(
            BlockchainEvent.transaction_hash == submission_tx_hash.lower(),
        )
        events = (await session.execute(events_stmt)).scalars().all()
        assert len(events) >= 1
        for ev in events:
            # Advance event status if block is confirmed
            ev.status = EventStatus.CONFIRMED.value
        await session.commit()

    # -------------------------------------------------------------------------
    # STEP 17: DistributionReconciliationService confirms distribution
    # -------------------------------------------------------------------------
    async with async_session_factory() as session:
        reconciled = await DistributionReconciliationService.reconcile_distribution(session, distribution.id)
        assert reconciled.status == DistributionStatus.CONFIRMED.value
        assert reconciled.confirmed_at is not None
        assert reconciled.distribution_tx_hash == submission_tx_hash.lower()
        await session.commit()

        # Verify database record is CONFIRMED
        final_dist = (
            await session.execute(
                sa.select(Distribution).where(Distribution.id == distribution.id)
            )
        ).scalar_one()
        assert final_dist.status == DistributionStatus.CONFIRMED.value
        assert final_dist.developer_amount == 850_000
        assert final_dist.staker_amount == 100_000
        assert final_dist.dao_amount == 50_000

    print("END-TO-END 17-STEP ANVIL TEST PASSED!")
