"""Comprehensive test suite for Phase 6.2C Revenue Distribution accounting, validation, and invariants."""

import random
import uuid
import pytest
from web3 import Web3

from app.models.distribution import DistributionStatus
from app.models.settlement import SettlementAction, SettlementStatus
from app.services.blockchain.config import (
    CHAIN_ID_BASE_MAINNET,
    CHAIN_ID_BASE_SEPOLIA,
    CHAIN_ID_LOCAL,
)
from app.services.distribution.config import (
    DISTRIBUTION_VERSION,
    compute_85_10_5_split,
    get_platform_recipients,
    validate_distribution_recipients,
)
from app.services.distribution.errors import (
    DistributionConservationError,
    DistributionDuplicateRecipientError,
    DistributionMainnetBlockedError,
)


class TestDistributionAccounting:
    """Rigorous mathematical tests for 85/10/5 integer arithmetic and remainder conservation."""

    def test_zero_amount_rejected(self):
        with pytest.raises(ValueError, match="strictly positive"):
            compute_85_10_5_split(0)

        with pytest.raises(ValueError, match="strictly positive"):
            compute_85_10_5_split(-1000)

    @pytest.mark.parametrize(
        "gross, expected_dev, expected_stk, expected_dao",
        [
            (1, 0, 0, 1),
            (2, 1, 0, 1),
            (3, 2, 0, 1),
            (99, 84, 9, 6),
            (100, 85, 10, 5),
            (101, 85, 10, 6),
            (999_999, 849_999, 99_999, 50_001),
            (1_000_000, 850_000, 100_000, 50_000),
            (1_000_001, 850_000, 100_000, 50_001),
            (777_777, 661_110, 77_777, 38_890),
            (10_000_000_000 * 10**6, 8_500_000_000 * 10**6, 1_000_000_000 * 10**6, 500_000_000 * 10**6),
        ],
    )
    def test_exact_edge_case_splits(self, gross, expected_dev, expected_stk, expected_dao):
        dev, stk, dao = compute_85_10_5_split(gross)
        assert dev == expected_dev, f"Dev split mismatch for {gross}"
        assert stk == expected_stk, f"Staker split mismatch for {gross}"
        assert dao == expected_dao, f"DAO split mismatch for {gross}"
        assert dev + stk + dao == gross, f"Conservation violated for {gross}"

    def test_fuzz_financial_conservation(self):
        """Fuzz tests conservation invariant over 10,000 arbitrary integer gross amounts."""
        # Test small numbers [1, 200]
        for a in range(1, 201):
            dev, stk, dao = compute_85_10_5_split(a)
            assert dev + stk + dao == a
            assert dev <= a
            assert stk <= a
            assert dao <= a

        # Test 10,000 random integers across scales from 1 unit to 10^30 units
        rng = random.Random(42)
        for _ in range(10_000):
            gross = rng.randint(1, 10**30)
            dev, stk, dao = compute_85_10_5_split(gross)
            assert dev + stk + dao == gross
            assert dev == (gross * 85) // 100
            assert stk == (gross * 10) // 100
            assert dao == gross - dev - stk


class TestDistributionRecipientValidation:
    """Verifies fail-closed recipient resolution and rejection of invalid/duplicate addresses."""

    def test_valid_distinct_recipients(self):
        dev = "0x0000000000000000000000000000000000000001"
        stk = "0x0000000000000000000000000000000000000002"
        dao = "0x0000000000000000000000000000000000000003"
        validate_distribution_recipients(dev, stk, dao)  # Should not raise

    def test_reject_zero_addresses(self):
        zero = "0x0000000000000000000000000000000000000000"
        valid1 = "0x0000000000000000000000000000000000000001"
        valid2 = "0x0000000000000000000000000000000000000002"

        with pytest.raises(ValueError, match="Developer recipient"):
            validate_distribution_recipients(zero, valid1, valid2)

        with pytest.raises(ValueError, match="Staker recipient"):
            validate_distribution_recipients(valid1, zero, valid2)

        with pytest.raises(ValueError, match="DAO recipient"):
            validate_distribution_recipients(valid1, valid2, zero)

    def test_reject_duplicate_recipients(self):
        addr1 = "0x0000000000000000000000000000000000000001"
        addr2 = "0x0000000000000000000000000000000000000002"

        # Staker == DAO
        with pytest.raises(DistributionDuplicateRecipientError, match="Staker and DAO"):
            validate_distribution_recipients(addr1, addr2, addr2)

        # Developer == Staker
        with pytest.raises(DistributionDuplicateRecipientError, match="Developer recipient cannot match staker"):
            validate_distribution_recipients(addr1, addr1, addr2)

        # Developer == DAO
        with pytest.raises(DistributionDuplicateRecipientError, match="Developer recipient cannot match DAO"):
            validate_distribution_recipients(addr1, addr2, addr1)

    def test_platform_recipients_resolution(self):
        for chain_id in [CHAIN_ID_LOCAL, CHAIN_ID_BASE_SEPOLIA, CHAIN_ID_BASE_MAINNET]:
            recipients = get_platform_recipients(chain_id)
            assert Web3.is_address(recipients.staker_recipient)
            assert Web3.is_address(recipients.dao_recipient)
            assert recipients.staker_recipient != recipients.dao_recipient
            assert recipients.staker_recipient != "0x0000000000000000000000000000000000000000"
            assert recipients.dao_recipient != "0x0000000000000000000000000000000000000000"


class TestDistributionKeyAndVersioning:
    """Verifies deterministic keys and version binding."""

    def test_distribution_version_is_one(self):
        assert DISTRIBUTION_VERSION == 1

    def test_deterministic_key_structure(self):
        chain_id = 84532
        escrow_id = "0x" + "aa" * 32
        settlement_id = uuid.uuid4()
        expected = f"distribution:{chain_id}:{escrow_id}:{settlement_id}:1"
        actual = f"distribution:{chain_id}:{escrow_id}:{settlement_id}:{DISTRIBUTION_VERSION}"
        assert actual == expected
