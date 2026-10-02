"""Unit tests for DistributionService with mock async session."""

from unittest.mock import AsyncMock, MagicMock, patch
import uuid
import pytest

from app.models.distribution import Distribution, DistributionStatus
from app.models.settlement import Settlement, SettlementAction, SettlementStatus
from app.services.blockchain.config import CHAIN_ID_BASE_MAINNET, CHAIN_ID_BASE_SEPOLIA, CHAIN_ID_LOCAL
from app.services.distribution.errors import (
    DistributionAuthorizationError,
    DistributionMainnetBlockedError,
    DistributionNotFoundError,
    DistributionStateConflictError,
)
from app.services.distribution.service import DistributionService


@pytest.fixture
def mock_session():
    session = AsyncMock()
    return session


@pytest.fixture
def sample_settlement():
    s = MagicMock(spec=Settlement)
    s.id = uuid.uuid4()
    s.chain_id = CHAIN_ID_LOCAL
    s.escrow_contract = "0x5fbdb2315678afecb367f032d93f642f64180aa3"
    s.escrow_id = "0x" + "aa" * 32
    s.action = SettlementAction.RELEASE.value
    s.status = SettlementStatus.AUTHORIZED.value
    s.amount = 1000 * 10**6  # 1000 USDC
    s.beneficiary_address = "0x0000000000000000000000000000000000000099"
    s.token_address = "0x0000000000000000000000000000000000000088"
    s.user_id = uuid.uuid4()
    s.transaction_intent_id = None
    s.settlement_tx_hash = None
    s.block_number = None
    s.block_hash = None
    s.confirmations = 0
    return s


@pytest.mark.asyncio
async def test_create_distribution_settlement_not_found(mock_session):
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None
    mock_session.execute.return_value = mock_result

    with pytest.raises(DistributionNotFoundError):
        await DistributionService.create_distribution(mock_session, uuid.uuid4())


@pytest.mark.asyncio
async def test_create_distribution_mainnet_blocked(mock_session, sample_settlement):
    sample_settlement.chain_id = CHAIN_ID_BASE_MAINNET
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = sample_settlement
    mock_session.execute.return_value = mock_result

    with pytest.raises(DistributionMainnetBlockedError):
        await DistributionService.create_distribution(mock_session, sample_settlement.id)


@pytest.mark.asyncio
async def test_create_distribution_non_release_action_conflict(mock_session, sample_settlement):
    sample_settlement.action = SettlementAction.REFUND.value
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = sample_settlement
    mock_session.execute.return_value = mock_result

    with pytest.raises(DistributionStateConflictError, match="does not trigger revenue distribution"):
        await DistributionService.create_distribution(mock_session, sample_settlement.id)


@pytest.mark.asyncio
async def test_create_distribution_pending_authorization_rejected(mock_session, sample_settlement):
    sample_settlement.status = SettlementStatus.PENDING_AUTHORIZATION.value
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = sample_settlement
    mock_session.execute.return_value = mock_result

    with pytest.raises(DistributionAuthorizationError, match="PENDING_AUTHORIZATION"):
        await DistributionService.create_distribution(mock_session, sample_settlement.id)


@pytest.mark.asyncio
async def test_create_distribution_terminal_status_rejected(mock_session, sample_settlement):
    sample_settlement.status = SettlementStatus.FAILED.value
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = sample_settlement
    mock_session.execute.return_value = mock_result

    with pytest.raises(DistributionStateConflictError, match="terminal/blocked status"):
        await DistributionService.create_distribution(mock_session, sample_settlement.id)


@pytest.mark.asyncio
async def test_create_distribution_idempotent_existing(mock_session, sample_settlement):
    existing_dist = MagicMock(spec=Distribution)
    existing_dist.id = uuid.uuid4()

    mock_settlement_res = MagicMock()
    mock_settlement_res.scalar_one_or_none.return_value = sample_settlement

    mock_existing_res = MagicMock()
    mock_existing_res.scalar_one_or_none.return_value = existing_dist

    mock_session.execute.side_effect = [mock_settlement_res, mock_existing_res]

    dist, was_created = await DistributionService.create_distribution(mock_session, sample_settlement.id)
    assert dist == existing_dist
    assert was_created is False


@pytest.mark.asyncio
async def test_create_distribution_success_exact_amounts(mock_session, sample_settlement):
    mock_settlement_res = MagicMock()
    mock_settlement_res.scalar_one_or_none.return_value = sample_settlement

    mock_existing_res = MagicMock()
    mock_existing_res.scalar_one_or_none.return_value = None

    mock_session.execute.side_effect = [mock_settlement_res, mock_existing_res]

    dist, was_created = await DistributionService.create_distribution(mock_session, sample_settlement.id)
    assert was_created is True
    assert dist.gross_amount == 1000 * 10**6
    assert dist.developer_amount == 850 * 10**6  # 85%
    assert dist.staker_amount == 100 * 10**6     # 10%
    assert dist.dao_amount == 50 * 10**6         # 5% (exact remainder)
    assert dist.developer_amount + dist.staker_amount + dist.dao_amount == dist.gross_amount
