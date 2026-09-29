"""Unit and integration tests for the standalone AgentChain Indexer service."""

from unittest.mock import AsyncMock, patch
import pytest

from agentchain_indexer.config import IndexerSettings
from agentchain_indexer.service import IndexerService
from app.services.blockchain.config import CHAIN_ID_ANVIL, CHAIN_ID_BASE_MAINNET


def test_indexer_settings():
    settings = IndexerSettings(chain_id=CHAIN_ID_ANVIL)
    cfg = settings.get_chain_config()
    assert cfg.chain_id == 31337
    assert cfg.network_name == "anvil"

    mainnet_settings = IndexerSettings(chain_id=CHAIN_ID_BASE_MAINNET)
    mainnet_cfg = mainnet_settings.get_chain_config()
    assert mainnet_cfg.chain_id == 8453
    # Mainnet transactions must remain disabled
    assert mainnet_cfg.allow_transactions is False


@pytest.mark.asyncio
async def test_indexer_service_initialization_and_step():
    settings = IndexerSettings(chain_id=CHAIN_ID_ANVIL)
    service = IndexerService(settings)

    # Mock RPC and indexer methods
    service.rpc_client.verify_chain_id = AsyncMock(return_value=31337)
    service.rpc_client.get_block_number = AsyncMock(return_value=15)
    service.event_indexer.index_block_range = AsyncMock(return_value=2)
    service.block_tracker.advance_block_confirmations = AsyncMock(return_value=1)
    service.event_indexer.advance_event_confirmations = AsyncMock(return_value=1)

    # Initialize
    await service.initialize()
    service.rpc_client.verify_chain_id.assert_awaited_once()

    # Step
    with patch.object(service.block_tracker, "get_latest_canonical_block", return_value=None):
        indexed = await service.step()
        assert indexed == 2

    await service.stop()
