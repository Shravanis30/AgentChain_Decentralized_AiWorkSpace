from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
from typing import Any

from eth_account import Account
from eth_account.messages import encode_defunct
from httpx import ASGITransport, AsyncClient
import pytest
from siwe import SiweMessage

# Add backend directory to sys.path
backend_path = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(backend_path))

from app.core.config import Settings, settings
from app.db.session import async_session_factory, engine
from app.main import app


@pytest.fixture(autouse=True)
async def cleanup_db_connections():
    async def _clean_redis():
        try:
            from app.services.rate_limiter import get_redis_client
            client = get_redis_client()
            for pattern in ("agentchain:*", "rl:*", "worker:*", "test:*"):
                keys = await client.keys(pattern)
                if keys:
                    await client.delete(*keys)
        except Exception:
            pass

    await _clean_redis()
    yield
    await engine.dispose()
    await _clean_redis()




@pytest.fixture
def app_settings() -> Settings:
    return settings


@pytest.fixture
async def db_session():
    async with async_session_factory() as session:
        yield session


@pytest.fixture
async def async_client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest.fixture
def test_wallet():
    """Returns a freshly generated EVM account for testing."""
    return Account.create()


def create_siwe_payload(
    account: Any,
    nonce: str,
    domain: str = "localhost:3000",
    uri: str = "http://localhost:3000",
    chain_id: int = 31337,
    issued_at: datetime | None = None,
    expiration_time: datetime | None = None,
    statement: str = "Sign in with Ethereum to AgentChain",
) -> dict[str, str]:
    """Helper to create a valid signed SIWE payload for testing."""
    now = issued_at or datetime.now(timezone.utc)
    exp = expiration_time or (now + timedelta(minutes=10))

    siwe_msg = SiweMessage(
        domain=domain,
        address=account.address,
        uri=uri,
        version="1",
        chain_id=chain_id,
        nonce=nonce,
        issued_at=now.isoformat(),
        expiration_time=exp.isoformat(),
        statement=statement,
    )
    message_str = siwe_msg.prepare_message()
    signable = encode_defunct(text=message_str)
    signed = account.sign_message(signable)
    signature_hex = signed.signature.hex()

    return {
        "message": message_str,
        "signature": signature_hex,
    }
