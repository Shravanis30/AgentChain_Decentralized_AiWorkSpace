"""Tests for Anvil Test Bootstrap and Safety Boundaries (SEC-6.11-002).

Validates:
1. Chain safety assertion strictly permits chain ID 31337 and rejects 84532, 8453, and other non-local chains.
2. Full Anvil reset reproduction: wipe bytecode -> verify absence -> bootstrap -> verify restoration.
3. Functional verification of all restored contracts on local chain.
"""

from unittest.mock import MagicMock
import pytest
from web3 import Web3

from tests.anvil_bootstrap import (
    BASE_MAINNET_CHAIN_ID,
    BASE_SEPOLIA_CHAIN_ID,
    CANONICAL_ANVIL_ADDRESSES,
    DEFAULT_ANVIL_RPC,
    LOCAL_ANVIL_CHAIN_ID,
    SecurityBoundaryViolation,
    assert_local_chain_safety,
    bootstrap_anvil,
    check_bytecode_present,
)


def test_chain_safety_allows_local_anvil():
    """Verify local chain ID 31337 is permitted without exception."""
    assert_local_chain_safety(LOCAL_ANVIL_CHAIN_ID)


def test_chain_safety_strictly_rejects_base_sepolia():
    """Verify chain ID 84532 (Base Sepolia) is strictly blocked."""
    with pytest.raises(SecurityBoundaryViolation) as exc_info:
        assert_local_chain_safety(BASE_SEPOLIA_CHAIN_ID)
    assert "strictly rejected on live/testnet" in str(exc_info.value)
    assert str(BASE_SEPOLIA_CHAIN_ID) in str(exc_info.value)


def test_chain_safety_strictly_rejects_base_mainnet():
    """Verify chain ID 8453 (Base Mainnet) is strictly blocked."""
    with pytest.raises(SecurityBoundaryViolation) as exc_info:
        assert_local_chain_safety(BASE_MAINNET_CHAIN_ID)
    assert "strictly rejected on live/testnet" in str(exc_info.value)
    assert str(BASE_MAINNET_CHAIN_ID) in str(exc_info.value)


@pytest.mark.parametrize("foreign_chain_id", [1, 137, 42161, 10, 56])
def test_chain_safety_rejects_arbitrary_foreign_chains(foreign_chain_id: int):
    """Verify non-31337 foreign chain IDs are rejected."""
    with pytest.raises(SecurityBoundaryViolation) as exc_info:
        assert_local_chain_safety(foreign_chain_id)
    assert f"requires chain ID {LOCAL_ANVIL_CHAIN_ID}" in str(exc_info.value)


def test_bootstrap_refuses_when_rpc_resolves_to_sepolia(monkeypatch):
    """Verify bootstrap_anvil aborts before deployment if RPC reports chain 84532."""
    class MockWeb3:
        HTTPProvider = MagicMock()
        def __init__(self, provider):
            self.eth = MagicMock()
            self.eth.chain_id = BASE_SEPOLIA_CHAIN_ID
        def is_connected(self):
            return True

    monkeypatch.setattr("tests.anvil_bootstrap.Web3", MockWeb3)

    with pytest.raises(SecurityBoundaryViolation) as exc_info:
        bootstrap_anvil(rpc_url="http://fake-rpc:8545")
    assert "strictly rejected on live/testnet" in str(exc_info.value)


def test_bootstrap_refuses_when_rpc_resolves_to_mainnet(monkeypatch):
    """Verify bootstrap_anvil aborts before deployment if RPC reports chain 8453."""
    class MockWeb3:
        HTTPProvider = MagicMock()
        def __init__(self, provider):
            self.eth = MagicMock()
            self.eth.chain_id = BASE_MAINNET_CHAIN_ID
        def is_connected(self):
            return True

    monkeypatch.setattr("tests.anvil_bootstrap.Web3", MockWeb3)

    with pytest.raises(SecurityBoundaryViolation) as exc_info:
        bootstrap_anvil(rpc_url="http://fake-rpc:8545")
    assert "strictly rejected on live/testnet" in str(exc_info.value)


@pytest.mark.asyncio
async def test_anvil_container_reset_and_automatic_reseed():
    """
    Automated Anvil restart/reset lifecycle test:
    1. Connect to local Anvil
    2. Reset Anvil via anvil_reset RPC (simulating container restart / ephemeral wipe)
    3. Verify bytecode absence (all contracts 0x)
    4. Execute bootstrap_anvil()
    5. Verify bytecode restored at exact deterministic addresses
    6. Execute representative contract calls to verify functional correctness
    """
    w3 = Web3(Web3.HTTPProvider(DEFAULT_ANVIL_RPC))
    assert w3.is_connected(), f"Anvil RPC not connected at {DEFAULT_ANVIL_RPC}"
    assert w3.eth.chain_id == LOCAL_ANVIL_CHAIN_ID

    # 1. Reset Anvil to simulate container wipe
    reset_res = w3.provider.make_request("anvil_reset", [])
    assert "error" not in reset_res, f"anvil_reset failed: {reset_res}"
    assert w3.eth.block_number == 0

    # 2. Verify bytecode absence
    for name, addr in CANONICAL_ANVIL_ADDRESSES.items():
        code = w3.eth.get_code(Web3.to_checksum_address(addr))
        assert len(code) == 0, f"Expected 0 bytecode for {name} after reset, got {len(code)}"
    assert not check_bytecode_present(w3, CANONICAL_ANVIL_ADDRESSES)

    # 3. Execute automated deterministic bootstrap
    deployed = bootstrap_anvil(rpc_url=DEFAULT_ANVIL_RPC, force_reseed=True)
    assert deployed == CANONICAL_ANVIL_ADDRESSES

    # 4. Verify bytecode restored
    assert check_bytecode_present(w3, CANONICAL_ANVIL_ADDRESSES)
    for name, addr in CANONICAL_ANVIL_ADDRESSES.items():
        code = w3.eth.get_code(Web3.to_checksum_address(addr))
        assert len(code) > 0, f"Expected non-empty bytecode for {name}, got 0"

    # 5. Representative functional contract calls
    usdc_abi = [
        {"name": "name", "inputs": [], "outputs": [{"name": "", "type": "string"}], "type": "function"},
        {"name": "symbol", "inputs": [], "outputs": [{"name": "", "type": "string"}], "type": "function"},
        {"name": "decimals", "inputs": [], "outputs": [{"name": "", "type": "uint8"}], "type": "function"},
    ]
    usdc = w3.eth.contract(
        address=Web3.to_checksum_address(CANONICAL_ANVIL_ADDRESSES["MockUSDC"]),
        abi=usdc_abi,
    )
    assert usdc.functions.name().call() == "USD Coin"
    assert usdc.functions.symbol().call() == "USDC"
    assert usdc.functions.decimals().call() == 6

    # Verify Escrow contract linkage
    escrow_abi = [
        {"name": "paymentToken", "inputs": [], "outputs": [{"name": "", "type": "address"}], "type": "function"},
        {"name": "distributor", "inputs": [], "outputs": [{"name": "", "type": "address"}], "type": "function"},
    ]
    escrow = w3.eth.contract(
        address=Web3.to_checksum_address(CANONICAL_ANVIL_ADDRESSES["Escrow"]),
        abi=escrow_abi,
    )
    assert escrow.functions.paymentToken().call() == Web3.to_checksum_address(CANONICAL_ANVIL_ADDRESSES["MockUSDC"])
    assert escrow.functions.distributor().call() == Web3.to_checksum_address(CANONICAL_ANVIL_ADDRESSES["RevenueDistributor"])
