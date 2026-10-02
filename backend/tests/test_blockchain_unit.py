"""Unit tests for blockchain infrastructure: ABI decoding, gas bumping, and error classification."""

from eth_account import Account
import pytest
from web3 import Web3

from app.services.blockchain.abi import (
    ESCROW_ABI,
    EVENT_TOPICS,
    decode_escrow_log,
    get_event_abi_by_topic,
)
from app.services.blockchain.config import (
    ALLOWED_OPERATIONS,
    CHAIN_ID_ANVIL,
    CHAIN_ID_BASE_MAINNET,
    CHAIN_ID_BASE_SEPOLIA,
    get_chain_config,
)
from app.services.blockchain.errors import (
    ArbitraryCalldataBlockedError,
    BlockchainError,
    ChainMismatchError,
    ContractMismatchError,
    ErrorClassification,
    MainnetSubmissionBlockedError,
    PermanentRpcError,
    TransientRpcError,
    UnauthorizedOperationError,
    classify_rpc_error,
)
from app.services.blockchain.gas_estimator import (
    DEFAULT_GAS_LIMIT,
    DEFAULT_PRIORITY_FEE_WEI,
    MAX_FEE_CEILING_WEI,
    GasEstimator,
)
from app.services.blockchain.rpc_client import BlockchainRpcClient


def test_chain_configurations():
    """Verify supported chain configs and safety boundaries."""
    anvil_cfg = get_chain_config(CHAIN_ID_ANVIL)
    assert anvil_cfg.chain_id == 31337
    assert anvil_cfg.allow_transactions is True
    assert anvil_cfg.confirmations_required == 1

    sepolia_cfg = get_chain_config(CHAIN_ID_BASE_SEPOLIA)
    assert sepolia_cfg.chain_id == 84532
    assert sepolia_cfg.confirmations_required == 3

    mainnet_cfg = get_chain_config(CHAIN_ID_BASE_MAINNET)
    assert mainnet_cfg.chain_id == 8453
    assert mainnet_cfg.confirmations_required == 12
    # CRITICAL: Mainnet transaction submission must be disabled
    assert mainnet_cfg.allow_transactions is False

    with pytest.raises(ValueError, match="Unsupported or unconfigured chain ID"):
        get_chain_config(999999)


def test_allowed_operations_allowlist():
    """Verify only allowlisted operations are permitted."""
    expected = {
        "createEscrow",
        "createAndFundEscrow",
        "fundEscrow",
        "lockEscrow",
        "releaseEscrow",
        "refundEscrow",
        "disputeEscrow",
        "resolveDispute",
        "pause",
        "unpause",
        "notarizeResult",
        "registerReputationEvent",
    }
    assert ALLOWED_OPERATIONS == expected


def test_error_classification():
    """Verify error categorization into TRANSIENT, PERMANENT, and UNKNOWN."""
    assert classify_rpc_error(TransientRpcError("Timeout")) == ErrorClassification.TRANSIENT
    assert classify_rpc_error(PermanentRpcError("Bad call")) == ErrorClassification.PERMANENT
    assert classify_rpc_error(MainnetSubmissionBlockedError("Blocked")) == ErrorClassification.PERMANENT
    assert classify_rpc_error(ChainMismatchError("Mismatch")) == ErrorClassification.PERMANENT

    # String matching heuristics
    assert classify_rpc_error(Exception("429 Too Many Requests")) == ErrorClassification.TRANSIENT
    assert classify_rpc_error(Exception("Connection reset by peer")) == ErrorClassification.TRANSIENT
    assert classify_rpc_error(Exception("execution reverted: Unauthorized")) == ErrorClassification.PERMANENT
    assert classify_rpc_error(Exception("Random unexpected error")) == ErrorClassification.UNKNOWN


def test_escrow_abi_event_topics():
    """Verify event topic definitions exist for all 9 Escrow contract events."""
    event_names = {item["name"] for item in EVENT_TOPICS.values()}
    required_events = {
        "EscrowCreated",
        "EscrowFunded",
        "EscrowLocked",
        "EscrowReleased",
        "EscrowRefunded",
        "DisputeOpened",
        "DisputeResolved",
        "Paused",
        "Unpaused",
    }
    assert required_events.issubset(event_names)


