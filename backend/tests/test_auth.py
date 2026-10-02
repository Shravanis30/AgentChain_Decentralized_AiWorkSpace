from datetime import datetime, timedelta, timezone
from eth_account import Account
import pytest
from sqlalchemy import select

from app.models.audit import AuditEventType, AuditLog
from app.models.session import AuthNonce
from app.models.user import User, UserRole, Wallet
from tests.conftest import create_siwe_payload


@pytest.mark.asyncio
async def test_nonce_generation(async_client, test_wallet):
    response = await async_client.post(
        "/api/v1/auth/nonce",
        json={"wallet_address": test_wallet.address, "chain_id": 31337},
    )
    assert response.status_code == 200
    data = response.json()
    assert "nonce" in data
    assert len(data["nonce"]) == 32
    assert data["wallet_address"].lower() == test_wallet.address.lower()
    assert "expires_at" in data


@pytest.mark.asyncio
async def test_nonce_invalid_address(async_client):
    response = await async_client.post(
        "/api/v1/auth/nonce",
        json={"wallet_address": "invalid-address-format", "chain_id": 31337},
    )
    assert response.status_code in (400, 422)


@pytest.mark.asyncio
async def test_valid_siwe_authentication(async_client, test_wallet, db_session):
    # 1. Request nonce
    nonce_res = await async_client.post(
        "/api/v1/auth/nonce",
        json={"wallet_address": test_wallet.address, "chain_id": 31337},
    )
    assert nonce_res.status_code == 200
    nonce = nonce_res.json()["nonce"]

    # 2. Sign SIWE message
    payload = create_siwe_payload(test_wallet, nonce=nonce, chain_id=31337)

    # 3. Verify
    verify_res = await async_client.post("/api/v1/auth/verify", json=payload)
    assert verify_res.status_code == 200
    data = verify_res.json()
    assert data["authenticated"] is True
    assert data["wallet"]["address"].lower() == test_wallet.address.lower()
    assert data["wallet"]["chain_id"] == 31337
    assert data["user"]["primary_role"] == "CLIENT"
    assert "token" in data

    # Check session cookie set
    cookies = verify_res.cookies
    assert "agentchain_session" in cookies

    # Check persistence in PostgreSQL
    wallet_db = (
        await db_session.execute(
            select(Wallet).where(Wallet.address == test_wallet.address)
        )
    ).scalar_one_or_none()
    assert wallet_db is not None
    assert wallet_db.chain_id == 31337

    # Check audit log
    audit_db = (
        await db_session.execute(
            select(AuditLog).where(
                AuditLog.wallet_address == test_wallet.address,
                AuditLog.event_type == AuditEventType.AUTH_SUCCESS.value,
            )
        )
    ).scalar_one_or_none()
    assert audit_db is not None


@pytest.mark.asyncio
async def test_nonce_single_use_replay_prevention(async_client, test_wallet):
    # 1. Get nonce
    nonce_res = await async_client.post(
        "/api/v1/auth/nonce",
        json={"wallet_address": test_wallet.address, "chain_id": 31337},
    )
    nonce = nonce_res.json()["nonce"]

    # 2. Sign SIWE message
    payload = create_siwe_payload(test_wallet, nonce=nonce, chain_id=31337)

    # 3. First verify -> Success
    res1 = await async_client.post("/api/v1/auth/verify", json=payload)
    assert res1.status_code == 200

    # 4. Replay attempt -> Must be rejected
    res2 = await async_client.post("/api/v1/auth/verify", json=payload)
    assert res2.status_code in (400, 401)
    assert "already been used" in res2.json()["detail"].lower() or "nonce" in res2.json()["detail"].lower()


