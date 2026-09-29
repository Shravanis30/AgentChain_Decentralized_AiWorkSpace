"""Blockchain network configuration and security boundaries for AgentChain.

Explicitly enforces:
- Configured chain IDs (Anvil: 31337, Base Sepolia: 84532, Base Mainnet: 8453).
- Official Circle USDC addresses.
- Strict confirmation policies per network.
- Hard-disabled transaction submission on Base Mainnet.
- Allowed contract operations allowlist.
"""

from dataclasses import dataclass, field
import os
from typing import Any
from dotenv import load_dotenv

load_dotenv()

# Chain IDs
CHAIN_ID_ANVIL: int = 31337
CHAIN_ID_LOCAL: int = 31337
CHAIN_ID_BASE_SEPOLIA: int = 84532
CHAIN_ID_BASE_MAINNET: int = 8453

# Verified official Circle USDC addresses
BASE_MAINNET_USDC: str = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
BASE_SEPOLIA_USDC: str = "0x036CbD53842c5426634e7929541eC2318f3dCF7e"

# Strict allowlist of permissible Escrow and ResultNotary smart contract operations
ALLOWED_OPERATIONS: frozenset[str] = frozenset({
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
})


@dataclass(frozen=True)
class ChainConfig:
    chain_id: int
    network_name: str
    rpc_url: str
    confirmations_required: int
    contract_addresses: dict[str, str] = field(default_factory=dict)
    token_address: str = ""
    enabled: bool = True
    allow_transactions: bool = False  # Hard safety gate
    max_reorg_depth: int = 32  # Authoritative reorg tracking window


def get_chain_configs() -> dict[int, ChainConfig]:
    """Returns the immutable registry of supported chain configurations."""
    escrow_anvil = os.getenv("ESCROW_CONTRACT_ANVIL", "0xCf7Ed3AccA5a467e9e704C703E8D87F634fB0Fc9")
    escrow_sepolia = os.getenv("ESCROW_CONTRACT_BASE_SEPOLIA", "0x0000000000000000000000000000000000000000")
    escrow_mainnet = os.getenv("ESCROW_CONTRACT_BASE_MAINNET", "0x0000000000000000000000000000000000000000")
    notary_anvil = os.getenv("RESULT_NOTARY_CONTRACT_ANVIL", "0x5FC8d32690cc91D4c39d9d3abcBD16989F875707")
    notary_sepolia = os.getenv("RESULT_NOTARY_CONTRACT_BASE_SEPOLIA", "0x0000000000000000000000000000000000000000")
    notary_mainnet = os.getenv("RESULT_NOTARY_CONTRACT_BASE_MAINNET", "0x0000000000000000000000000000000000000000")
    reputation_anvil = os.getenv("REPUTATION_REGISTRY_CONTRACT_ANVIL", "0x0165878A594ca255338adfa4d48449f69242Eb8F")
    reputation_sepolia = os.getenv("REPUTATION_REGISTRY_CONTRACT_BASE_SEPOLIA", "0x0000000000000000000000000000000000000000")
    reputation_mainnet = os.getenv("REPUTATION_REGISTRY_CONTRACT_BASE_MAINNET", "0x0000000000000000000000000000000000000000")
    reorg_depth = int(os.getenv("MAX_REORG_DEPTH", "32"))

    return {
        CHAIN_ID_ANVIL: ChainConfig(
            chain_id=CHAIN_ID_ANVIL,
            network_name="anvil",
            rpc_url=os.getenv("ANVIL_RPC_URL", "http://127.0.0.1:8545"),
            confirmations_required=int(os.getenv("ANVIL_CONFIRMATIONS", "1")),
            contract_addresses={
                "Escrow": escrow_anvil,
                "RevenueDistributor": os.getenv("REVENUE_DISTRIBUTOR_CONTRACT_ANVIL", "0x9fE46736679d2D9a65F0992F2272dE9f3c7fa6e0"),
                "ResultNotary": notary_anvil,
                "ReputationRegistry": reputation_anvil,
            },
            token_address=os.getenv("MOCK_USDC_CONTRACT", "0x5FbDB2315678afecb367f032d93F642f64180aa3"),
            enabled=True,
            allow_transactions=True,  # Local test operations allowed
            max_reorg_depth=reorg_depth,
        ),
        CHAIN_ID_BASE_SEPOLIA: ChainConfig(
            chain_id=CHAIN_ID_BASE_SEPOLIA,
            network_name="base-sepolia",
            rpc_url=os.getenv("BASE_SEPOLIA_RPC_URL", "https://sepolia.base.org"),
            confirmations_required=int(os.getenv("BASE_SEPOLIA_CONFIRMATIONS", "3")),
            contract_addresses={
                "Escrow": escrow_sepolia,
                "RevenueDistributor": os.getenv("REVENUE_DISTRIBUTOR_CONTRACT_BASE_SEPOLIA", "0x0000000000000000000000000000000000000000"),
                "ResultNotary": notary_sepolia,
                "ReputationRegistry": reputation_sepolia,
            },
            token_address=BASE_SEPOLIA_USDC,
            enabled=True,
            allow_transactions=os.getenv("ALLOW_SEPOLIA_TRANSACTIONS", "true").lower() == "true",
            max_reorg_depth=reorg_depth,
        ),
        CHAIN_ID_BASE_MAINNET: ChainConfig(
            chain_id=CHAIN_ID_BASE_MAINNET,
            network_name="base-mainnet",
            rpc_url=os.getenv("BASE_MAINNET_RPC_URL", "https://mainnet.base.org"),
            confirmations_required=int(os.getenv("BASE_MAINNET_CONFIRMATIONS", "12")),
            contract_addresses={
                "Escrow": escrow_mainnet,
                "RevenueDistributor": os.getenv("REVENUE_DISTRIBUTOR_CONTRACT_BASE_MAINNET", "0x0000000000000000000000000000000000000000"),
                "ResultNotary": notary_mainnet,
                "ReputationRegistry": reputation_mainnet,
            },
            token_address=BASE_MAINNET_USDC,
            enabled=True,
            # HARD STOP: Base Mainnet transaction submission MUST remain disabled in Phase 6.2A
            allow_transactions=False,
            max_reorg_depth=reorg_depth,
        ),
    }



def get_chain_config(chain_id: int) -> ChainConfig:
    """Retrieve verified chain config or fail closed."""
    configs = get_chain_configs()
    if chain_id not in configs:
        raise ValueError(f"Unsupported or unconfigured chain ID: {chain_id}")
    config = configs[chain_id]
    if not config.enabled:
        raise ValueError(f"Chain {chain_id} ({config.network_name}) is currently disabled")
    return config
