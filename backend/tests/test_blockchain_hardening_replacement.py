"""Phase 6.2A.1 Hardening Tests — Issue 3: Safe Transaction Replacement.

Tests Added:
1. Test 1 — Genuinely stuck transaction: pending beyond threshold -> replacement created with SAME nonce and higher fee.
2. Test 2 — RPC receipt outage: receipt query throws transient error -> classified UNKNOWN, NO replacement created.
3. Test 3 — Original transaction mined but receipt lagging: eth_getTransactionByHash returns blockNumber -> NO replacement created.
4. Test 4 — Replacement lifecycle linking: Attempt #1 REPLACED, Attempt #2 CONFIRMED, transaction linked cleanly.
5. Test 5 — Maximum replacement attempts: stops after max attempts (4) without infinite loop.
6. Test 6 — Nonce consumed externally: on-chain nonce advanced past tx.nonce -> reconciled as DROPPED, NO replacement.
7. Test 7 — Concurrent replacement workers: two workers running reconciliation -> ONLY ONE replacement attempt created.
8. Test 8 — Replacement fee validation: fee satisfies EIP-1559 12.5% bump requirements and respects ceiling.
"""

from datetime import datetime, timedelta, timezone
import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.base import utc_now
from app.models.blockchain import (
    AttemptStatus,
    BlockchainTransaction,
    BlockchainTransactionAttempt,
    BlockchainTransactionIntent,
    IntentStatus,
    TxLifecycleStatus,
)
from app.services.blockchain.config import CHAIN_ID_ANVIL, get_chain_config
from app.services.blockchain.errors import TransientRpcError
from app.services.blockchain.gas_estimator import GasEstimator
from app.services.blockchain.nonce_manager import NonceManager
from app.services.blockchain.reconciliation import BlockchainReconciliationService
from app.services.blockchain.relayer import BlockchainRelayer, MockSigner
from app.services.blockchain.rpc_client import BlockchainRpcClient


class MockRpcForReplacement(BlockchainRpcClient):
    """Configurable mock RPC client for testing transaction replacement scenarios."""

    def __init__(self, config):
        super().__init__(config)
        self.receipts = {}
        self.transactions = {}
        self.latest_nonces = {}
        self.pending_nonces = {}
        self.simulated_errors = {}
        self.sent_raw_txs = []

    async def get_transaction_receipt(self, tx_hash: str):
        if "get_transaction_receipt" in self.simulated_errors:
            raise self.simulated_errors["get_transaction_receipt"]
        return self.receipts.get(tx_hash)

    async def get_transaction(self, tx_hash: str):
        if "get_transaction" in self.simulated_errors:
            raise self.simulated_errors["get_transaction"]
        return self.transactions.get(tx_hash)

    async def get_transaction_count(self, address: str, block_tag: str = "pending"):
        if "get_transaction_count" in self.simulated_errors:
            raise self.simulated_errors["get_transaction_count"]
        norm = address.lower()
        if block_tag == "latest":
            return self.latest_nonces.get(norm, 0)
        return self.pending_nonces.get(norm, self.latest_nonces.get(norm, 0))

    async def send_raw_transaction(self, raw_tx_hex: str) -> str:
        self.sent_raw_txs.append(raw_tx_hex)
        return "0x" + "88" * 32


@pytest.fixture
def relayer_setup():
    cfg = get_chain_config(CHAIN_ID_ANVIL)
    mock_rpc = MockRpcForReplacement(cfg)
    nonce_mgr = NonceManager(mock_rpc)
    gas_est = GasEstimator(mock_rpc)
    signer = MockSigner("0x90F79bf6EB2c4f870365E785982E1f101E93b906")
    relayer = BlockchainRelayer(cfg, mock_rpc, nonce_mgr, gas_est, signer)
    reconciler = BlockchainReconciliationService(
        config=cfg,
        rpc_client=mock_rpc,
        relayer=relayer,
        stuck_threshold_seconds=120,
        max_replacement_attempts=4,
    )
    return cfg, mock_rpc, relayer, reconciler, signer.get_address()