@pytest.mark.asyncio
async def test_nonce_expiration(async_client, test_wallet, db_session):
    import secrets
    expired_nonce = secrets.token_hex(16)
    expired_time = datetime.now(timezone.utc) - timedelta(minutes=10)
    nonce_obj = AuthNonce(
        wallet_address=test_wallet.address,
        nonce=expired_nonce,
        chain_id=31337,
        expires_at=expired_time,
    )
    db_session.add(nonce_obj)
    await db_session.commit()

    payload = create_siwe_payload(test_wallet, nonce=expired_nonce, chain_id=31337)
    res = await async_client.post("/api/v1/auth/verify", json=payload)
    assert res.status_code == 400
    assert "expired" in res.json()["detail"].lower()



@pytest.mark.asyncio
async def test_invalid_signature_rejected(async_client, test_wallet):
    nonce_res = await async_client.post(
        "/api/v1/auth/nonce",
        json={"wallet_address": test_wallet.address, "chain_id": 31337},
    )
    nonce = nonce_res.json()["nonce"]

    payload = create_siwe_payload(test_wallet, nonce=nonce, chain_id=31337)
    # Corrupt signature
    corrupt_sig = "0x" + "a" * 130
    payload["signature"] = corrupt_sig

    res = await async_client.post("/api/v1/auth/verify", json=payload)
    assert res.status_code == 401


@pytest.mark.asyncio
async def test_signature_wrong_wallet_rejected(async_client, test_wallet):
    other_wallet = Account.create()
    nonce_res = await async_client.post(
        "/api/v1/auth/nonce",
        json={"wallet_address": test_wallet.address, "chain_id": 31337},
    )
    nonce = nonce_res.json()["nonce"]

    # SIWE message says test_wallet.address, but other_wallet signs it
    payload = create_siwe_payload(test_wallet, nonce=nonce, chain_id=31337)
    from eth_account.messages import encode_defunct
    wrong_sig = other_wallet.sign_message(encode_defunct(text=payload["message"])).signature.hex()
    payload["signature"] = wrong_sig

    res = await async_client.post("/api/v1/auth/verify", json=payload)
    assert res.status_code == 401


@pytest.mark.asyncio
async def test_unsupported_chain_rejected(async_client, test_wallet):
    nonce_res = await async_client.post(
        "/api/v1/auth/nonce",
        json={"wallet_address": test_wallet.address, "chain_id": 999999},
    )
    nonce = nonce_res.json()["nonce"]

    payload = create_siwe_payload(test_wallet, nonce=nonce, chain_id=999999)
    res = await async_client.post("/api/v1/auth/verify", json=payload)
    assert res.status_code == 400
    assert "chain" in res.json()["detail"].lower()


@pytest.mark.asyncio
async def test_unauthorized_domain_rejected(async_client, test_wallet):
    nonce_res = await async_client.post(
        "/api/v1/auth/nonce",
        json={"wallet_address": test_wallet.address, "chain_id": 31337},
    )
    nonce = nonce_res.json()["nonce"]

    payload = create_siwe_payload(test_wallet, nonce=nonce, domain="evil-phishing-site.xyz", chain_id=31337)
    res = await async_client.post("/api/v1/auth/verify", json=payload)
    assert res.status_code == 400
    assert "domain" in res.json()["detail"].lower()


