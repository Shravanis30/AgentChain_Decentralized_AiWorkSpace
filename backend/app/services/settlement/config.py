"""Settlement service configuration and hard security boundaries for Phase 6.2B."""

import os
from app.services.blockchain.config import (
    CHAIN_ID_ANVIL,
    CHAIN_ID_BASE_SEPOLIA,
    CHAIN_ID_BASE_MAINNET,
    BASE_SEPOLIA_USDC,
    BASE_MAINNET_USDC,
    get_chain_config,
)

# Hard gate: Base Mainnet settlement transaction submission MUST remain disabled
BLOCK_MAINNET_SETTLEMENT: bool = (
    os.getenv("BLOCK_MAINNET_SETTLEMENT", "true").lower() == "true"
)

# Supported chains for settlement
SUPPORTED_SETTLEMENT_CHAINS: frozenset[int] = frozenset({
    CHAIN_ID_ANVIL,
    CHAIN_ID_BASE_SEPOLIA,
    CHAIN_ID_BASE_MAINNET,
})

# Minimum confirmation thresholds required before an on-chain escrow event can be used for settlement
SETTLEMENT_CONFIRMATION_THRESHOLDS: dict[int, int] = {
    CHAIN_ID_ANVIL: int(os.getenv("ANVIL_CONFIRMATIONS", "1")),
    CHAIN_ID_BASE_SEPOLIA: int(os.getenv("BASE_SEPOLIA_CONFIRMATIONS", "3")),
    CHAIN_ID_BASE_MAINNET: int(os.getenv("BASE_MAINNET_CONFIRMATIONS", "12")),
}


def is_chain_supported(chain_id: int) -> bool:
    return chain_id in SUPPORTED_SETTLEMENT_CHAINS


def get_required_confirmations(chain_id: int) -> int:
    return SETTLEMENT_CONFIRMATION_THRESHOLDS.get(chain_id, 12)


# Authoritative reorg tracking window
MAX_REORG_DEPTH: int = int(os.getenv("MAX_REORG_DEPTH", "32"))


def get_max_reorg_depth(chain_id: int) -> int:
    try:
        cfg = get_chain_config(chain_id)
        return cfg.max_reorg_depth
    except Exception:
        return MAX_REORG_DEPTH