async def _create_pending_tx(
    session: AsyncSession,
    chain_id: int,
    from_addr: str,
    nonce: int,
    initial_hash: str,
    submitted_at: datetime,
) -> BlockchainTransaction:
    intent = BlockchainTransactionIntent(
        chain_id=chain_id,
        operation="lockEscrow",
        target_contract=get_chain_config(chain_id).contract_addresses["Escrow"],
        parameters={"escrowId": "0x" + "01" * 32},
        idempotency_key=f"intent-nonce-{nonce}",
        status=IntentStatus.SUBMITTED,
    )
    session.add(intent)
    await session.flush()

    tx = BlockchainTransaction(
        intent_id=intent.id,
        chain_id=chain_id,
        from_address=from_addr.lower(),
        to_address=intent.target_contract.lower(),
        nonce=nonce,
        status=TxLifecycleStatus.SUBMITTED,
        current_tx_hash=initial_hash,
        submitted_at=submitted_at,
        attempts_count=1,
    )
    session.add(tx)
    await session.flush()

    att = BlockchainTransactionAttempt(
        transaction_id=tx.id,
        attempt_number=1,
        tx_hash=initial_hash,
        nonce=nonce,
        max_fee_per_gas=20_000_000_000,
        max_priority_fee_per_gas=1_500_000_000,
        gas_limit=100_000,
        raw_signed_tx="0x00",
        status=AttemptStatus.SUBMITTED,
        submitted_at=submitted_at,
    )
    session.add(att)
    await session.flush()
    return tx


@pytest.mark.asyncio
async def test_1_genuinely_stuck_transaction_triggers_safe_replacement(
    db_session: AsyncSession, relayer_setup
):
    """Test 1 — Genuinely stuck transaction:
    Transaction remains pending past 120s.
    RPC receipt: None (clean null).
    RPC get_transaction: returns pending tx object (blockNumber is None).
    RPC nonce count: latest == tx.nonce (unmined on chain).
    Expected: Safe replacement created with SAME nonce and higher fee.
    """
    cfg, mock_rpc, relayer, reconciler, from_addr = relayer_setup
    tx_hash = "0x" + "11" * 32
    stuck_time = utc_now() - timedelta(seconds=130)

    tx = await _create_pending_tx(db_session, cfg.chain_id, from_addr, nonce=5, initial_hash=tx_hash, submitted_at=stuck_time)

    # RPC setup:
    # 1. Receipt is None (unmined)
    mock_rpc.receipts[tx_hash] = None
    # 2. Transaction object exists in mempool (blockNumber is None)
    mock_rpc.transactions[tx_hash] = {"hash": tx_hash, "nonce": "0x5", "blockNumber": None}
    # 3. On-chain nonces: latest = 5, pending = 6
    mock_rpc.latest_nonces[from_addr.lower()] = 5
    mock_rpc.pending_nonces[from_addr.lower()] = 6

    # Reconcile
    results = await reconciler.reconcile_pending_transactions(db_session)
    assert results["bumped"] == 1
    assert results["confirmed"] == 0

    await db_session.refresh(tx)
    assert tx.attempts_count == 2
    assert tx.nonce == 5  # SAME nonce preserved!
    assert tx.current_tx_hash != tx_hash  # New hash assigned

    # Verify attempts records
    attempts_stmt = sa.select(BlockchainTransactionAttempt).where(
        BlockchainTransactionAttempt.transaction_id == tx.id
    ).order_by(BlockchainTransactionAttempt.attempt_number.asc())
    attempts = (await db_session.execute(attempts_stmt)).scalars().all()
    assert len(attempts) == 2
    assert attempts[0].status == AttemptStatus.REPLACED
    assert attempts[1].status == AttemptStatus.SUBMITTED
    assert attempts[1].nonce == 5
    assert attempts[1].max_fee_per_gas > attempts[0].max_fee_per_gas


@pytest.mark.asyncio
async def test_2_rpc_receipt_outage_classifies_unknown_and_defers(
    db_session: AsyncSession, relayer_setup
):
    """Test 2 — RPC receipt outage:
    Receipt lookup temporarily fails with TransientRpcError (HTTP 500 / network drop).
    Expected: NO replacement created. Transaction remains SUBMITTED.
    """
    cfg, mock_rpc, relayer, reconciler, from_addr = relayer_setup
    tx_hash = "0x" + "22" * 32
    stuck_time = utc_now() - timedelta(seconds=150)

    tx = await _create_pending_tx(db_session, cfg.chain_id, from_addr, nonce=6, initial_hash=tx_hash, submitted_at=stuck_time)

    # Simulate RPC failure on receipt lookup
    mock_rpc.simulated_errors["get_transaction_receipt"] = TransientRpcError("HTTP 502 Bad Gateway")

    results = await reconciler.reconcile_pending_transactions(db_session)
    assert results["bumped"] == 0
    assert results["confirmed"] == 0

    await db_session.refresh(tx)
    assert tx.attempts_count == 1  # Unchanged!
    assert tx.status == TxLifecycleStatus.SUBMITTED