@pytest.mark.asyncio
async def test_session_lifecycle_and_logout(async_client, test_wallet):
    # 1. Login
    nonce_res = await async_client.post(
        "/api/v1/auth/nonce",
        json={"wallet_address": test_wallet.address, "chain_id": 31337},
    )
    nonce = nonce_res.json()["nonce"]
    payload = create_siwe_payload(test_wallet, nonce=nonce)
    verify_res = await async_client.post("/api/v1/auth/verify", json=payload)
    assert verify_res.status_code == 200
    token = verify_res.json()["token"]

    # 2. Access /auth/me with Bearer token
    me_res = await async_client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert me_res.status_code == 200
    assert me_res.json()["primary_wallet"]["address"].lower() == test_wallet.address.lower()

    # 3. Access /users/me
    profile_res = await async_client.get(
        "/api/v1/users/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert profile_res.status_code == 200
    assert len(profile_res.json()["wallets"]) >= 1

    # 4. Logout
    logout_res = await async_client.post(
        "/api/v1/auth/logout",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert logout_res.status_code == 200

    # 5. Token is now revoked
    me_after_logout = await async_client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert me_after_logout.status_code == 401


@pytest.mark.asyncio
async def test_rbac_authorization(async_client, test_wallet, db_session):
    # 1. Login with CLIENT role
    nonce_res = await async_client.post(
        "/api/v1/auth/nonce",
        json={"wallet_address": test_wallet.address, "chain_id": 31337},
    )
    nonce = nonce_res.json()["nonce"]
    payload = create_siwe_payload(test_wallet, nonce=nonce)
    verify_res = await async_client.post("/api/v1/auth/verify", json=payload)
    token = verify_res.json()["token"]
    user_id = verify_res.json()["user"]["id"]

    # 2. Try to access admin-only endpoint -> 403 Forbidden
    admin_res = await async_client.get(
        "/api/v1/users/admin/audit-logs",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert admin_res.status_code == 403

    # 3. Upgrade user to ADMIN role in database
    user_db = (await db_session.execute(select(User).where(User.id == user_id))).scalar_one()
    user_db.primary_role = UserRole.ADMIN.value
    await db_session.commit()

    # 4. Now admin endpoint succeeds
    admin_res_ok = await async_client.get(
        "/api/v1/users/admin/audit-logs",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert admin_res_ok.status_code == 200
    assert "Admin authorization granted" in admin_res_ok.json()["message"]


@pytest.mark.asyncio
async def test_wallet_linking_flow(async_client, test_wallet, db_session):
    # 1. Login with wallet 1
    nonce1 = (await async_client.post("/api/v1/auth/nonce", json={"wallet_address": test_wallet.address})).json()["nonce"]
    payload1 = create_siwe_payload(test_wallet, nonce=nonce1)
    login_res = await async_client.post("/api/v1/auth/verify", json=payload1)
    token = login_res.json()["token"]

    # 2. Request nonce for wallet 2
    wallet2 = Account.create()
    nonce2 = (await async_client.post("/api/v1/auth/nonce", json={"wallet_address": wallet2.address})).json()["nonce"]

    # 3. Sign proof with wallet 2
    payload2 = create_siwe_payload(wallet2, nonce=nonce2, statement="Link secondary wallet to AgentChain")

    # 4. Link wallet 2 to user
    link_res = await async_client.post(
        "/api/v1/users/me/wallets",
        json=payload2,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert link_res.status_code == 201
    assert link_res.json()["address"].lower() == wallet2.address.lower()

    # 5. Verify /users/me/wallets shows both wallets
    wallets_res = await async_client.get(
        "/api/v1/users/me/wallets",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert wallets_res.status_code == 200
    addrs = [w["address"].lower() for w in wallets_res.json()]
    assert test_wallet.address.lower() in addrs
    assert wallet2.address.lower() in addrs


@pytest.mark.asyncio
async def test_rate_limiting_enforcement(async_client, test_wallet):
    # Simulate high frequency requests from a specific IP
    custom_headers = {"X-Forwarded-For": "198.51.100.42"}
    responses = []
    # RATE_LIMIT_NONCE_LIMIT is 30, so 32 requests will trigger rate limiting
    for _ in range(32):
        res = await async_client.post(
            "/api/v1/auth/nonce",
            json={"wallet_address": test_wallet.address, "chain_id": 31337},
            headers=custom_headers,
        )
        responses.append(res)

    status_codes = [r.status_code for r in responses]
    assert 200 in status_codes
    assert 429 in status_codes
    rate_limited_res = next(r for r in responses if r.status_code == 429)
    assert "Retry-After" in rate_limited_res.headers