def test_decode_escrow_created_log():
    """Verify typed decoding of EscrowCreated log."""
    w3 = Web3()
    contract = w3.eth.contract(abi=ESCROW_ABI)

    escrow_id = bytes.fromhex("11" * 32)
    client_addr = Web3.to_checksum_address("0x1111111111111111111111111111111111111111")
    beneficiary_addr = Web3.to_checksum_address("0x2222222222222222222222222222222222222222")
    amount = 5_000_000
    ref_id = bytes.fromhex("33" * 32)
    deadline = 1_800_000_000

    # Create mock event receipt
    # Topic 0: event sig, Topic 1: escrowId, Topic 2: client, Topic 3: beneficiary
    event_abi = [x for x in ESCROW_ABI if x.get("name") == "EscrowCreated"][0]
    sig = "EscrowCreated(bytes32,address,address,uint256,bytes32,uint256)"
    topic0 = "0x" + Web3.keccak(text=sig).hex()
    topic1 = "0x" + escrow_id.hex()
    topic2 = "0x" + ("00" * 12) + client_addr[2:].lower()
    topic3 = "0x" + ("00" * 12) + beneficiary_addr[2:].lower()

    # Data: amount (uint256), refId (bytes32), deadline (uint256)
    data = "0x" + hex(amount)[2:].zfill(64) + ref_id.hex() + hex(deadline)[2:].zfill(64)

    raw_log = {
        "address": "0x5FbDB2315678afecb367f032d93F642f64180aa3",
        "topics": [topic0, topic1, topic2, topic3],
        "data": data,
        "blockNumber": "0xa",
        "blockHash": "0x" + "aa" * 32,
        "transactionHash": "0x" + "bb" * 32,
        "transactionIndex": "0x0",
        "logIndex": "0x0",
    }

    decoded = decode_escrow_log(raw_log)
    assert decoded is not None
    event_name, args = decoded
    assert event_name == "EscrowCreated"
    assert args["escrowId"] == "0x" + escrow_id.hex().lower()
    assert args["client"].lower() == client_addr.lower()
    assert args["beneficiary"].lower() == beneficiary_addr.lower()
    assert int(args["amount"]) == amount


def test_decode_unrelated_log_returns_none():
    """Verify non-escrow logs return None safely without raising."""
    unknown_log = {
        "address": "0x1234567890123456789012345678901234567890",
        "topics": ["0x" + "99" * 32],
        "data": "0x00",
    }
    assert decode_escrow_log(unknown_log) is None


def test_gas_estimator_bumping():
    """Verify gas bumping logic satisfies the minimum 10% EIP-1559 requirement and caps at ceiling."""
    cfg = get_chain_config(CHAIN_ID_ANVIL)
    rpc = BlockchainRpcClient(cfg)
    estimator = GasEstimator(rpc, max_fee_ceiling=MAX_FEE_CEILING_WEI)

    old_max_fee = 10_000_000_000      # 10 gwei
    old_priority_fee = 1_000_000_000  # 1 gwei

    bumped_max, bumped_priority = estimator.bump_gas_fees(
        old_max_fee=old_max_fee,
        old_priority_fee=old_priority_fee,
        bump_percent=12.5,
    )

    # Must be at least 10% higher
    assert bumped_max >= int(old_max_fee * 1.10)
    assert bumped_priority >= int(old_priority_fee * 1.10)
    assert bumped_max == int(old_max_fee * 1.125)
    assert bumped_priority == int(old_priority_fee * 1.125)

    # Test ceiling cap
    extreme_max = MAX_FEE_CEILING_WEI - 1000
    capped_max, _ = estimator.bump_gas_fees(old_max_fee=extreme_max, old_priority_fee=1000)
    assert capped_max == MAX_FEE_CEILING_WEI
