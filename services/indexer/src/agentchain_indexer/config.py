"""Indexer service configuration."""

import os
from pydantic_settings import BaseSettings, SettingsConfigDict
from app.services.blockchain.config import CHAIN_ID_ANVIL, ChainConfig, get_chain_config


class IndexerSettings(BaseSettings):
    chain_id: int = int(os.getenv("INDEXER_CHAIN_ID", str(CHAIN_ID_ANVIL)))
    poll_interval_seconds: float = float(os.getenv("INDEXER_POLL_INTERVAL", "1.0"))
    batch_block_size: int = int(os.getenv("INDEXER_BATCH_SIZE", "20"))
    reconciliation_interval_seconds: float = float(os.getenv("INDEXER_RECONCILIATION_INTERVAL", "30.0"))

    database_url: str = os.getenv("DATABASE_URL", "postgresql+asyncpg://postgres:postgres@localhost:5432/agentchain")
    redis_url: str = os.getenv("REDIS_URL", "redis://localhost:6379/0")

    model_config = SettingsConfigDict(env_prefix="INDEXER_", extra="ignore")

    def get_chain_config(self) -> ChainConfig:
        return get_chain_config(self.chain_id)


settings = IndexerSettings()