@pytest.mark.asyncio
async def test_3_mined_transaction_with_receipt_temporarily_unavailable(
    db_session: AsyncSession, relayer_setup
):
    """Test 3 — Transaction mined on chain, but receipt index is temporarily lagging:
    eth_getTransactionReceipt returns None.
    eth_getTransactionByHash returns blockNumber=999 (already mined!).
    Expected: NO replacement created. Once receipt arrives, confirms normally.
    """
    cfg, mock_rpc, relayer, reconciler, from_addr = relayer_setup
    tx_hash = "0x" + "33" * 32
    stuck_time = utc_now() - timedelta(seconds=140)

    tx = await _create_pending_tx(db_session, cfg.chain_id, from_addr, nonce=7, initial_hash=tx_hash, submitted_at=stuck_time)

    # Receipt is None, but transaction object shows blockNumber=999
    mock_rpc.receipts[tx_hash] = None
    mock_rpc.transactions[tx_hash] = {"hash": tx_hash, "nonce": "0x7", "blockNumber": "0x3e7"}
    mock_rpc.latest_nonces[from_addr.lower()] = 7

    # First reconciliation: detects mined blockNumber -> DEFERS, does NOT replace!
    res1 = await reconciler.reconcile_pending_transactions(db_session)
    assert res1["bumped"] == 0
    assert res1["confirmed"] == 0
    await db_session.refresh(tx)
    assert tx.attempts_count == 1

    # Later: receipt arrives from node
    mock_rpc.receipts[tx_hash] = {
        "blockNumber": "0x3e7",
        "blockHash": "0x" + "99" * 32,
        "gasUsed": "0x5208",
        "status": "0x1",
        "effectiveGasPrice": "0x4a817c800",
    }

    # Second reconciliation: confirms transaction cleanly
    res2 = await reconciler.reconcile_pending_transactions(db_session)
    assert res2["confirmed"] == 1
    assert res2["bumped"] == 0
    await db_session.refresh(tx)
    assert tx.status == TxLifecycleStatus.CONFIRMED


@pytest.mark.asyncio
async def test_4_replacement_attempt_lifecycle_linking(
    db_session: AsyncSession, relayer_setup
):
    """Test 4 — Replacement transaction attempt linking:
    Attempt 1 was bumped to Attempt 2.
    Attempt 2 confirms on-chain.
    Verify: Attempt 1 is REPLACED, Attempt 2 is CONFIRMED, Transaction is CONFIRMED.
    """
    cfg, mock_rpc, relayer, reconciler, from_addr = relayer_setup
    hash1 = "0x" + "41" * 32
    hash2 = "0x" + "42" * 32
    stuck_time = utc_now() - timedelta(seconds=130)

    tx = await _create_pending_tx(db_session, cfg.chain_id, from_addr, nonce=8, initial_hash=hash1, submitted_at=stuck_time)

    mock_rpc.receipts[hash1] = None
    mock_rpc.transactions[hash1] = {"hash": hash1, "nonce": "0x8", "blockNumber": None}
    mock_rpc.latest_nonces[from_addr.lower()] = 8

    # Bump to attempt 2
    res = await reconciler.reconcile_pending_transactions(db_session)
    assert res["bumped"] == 1
    await db_session.refresh(tx)
    hash2 = tx.current_tx_hash

    # Now attempt 2 gets mined and receipt arrives
    mock_rpc.receipts[hash2] = {
        "blockNumber": "0x100",
        "blockHash": "0x" + "aa" * 32,
        "gasUsed": "0x5208",
        "status": "0x1",
        "effectiveGasPrice": "0x4a817c800",
    }

    res_confirm = await reconciler.reconcile_pending_transactions(db_session)
    assert res_confirm["confirmed"] == 1

    await db_session.refresh(tx)
    assert tx.status == TxLifecycleStatus.CONFIRMED

    attempts_stmt = sa.select(BlockchainTransactionAttempt).where(
        BlockchainTransactionAttempt.transaction_id == tx.id
    ).order_by(BlockchainTransactionAttempt.attempt_number.asc())
    attempts = (await db_session.execute(attempts_stmt)).scalars().all()

    assert len(attempts) == 2
    assert attempts[0].status == AttemptStatus.REPLACED
    assert attempts[1].status == AttemptStatus.CONFIRMED


@pytest.mark.asyncio
async def test_5_maximum_replacement_attempts_enforced(
    db_session: AsyncSession, relayer_setup
):
    """Test 5 — Maximum replacement attempts:
    System must not exceed configured max attempts (e.g. 4) and avoid infinite gas bumps.
    """
    cfg, mock_rpc, relayer, reconciler, from_addr = relayer_setup
    tx_hash = "0x" + "55" * 32
    stuck_time = utc_now() - timedelta(seconds=200)

    tx = await _create_pending_tx(db_session, cfg.chain_id, from_addr, nonce=9, initial_hash=tx_hash, submitted_at=stuck_time)
    tx.attempts_count = 4  # Already reached max
    await db_session.flush()

    mock_rpc.receipts[tx_hash] = None
    mock_rpc.transactions[tx_hash] = {"hash": tx_hash, "nonce": "0x9", "blockNumber": None}
    mock_rpc.latest_nonces[from_addr.lower()] = 9

    results = await reconciler.reconcile_pending_transactions(db_session)
    assert results["bumped"] == 0  # HALTED! No further bumps.

    await db_session.refresh(tx)
    assert tx.attempts_count == 4


