import os
from typing import NamedTuple
from web3 import Web3

from app.services.blockchain.config import (
    CHAIN_ID_BASE_MAINNET,
    CHAIN_ID_BASE_SEPOLIA,
    CHAIN_ID_LOCAL,
)
from app.services.distribution.errors import (
    DistributionConservationError,
    DistributionDuplicateRecipientError,
)

DISTRIBUTION_VERSION = 1
DEVELOPER_PERCENTAGE = 85
STAKER_PERCENTAGE = 10
DAO_PERCENTAGE = 5

ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"


class PlatformDistributionRecipients(NamedTuple):
    staker_recipient: str
    dao_recipient: str


# Authoritative platform recipient defaults
DEFAULT_RECIPIENTS: dict[int, PlatformDistributionRecipients] = {
    CHAIN_ID_LOCAL: PlatformDistributionRecipients(
        staker_recipient=Web3.to_checksum_address("0x51a0000000000000000000000000000000000001"),
        dao_recipient=Web3.to_checksum_address("0xda00000000000000000000000000000000000002"),
    ),
    CHAIN_ID_BASE_SEPOLIA: PlatformDistributionRecipients(
        staker_recipient=Web3.to_checksum_address("0x51a0000000000000000000000000000000000001"),
        dao_recipient=Web3.to_checksum_address("0xda00000000000000000000000000000000000002"),
    ),
    CHAIN_ID_BASE_MAINNET: PlatformDistributionRecipients(
        staker_recipient=Web3.to_checksum_address("0x51a0000000000000000000000000000000000001"),
        dao_recipient=Web3.to_checksum_address("0xda00000000000000000000000000000000000002"),
    ),
}


def compute_85_10_5_split(gross_amount: int) -> tuple[int, int, int]:
    """Computes deterministic 85/10/5 integer split in token base units.
    
    Invariant:
        gross_amount == developer_amount + staker_amount + dao_amount
    
    Developer: floor(gross * 85 / 100)
    Staker:    floor(gross * 10 / 100)
    DAO:       gross - developer - staker (exact remainder)
    """
    if gross_amount <= 0:
        raise ValueError(f"gross_amount must be strictly positive, got {gross_amount}")

    developer_amount = (gross_amount * 85) // 100
    staker_amount = (gross_amount * 10) // 100
    dao_amount = gross_amount - developer_amount - staker_amount

    if developer_amount + staker_amount + dao_amount != gross_amount:
        raise DistributionConservationError(
            f"Conservation violated: dev({developer_amount}) + stk({staker_amount}) + dao({dao_amount}) != gross({gross_amount})"
        )

    return developer_amount, staker_amount, dao_amount


def get_platform_recipients(chain_id: int) -> PlatformDistributionRecipients:
    """Resolves authoritative platform staker and DAO recipients for a given chain."""
    defaults = DEFAULT_RECIPIENTS.get(chain_id)
    if not defaults:
        raise ValueError(f"Unsupported chain ID for distribution: {chain_id}")

    staker_env = os.getenv("STAKER_RECIPIENT_ADDRESS")
    dao_env = os.getenv("DAO_RECIPIENT_ADDRESS")

    staker = Web3.to_checksum_address(staker_env) if staker_env else defaults.staker_recipient
    dao = Web3.to_checksum_address(dao_env) if dao_env else defaults.dao_recipient

    if staker == ZERO_ADDRESS:
        raise ValueError("Staker recipient cannot be zero address")
    if dao == ZERO_ADDRESS:
        raise ValueError("DAO recipient cannot be zero address")
    if staker.lower() == dao.lower():
        raise DistributionDuplicateRecipientError("Staker recipient and DAO recipient cannot be identical")

    return PlatformDistributionRecipients(staker_recipient=staker, dao_recipient=dao)


def validate_distribution_recipients(
    developer_recipient: str,
    staker_recipient: str,
    dao_recipient: str,
) -> None:
    """Validates that all recipients are non-zero, checksummed, and mutually distinct."""
    if not Web3.is_address(developer_recipient) or Web3.to_checksum_address(developer_recipient) == ZERO_ADDRESS:
        raise ValueError("Developer recipient must be a valid non-zero address")
    if not Web3.is_address(staker_recipient) or Web3.to_checksum_address(staker_recipient) == ZERO_ADDRESS:
        raise ValueError("Staker recipient must be a valid non-zero address")
    if not Web3.is_address(dao_recipient) or Web3.to_checksum_address(dao_recipient) == ZERO_ADDRESS:
        raise ValueError("DAO recipient must be a valid non-zero address")

    dev_clean = developer_recipient.lower()
    stk_clean = staker_recipient.lower()
    dao_clean = dao_recipient.lower()

    if stk_clean == dao_clean:
        raise DistributionDuplicateRecipientError("Staker and DAO recipients cannot be identical")
    if dev_clean == stk_clean:
        raise DistributionDuplicateRecipientError("Developer recipient cannot match staker recipient")
    if dev_clean == dao_clean:
        raise DistributionDuplicateRecipientError("Developer recipient cannot match DAO recipient")
