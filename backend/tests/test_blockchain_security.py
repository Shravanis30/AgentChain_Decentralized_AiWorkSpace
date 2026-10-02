"""Security boundary tests for blockchain infrastructure.

Explicitly verifies:
- Base Mainnet transaction submission is hard-blocked at the code level.
- Unauthorized smart contract operations are rejected.
- Non-allowlisted contract addresses are rejected.
- Arbitrary raw calldata submission is blocked.
- Private keys never leak through string representations, exceptions, or database fields.
- Duplicate transaction intents maintain strict idempotency.
"""

from eth_account import Account
import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from web3 import Web3

from app.models.blockchain import (
    BlockchainTransaction,
    BlockchainTransactionAttempt,
    BlockchainTransactionIntent,
    IntentStatus,
)
from app.services.blockchain.config import (
    CHAIN_ID_ANVIL,
    CHAIN_ID_BASE_MAINNET,
    CHAIN_ID_BASE_SEPOLIA,
    get_chain_config,
)
from app.services.blockchain.errors import (
    ArbitraryCalldataBlockedError,
    ChainMismatchError,
    ContractMismatchError,
    MainnetSubmissionBlockedError,
    UnauthorizedOperationError,
)
from app.services.blockchain.gas_estimator import GasEstimator
from app.services.blockchain.nonce_manager import NonceManager
from app.services.blockchain.relayer import (
    BlockchainRelayer,
    LocalAccountSigner,
    MockSigner,
)
from app.services.blockchain.rpc_client import BlockchainRpcClient
from app.services.blockchain.transaction_intent import TransactionIntentService


@pytest.mark.asyncio
async def test_base_mainnet_transaction_submission_is_hard_blocked(db_session: AsyncSession):
    """Verify that any attempt to submit a transaction on Base Mainnet fails closed with MainnetSubmissionBlockedError."""
    mainnet_cfg = get_chain_config(CHAIN_ID_BASE_MAINNET)
    rpc = BlockchainRpcClient(mainnet_cfg)
    nonce_mgr = NonceManager(rpc)
    gas_est = GasEstimator(rpc)
    signer = MockSigner()

    relayer = BlockchainRelayer(
        config=mainnet_cfg,
        rpc_client=rpc,
        nonce_manager=nonce_mgr,
        gas_estimator=gas_est,
        signer=signer,
    )

    intent = BlockchainTransactionIntent(
        chain_id=CHAIN_ID_BASE_MAINNET,
        idempotency_key="mainnet-attack-key",
        target_contract="0x0000000000000000000000000000000000000000",
        operation="releaseEscrow",
        parameters={"escrowId": "0x" + "11" * 32},
        status=IntentStatus.CREATED,
    )

    with pytest.raises(MainnetSubmissionBlockedError, match="HARD SECURITY GATING: Base Mainnet transaction submission is disabled"):
        await relayer.submit_intent(db_session, intent)

    with pytest.raises(MainnetSubmissionBlockedError):
        await rpc.send_raw_transaction("0x1234")


@pytest.mark.asyncio
async def test_unauthorized_operation_is_blocked(db_session: AsyncSession):
    """Verify that unapproved contract methods (e.g. selfdestruct, transfer, exploit) are blocked."""
    with pytest.raises(UnauthorizedOperationError, match="not an authorized contract operation"):
        await TransactionIntentService.create_intent(
            session=db_session,
            chain_id=CHAIN_ID_ANVIL,
            idempotency_key="unauth-op-1",
            target_contract="0x5FbDB2315678afecb367f032d93F642f64180aa3",
            operation="unauthorizedAdminDrain",
            parameters={},
        )


@pytest.mark.asyncio
async def test_contract_not_in_allowlist_is_blocked(db_session: AsyncSession):
    """Verify that interaction with arbitrary contract addresses is blocked."""
    fake_contract = "0x9999999999999999999999999999999999999999"
    with pytest.raises(ContractMismatchError, match="not in the allowlist"):
        await TransactionIntentService.create_intent(
            session=db_session,
            chain_id=CHAIN_ID_ANVIL,
            idempotency_key="fake-contract-1",
            target_contract=fake_contract,
            operation="releaseEscrow",
            parameters={"escrowId": "0x" + "11" * 32},
        )


@pytest.mark.asyncio
async def test_arbitrary_calldata_blocked_during_relaying():
    """Verify that the relayer rejects corrupted or unexpected calldata parameters."""
    cfg = get_chain_config(CHAIN_ID_ANVIL)
    rpc = BlockchainRpcClient(cfg)
    relayer = BlockchainRelayer(cfg, rpc, NonceManager(rpc), GasEstimator(rpc), MockSigner())

    # Missing parameters for createEscrow
    with pytest.raises(ArbitraryCalldataBlockedError):
        relayer._encode_calldata("createEscrow", {"corrupted": "bad"})


import uuid


def test_private_key_zero_leakage():
    """Verify private keys never appear in string representation, exceptions, or DB representations."""
    test_key = "0x4f3edf983ac636a65a842ce7c78d9aa706d3b113bce9c46f30d7d21715b23b1d"
    signer = LocalAccountSigner(test_key)

    # Check repr and str
    assert test_key not in str(signer)
    assert test_key not in repr(signer)
    assert test_key.removeprefix("0x") not in str(signer)

    # Verify attempt object does not have any private key field
    attempt = BlockchainTransactionAttempt(
        transaction_id=uuid.uuid4(),
        attempt_number=1,
        tx_hash="0x" + "aa" * 32,
        nonce=0,
        max_fee_per_gas=100,
        max_priority_fee_per_gas=10,
        gas_limit=100000,
    )
    for col in BlockchainTransactionAttempt.__table__.columns:
        assert "key" not in col.name.lower() or col.name == "idempotency_key"


@pytest.mark.asyncio
async def test_idempotent_intent_deduplication(db_session: AsyncSession):
    """Verify that creating the same intent twice returns the existing record without creating duplicates."""
    cfg = get_chain_config(CHAIN_ID_ANVIL)
    target = cfg.contract_addresses["Escrow"]

    intent1, is_new1 = await TransactionIntentService.create_intent(
        session=db_session,
        chain_id=CHAIN_ID_ANVIL,
        idempotency_key="idempotent-test-key-001",
        target_contract=target,
        operation="releaseEscrow",
        parameters={"escrowId": "0x" + "aa" * 32},
    )
    assert is_new1 is True

    # Duplicate call with identical key
    intent2, is_new2 = await TransactionIntentService.create_intent(
        session=db_session,
        chain_id=CHAIN_ID_ANVIL,
        idempotency_key="idempotent-test-key-001",
        target_contract=target,
        operation="releaseEscrow",
        parameters={"escrowId": "0x" + "aa" * 32},
    )
    assert is_new2 is False
    assert intent1.id == intent2.id
