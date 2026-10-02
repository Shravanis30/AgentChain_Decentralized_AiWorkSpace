from app.core.config import Settings


def test_configuration_defaults(app_settings: Settings):
    assert app_settings.APP_NAME == "AgentChain"
    assert app_settings.APP_VERSION == "0.1.0"
    assert "postgresql+asyncpg://" in app_settings.DATABASE_URL
    assert "redis://" in app_settings.REDIS_URL
    assert "http://" in app_settings.QDRANT_URL
    assert "http://" in app_settings.S3_ENDPOINT_URL
    assert app_settings.CHAIN_ID in (31337, 84532)


def test_configuration_validation_custom_env():
    custom = Settings(
        APP_NAME="CustomAgentChain",
        PORT=9000,
        DEBUG=False,
        CHAIN_ID=8453,
    )
    assert custom.APP_NAME == "CustomAgentChain"
    assert custom.PORT == 9000
    assert custom.DEBUG is False
    assert custom.CHAIN_ID == 8453