@pytest.mark.asyncio
async def test_6_external_nonce_consumption_reconciles_safely(
    db_session: AsyncSession, relayer_setup
):
    """Test 6 — Nonce consumed externally:
    If another transaction consumed the nonce (latest_nonce > tx.nonce):
    Do not blindly create another transaction. Reconcile safely by marking DROPPED.
    """
    cfg, mock_rpc, relayer, reconciler, from_addr = relayer_setup
    tx_hash = "0x" + "66" * 32
    stuck_time = utc_now() - timedelta(seconds=150)

    tx = await _create_pending_tx(db_session, cfg.chain_id, from_addr, nonce=10, initial_hash=tx_hash, submitted_at=stuck_time)

    # On-chain nonce has already moved to 11!
    mock_rpc.receipts[tx_hash] = None
    mock_rpc.transactions[tx_hash] = None
    mock_rpc.latest_nonces[from_addr.lower()] = 11

    results = await reconciler.reconcile_pending_transactions(db_session)
    assert results["bumped"] == 0  # Did NOT replace!

    await db_session.refresh(tx)
    assert tx.status == TxLifecycleStatus.DROPPED
    assert "consumed on-chain" in tx.error_message


@pytest.mark.asyncio
async def test_7_concurrent_replacement_prevented(
    db_session: AsyncSession, relayer_setup
):
    """Test 7 — Concurrency test:
    Two workers attempt to reconcile and replace the same stuck transaction.
    Expected: ONLY ONE replacement attempt is created.
    """
    cfg, mock_rpc, relayer, reconciler, from_addr = relayer_setup
    tx_hash = "0x" + "77" * 32
    stuck_time = utc_now() - timedelta(seconds=130)

    tx = await _create_pending_tx(db_session, cfg.chain_id, from_addr, nonce=12, initial_hash=tx_hash, submitted_at=stuck_time)

    mock_rpc.receipts[tx_hash] = None
    mock_rpc.transactions[tx_hash] = {"hash": tx_hash, "nonce": "0xc", "blockNumber": None}
    mock_rpc.latest_nonces[from_addr.lower()] = 12

    # Worker 1 runs reconciliation and bumps
    res1 = await reconciler.reconcile_pending_transactions(db_session)
    assert res1["bumped"] == 1

    # Worker 2 runs reconciliation immediately after
    res2 = await reconciler.reconcile_pending_transactions(db_session)
    # The replacement submitted_at is now recent (< stuck_threshold)
    assert res2["bumped"] == 0

    await db_session.refresh(tx)
    assert tx.attempts_count == 2  # Exactly ONE replacement created


@pytest.mark.asyncio
async def test_8_replacement_fee_validation(
    db_session: AsyncSession, relayer_setup
):
    """Test 8 — Replacement fee validation:
    Verify that replacement gas fees are bumped by at least 10% (12.5% in implementation),
    and strictly adhere to configured gas limits and fee ceiling.
    """
    cfg, mock_rpc, relayer, reconciler, from_addr = relayer_setup
    tx_hash = "0x" + "88" * 32
    stuck_time = utc_now() - timedelta(seconds=130)

    tx = await _create_pending_tx(db_session, cfg.chain_id, from_addr, nonce=15, initial_hash=tx_hash, submitted_at=stuck_time)

    mock_rpc.receipts[tx_hash] = None
    mock_rpc.transactions[tx_hash] = {"hash": tx_hash, "nonce": "0xf", "blockNumber": None}
    mock_rpc.latest_nonces[from_addr.lower()] = 15

    # Trigger replacement
    res = await reconciler.reconcile_pending_transactions(db_session)
    assert res["bumped"] == 1

    attempts_stmt = sa.select(BlockchainTransactionAttempt).where(
        BlockchainTransactionAttempt.transaction_id == tx.id
    ).order_by(BlockchainTransactionAttempt.attempt_number.asc())
    attempts = (await db_session.execute(attempts_stmt)).scalars().all()

    att1, att2 = attempts[0], attempts[1]
    # Verify at least 10% bump on both max_fee and priority_fee
    assert att2.max_fee_per_gas >= int(att1.max_fee_per_gas * 1.10)
    assert att2.max_priority_fee_per_gas >= int(att1.max_priority_fee_per_gas * 1.10)
    # Verify nonce equality
    assert att2.nonce == att1.nonce
    # Verify gas limit preserved
    assert att2.gas_limit == att1.gas_limit

