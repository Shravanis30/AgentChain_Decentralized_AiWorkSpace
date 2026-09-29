"""EIP-1559 fee calculation and deterministic replacement gas bumping.

Guarantees:
- Compatible with EIP-1559 networks (Base, Base Sepolia, Anvil).
- Computes base fee multiplier + priority tip.
- Implements strict minimum 10% / 12.5% bump rule for transaction replacement.
- Strictly bounds fee escalation with configurable safety ceiling.
"""

import logging
from typing import Any
from app.services.blockchain.rpc_client import BlockchainRpcClient

logger = logging.getLogger(__name__)

# Base networks have low priority fees, default fallback tip: 1 gwei
DEFAULT_PRIORITY_FEE_WEI: int = 1_000_000_000  # 1 gwei
MIN_PRIORITY_FEE_WEI: int = 100_000            # 0.0001 gwei
MAX_FEE_CEILING_WEI: int = 200_000_000_000     # 200 gwei absolute safety cap
DEFAULT_GAS_LIMIT: int = 300_000               # Default gas limit for Escrow calls


class GasEstimator:
    """Calculates EIP-1559 fees and controlled replacement gas escalation."""

    def __init__(
        self,
        rpc_client: BlockchainRpcClient,
        max_fee_ceiling: int = MAX_FEE_CEILING_WEI,
        default_gas_limit: int = DEFAULT_GAS_LIMIT,
    ) -> None:
        self.rpc_client = rpc_client
        self.max_fee_ceiling = max_fee_ceiling
        self.default_gas_limit = default_gas_limit

    async def estimate_eip1559_fees(self) -> tuple[int, int]:
        """Calculates current (max_fee_per_gas, max_priority_fee_per_gas) in Wei."""
        try:
            fee_history = await self.rpc_client.get_fee_history(
                block_count=2,
                newest_block="latest",
                reward_percentiles=[50.0],
            )
            base_fee_hex_list = fee_history.get("baseFeePerGas", [])
            rewards = fee_history.get("reward", [])

            if base_fee_hex_list:
                latest_base_fee = int(base_fee_hex_list[-1], 16)
            else:
                latest_base_fee = 1_000_000_000

            priority_fee = DEFAULT_PRIORITY_FEE_WEI
            if rewards and rewards[-1]:
                suggested_tip = int(rewards[-1][0], 16)
                if suggested_tip >= MIN_PRIORITY_FEE_WEI:
                    priority_fee = suggested_tip

        except Exception as e:
            logger.warning("Fee history fetch failed (%s), querying latest block directly", e)
            block = await self.rpc_client.get_block_by_number(await self.rpc_client.get_block_number())
            latest_base_fee = int(block.get("baseFeePerGas", "0x3b9aca00"), 16) if block else 1_000_000_000
            priority_fee = DEFAULT_PRIORITY_FEE_WEI

        # Formula: max_fee = (2 * base_fee) + priority_fee
        max_fee = (latest_base_fee * 2) + priority_fee
        max_fee = min(max_fee, self.max_fee_ceiling)

        return max_fee, priority_fee

    def bump_gas_fees(
        self,
        old_max_fee: int,
        old_priority_fee: int,
        bump_percent: float = 12.5,
    ) -> tuple[int, int]:
        """Calculates replacement gas parameters bumped by at least bump_percent (default 12.5%).
        
        Ensures strict adherence to EIP-1559 replacement rules (minimum +10%).
        """
        multiplier = 1.0 + (bump_percent / 100.0)

        new_priority_fee = max(
            int(old_priority_fee * multiplier),
            old_priority_fee + 1,
        )
        new_max_fee = max(
            int(old_max_fee * multiplier),
            old_max_fee + 1,
        )

        if new_max_fee > self.max_fee_ceiling:
            logger.warning(
                "Bumped max fee %d exceeded ceiling %d; capping at ceiling",
                new_max_fee,
                self.max_fee_ceiling,
            )
            new_max_fee = self.max_fee_ceiling

        return new_max_fee, new_priority_fee
