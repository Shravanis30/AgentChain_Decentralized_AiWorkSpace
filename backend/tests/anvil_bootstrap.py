"""Deterministic Anvil Test Bootstrap & Chain Safety Boundary.

Provides automated, reproducible contract deployment for local Anvil test environments
when the container restarts or memory resets.

STRICT SAFETY ENFORCEMENT:
- Operates ONLY on local Anvil (CHAIN_ID == 31337).
- Refuses to execute against Base Sepolia (84532), Base Mainnet (8453), or any non-local network.
- Uses deterministic local Anvil test credentials only.
"""

from dataclasses import dataclass
import os
from pathlib import Path
import shutil
import subprocess
from typing import Any
from web3 import Web3


LOCAL_ANVIL_CHAIN_ID: int = 31337
BASE_SEPOLIA_CHAIN_ID: int = 84532
BASE_MAINNET_CHAIN_ID: int = 8453

BLOCKED_CHAIN_IDS: frozenset[int] = frozenset({
    BASE_SEPOLIA_CHAIN_ID,
    BASE_MAINNET_CHAIN_ID,
})

DEFAULT_ANVIL_RPC: str = "http://127.0.0.1:8545"

# Canonical addresses deployed by contracts/script/Deploy.s.sol at nonce 0
CANONICAL_ANVIL_ADDRESSES: dict[str, str] = {
    "MockUSDC": "0x5FbDB2315678afecb367f032d93F642f64180aa3",
    "AgentRegistry": "0xe7f1725E7734CE288F8367e1Bb143E90bb3F0512",
    "RevenueDistributor": "0x9fE46736679d2D9a65F0992F2272dE9f3c7fa6e0",
    "Escrow": "0xCf7Ed3AccA5a467e9e704C703E8D87F634fB0Fc9",
    "ResultNotary": "0x5FC8d32690cc91D4c39d9d3abcBD16989F875707",
    "ReputationRegistry": "0x0165878A594ca255338adfa4d48449f69242Eb8F",
}


class SecurityBoundaryViolation(Exception):
    """Raised when an operation attempts to run against a forbidden network."""
    pass


class BootstrapError(Exception):
    """Raised when Anvil seeding fails."""
    pass


def assert_local_chain_safety(chain_id: int) -> None:
    """Strictly assert chain ID is local Anvil (31337) and reject live/testnet chains."""
    if chain_id in BLOCKED_CHAIN_IDS:
        raise SecurityBoundaryViolation(
            f"SECURITY VIOLATION: Local Anvil bootstrap strictly rejected on live/testnet network {chain_id}!"
        )
    if chain_id != LOCAL_ANVIL_CHAIN_ID:
        raise SecurityBoundaryViolation(
            f"SECURITY VIOLATION: Local Anvil bootstrap requires chain ID {LOCAL_ANVIL_CHAIN_ID}, got {chain_id}."
        )


def check_bytecode_present(w3: Web3, addresses: dict[str, str]) -> bool:
    """Return True if all expected contract addresses have non-empty bytecode."""
    for name, addr in addresses.items():
        try:
            code = w3.eth.get_code(Web3.to_checksum_address(addr))
            if len(code) == 0:
                return False
        except Exception:
            return False
    return True


def bootstrap_anvil(
    rpc_url: str = DEFAULT_ANVIL_RPC,
    force_reseed: bool = False,
    contracts_dir: Path | str | None = None,
) -> dict[str, str]:
    """
    Ensure local Anvil instance is connected, verified, and has contract bytecode deployed.

    1. Verifies Anvil RPC connection.
    2. Validates chain ID == 31337 (refuses 84532, 8453, or non-local chains).
    3. Checks if canonical contract bytecodes exist.
    4. If missing or force_reseed: deterministically reseeds via Deploy.s.sol.
    5. Validates resulting addresses and returns them.
    """
    w3 = Web3(Web3.HTTPProvider(rpc_url))
    if not w3.is_connected():
        raise ConnectionError(f"Cannot connect to Anvil RPC at {rpc_url}")

    chain_id = w3.eth.chain_id
    assert_local_chain_safety(chain_id)

    # Check if bytecode is already deployed
    if not force_reseed and check_bytecode_present(w3, CANONICAL_ANVIL_ADDRESSES):
        return dict(CANONICAL_ANVIL_ADDRESSES)

    # Locate contracts directory
    if contracts_dir is None:
        contracts_dir = Path(__file__).resolve().parent.parent.parent / "contracts"
    else:
        contracts_dir = Path(contracts_dir)

    if not (contracts_dir / "script" / "Deploy.s.sol").exists():
        raise BootstrapError(f"Deploy.s.sol not found in {contracts_dir}")

    # Locate forge binary
    forge_bin = shutil.which("forge") or os.path.expanduser("~/.foundry/bin/forge")
    if not os.path.exists(forge_bin):
        raise BootstrapError(f"Foundry 'forge' binary not found at {forge_bin}")

    # Reset Anvil to ensure clean state and nonce 0 for deterministic addresses
    try:
        w3.provider.make_request("anvil_reset", [])
    except Exception as reset_err:
        pass

    # Execute deterministic forge deployment with local Anvil key
    env = os.environ.copy()
    env["DEPLOYER_PRIVATE_KEY"] = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
    # Ensure MockUSDC is deployed at deterministic local address
    env["USDC_ADDRESS"] = "0x0000000000000000000000000000000000000000"

    cmd = [
        str(forge_bin),
        "script",
        "script/Deploy.s.sol:DeployScript",
        "--rpc-url",
        rpc_url,
        "--broadcast",
        "--legacy",
    ]

    result = subprocess.run(
        cmd,
        cwd=str(contracts_dir),
        capture_output=True,
        text=True,
        env=env,
    )

    if result.returncode != 0:
        raise BootstrapError(
            f"Anvil seeding script failed with code {result.returncode}:\n"
            f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
        )

    # Validate resulting addresses
    missing: list[str] = []
    for name, addr in CANONICAL_ANVIL_ADDRESSES.items():
        code = w3.eth.get_code(Web3.to_checksum_address(addr))
        if len(code) == 0:
            missing.append(f"{name} ({addr})")

    if missing:
        raise BootstrapError(
            f"Post-bootstrap validation failed: bytecode missing for {', '.join(missing)}"
        )

    return dict(CANONICAL_ANVIL_ADDRESSES)
