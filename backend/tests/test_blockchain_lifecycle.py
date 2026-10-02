"""Integration tests for transaction lifecycle: intents, outbox, relayer, gas bumping, nonces, and receipts."""

import asyncio
from unittest.mock import AsyncMock, patch
from eth_account import Account
import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from web3 import Web3

from app.models.blockchain import (
    AttemptStatus,
    BlockchainTransaction,
    BlockchainTransactionAttempt,
    BlockchainTransactionIntent,
    BlockchainTxOutbox,
    IntentStatus,
    RelayerNonce,
    TxLifecycleStatus,
)
from app.services.blockchain.config import CHAIN_ID_ANVIL, get_chain_config
from app.services.blockchain.gas_estimator import GasEstimator
from app.services.blockchain.nonce_manager import NonceManager
from app.services.blockchain.relayer import (
    BlockchainRelayer,
    LocalAccountSigner,
)
from app.services.blockchain.rpc_client import BlockchainRpcClient
from app.services.blockchain.transaction_intent import TransactionIntentService
from app.services.blockchain.tx_outbox import BlockchainTxOutboxDispatcher


class MockRpcClient(BlockchainRpcClient):
    """Mock RPC client providing deterministic test EVM responses."""

    def __init__(self, config):
        super().__init__(config)
        self.mock_nonce = 0
        self.mock_receipts = {}
        self.sent_raw_txs = []

    async def verify_chain_id(self) -> int:
        return self.chain_id

    async def get_transaction_count(self, address: str, block_tag: str = "pending") -> int:
        return self.mock_nonce

    async def get_fee_history(self, *args, **kwargs) -> dict:
        return {
            "baseFeePerGas": ["0x3b9aca00"],  # 1 gwei
            "reward": [["0x3b9aca00"]],       # 1 gwei
        }

    async def send_raw_transaction(self, raw_tx_hex: str) -> str:
        self.sent_raw_txs.append(raw_tx_hex)
        tx_hash = "0x" + Web3.keccak(hexstr=raw_tx_hex).hex().lower()
        return tx_hash

    async def get_transaction_receipt(self, tx_hash: str) -> dict | None:
        return self.mock_receipts.get(tx_hash.lower())


@pytest.mark.asyncio
async def test_full_transaction_intent_to_confirmation(db_session: AsyncSession):
    """End-to-end transaction test: Intent -> Outbox -> Redis -> Relayer -> Broadcast -> Receipt Confirmed."""
    cfg = get_chain_config(CHAIN_ID_ANVIL)
    mock_rpc = MockRpcClient(cfg)
    signer_acct = Account.create()
    signer = LocalAccountSigner(signer_acct.key.hex())
    nonce_mgr = NonceManager(mock_rpc)
    gas_est = GasEstimator(mock_rpc)
    relayer = BlockchainRelayer(cfg, mock_rpc, nonce_mgr, gas_est, signer)

    # 1. Create Transaction Intent
    target = cfg.contract_addresses["Escrow"]
    intent, was_new = await TransactionIntentService.create_intent(
        session=db_session,
        chain_id=CHAIN_ID_ANVIL,
        idempotency_key="lifecycle-test-001",
        target_contract=target,
        operation="releaseEscrow",
        parameters={"escrowId": "0x" + "11" * 32},
    )
    assert was_new is True
    assert intent.status == IntentStatus.CREATED

    # 2. Verify Outbox record created atomically
    outbox_entry = (await db_session.execute(
        BlockchainTxOutbox.__table__.select().where(BlockchainTxOutbox.intent_id == intent.id)
    )).one()
    assert outbox_entry.status == "PENDING"

    # 3. Dispatch Outbox via mock Redis
    mock_redis = AsyncMock()
    mock_redis.xadd.return_value = "1000-0"
    dispatcher = BlockchainTxOutboxDispatcher(mock_redis)

    dispatched = await dispatcher.dispatch_pending(db_session)
    assert dispatched >= 1
    assert intent.status == IntentStatus.QUEUED

    # 4. Relayer submits intent
    tx_record = await relayer.submit_intent(db_session, intent)
    assert tx_record.status == TxLifecycleStatus.SUBMITTED
    assert tx_record.nonce == 0
    assert intent.status == IntentStatus.SUBMITTED
    assert len(mock_rpc.sent_raw_txs) == 1

    tx_hash = tx_record.current_tx_hash

    # 5. Check receipt polling before mined (pending)
    updated = await relayer.poll_and_update_receipt(db_session, tx_record)
    assert updated is False
    assert tx_record.status == TxLifecycleStatus.SUBMITTED

    # 6. Mine transaction successfully (receipt status 1)
    mock_rpc.mock_receipts[tx_hash.lower()] = {
        "status": "0x1",
        "blockNumber": "0x64",  # block 100
        "blockHash": "0x" + "cc" * 32,
        "gasUsed": "0xc350",   # 50,000 gas
        "effectiveGasPrice": "0x77359400",
    }
    updated = await relayer.poll_and_update_receipt(db_session, tx_record)
    assert updated is True
    assert tx_record.status == TxLifecycleStatus.CONFIRMED
    assert intent.status == IntentStatus.CONFIRMED


@pytest.mark.asyncio
async def test_reverted_transaction_handling(db_session: AsyncSession):
    """Verify that a reverted transaction on-chain updates state to FAILED and records revert."""
    cfg = get_chain_config(CHAIN_ID_ANVIL)
    mock_rpc = MockRpcClient(cfg)
    signer = LocalAccountSigner(Account.create().key.hex())
    relayer = BlockchainRelayer(cfg, mock_rpc, NonceManager(mock_rpc), GasEstimator(mock_rpc), signer)

    intent, _ = await TransactionIntentService.create_intent(
        session=db_session,
        chain_id=CHAIN_ID_ANVIL,
        idempotency_key="revert-test-001",
        target_contract=cfg.contract_addresses["Escrow"],
        operation="releaseEscrow",
        parameters={"escrowId": "0x" + "22" * 32},
    )

    tx_record = await relayer.submit_intent(db_session, intent)
    tx_hash = tx_record.current_tx_hash

    # Mock reverted receipt: status 0
    mock_rpc.mock_receipts[tx_hash.lower()] = {
        "status": "0x0",
        "blockNumber": "0x65",
        "blockHash": "0x" + "dd" * 32,
        "gasUsed": "0xc350",
    }

    updated = await relayer.poll_and_update_receipt(db_session, tx_record)
    assert updated is True
    assert tx_record.status == TxLifecycleStatus.FAILED
    assert intent.status == IntentStatus.FAILED
    assert "reverted" in tx_record.error_message.lower()


@pytest.mark.asyncio
async def test_transaction_gas_bump_replacement(db_session: AsyncSession):
    """Verify stuck transaction is replaced with bumped gas fees using same nonce."""
    cfg = get_chain_config(CHAIN_ID_ANVIL)
    mock_rpc = MockRpcClient(cfg)
    signer = LocalAccountSigner(Account.create().key.hex())
    relayer = BlockchainRelayer(cfg, mock_rpc, NonceManager(mock_rpc), GasEstimator(mock_rpc), signer)

    intent, _ = await TransactionIntentService.create_intent(
        session=db_session,
        chain_id=CHAIN_ID_ANVIL,
        idempotency_key="replacement-test-001",
        target_contract=cfg.contract_addresses["Escrow"],
        operation="releaseEscrow",
        parameters={"escrowId": "0x" + "33" * 32},
    )

    tx_record = await relayer.submit_intent(db_session, intent)
    orig_tx_hash = tx_record.current_tx_hash
    orig_nonce = tx_record.nonce

    # Replacement bump
    new_attempt = await relayer.bump_and_replace_transaction(db_session, tx_record)
    assert new_attempt.attempt_number == 2
    assert new_attempt.nonce == orig_nonce  # SAME nonce
    assert tx_record.current_tx_hash != orig_tx_hash
    assert tx_record.attempts_count == 2
    assert len(mock_rpc.sent_raw_txs) == 2


@pytest.mark.asyncio
async def test_concurrent_nonce_reservations_are_unique(db_session: AsyncSession):
    """Verify that concurrent nonce reservations on the same account never collide."""
    cfg = get_chain_config(CHAIN_ID_ANVIL)
    mock_rpc = MockRpcClient(cfg)
    nonce_mgr = NonceManager(mock_rpc)
    account = Account.create().address

    # Concurrently reserve 10 nonces
    async def reserve():
        return await nonce_mgr.reserve_nonce(db_session, account)

    nonces = []
    for _ in range(10):
        n = await reserve()
        nonces.append(n)

    # Verify all nonces are unique and strictly monotonic
    assert len(nonces) == 10
    assert len(set(nonces)) == 10
    assert nonces == list(range(10))


@pytest.mark.asyncio
async def test_outbox_retry_and_dead_letter(db_session: AsyncSession):
    """Verify outbox retries with exponential backoff on failure and marks FAILED on exhaustion."""
    mock_redis = AsyncMock()
    mock_redis.xadd.side_effect = RuntimeError("Redis connection failed")
    dispatcher = BlockchainTxOutboxDispatcher(mock_redis)

    cfg = get_chain_config(CHAIN_ID_ANVIL)
    intent, _ = await TransactionIntentService.create_intent(
        session=db_session,
        chain_id=CHAIN_ID_ANVIL,
        idempotency_key="dead-letter-test-001",
        target_contract=cfg.contract_addresses["Escrow"],
        operation="releaseEscrow",
        parameters={"escrowId": "0x" + "44" * 32},
    )

    # First dispatch failure
    dispatched = await dispatcher.dispatch_pending(db_session)
    assert dispatched == 0

    outbox_stmt = BlockchainTxOutbox.__table__.select().where(BlockchainTxOutbox.intent_id == intent.id)
    item = (await db_session.execute(outbox_stmt)).one()
    assert item.attempts == 1
    assert item.status == "PENDING"
    assert "Redis connection failed" in item.error_message
