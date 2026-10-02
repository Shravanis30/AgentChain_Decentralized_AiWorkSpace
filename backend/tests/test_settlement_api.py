"""REST API integration tests for /api/v1/settlements endpoints."""

import uuid
from eth_account import Account
import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.blockchain import EscrowChainState, EventStatus
from app.models.settlement import Settlement, SettlementAction, SettlementStatus
from app.models.user import User, UserRole
from app.services.blockchain.config import (
    CHAIN_ID_ANVIL,
    CHAIN_ID_BASE_MAINNET,
    get_chain_config,
)
from app.services.settlement.verifier import STATE_LOCKED
from tests.conftest import create_siwe_payload


async def create_auth_user(async_client, db_session, role=UserRole.CLIENT, wallet_address=None):
    async_client.cookies.clear()
    wallet = Account.create() if not wallet_address else None
    addr = wallet.address if wallet else wallet_address

    # Fetch nonce
    nonce_res = await async_client.post(
        "/api/v1/auth/nonce",
        json={"wallet_address": addr, "chain_id": 31337},
    )
    nonce = nonce_res.json()["nonce"]
    if wallet:
        payload = create_siwe_payload(wallet, nonce=nonce, chain_id=31337)
    else:
        temp_wallet = Account.create()
        payload = create_siwe_payload(temp_wallet, nonce=nonce, chain_id=31337)
        payload["address"] = addr

    verify_res = await async_client.post("/api/v1/auth/verify", json=payload)
    data = verify_res.json()
    token = data["token"]
    user_id = data["user"]["id"]

    if role != UserRole.CLIENT:
        user_db = (await db_session.execute(sa.select(User).where(User.id == user_id))).scalar_one()
        user_db.primary_role = role.value
        await db_session.commit()

    return token, user_id, addr


@pytest.mark.asyncio
async def test_settlement_api_lifecycle(async_client, db_session: AsyncSession):
    """Tests end-to-end REST API lifecycle: create, get, list, authorize, cancel."""
    cfg = get_chain_config(CHAIN_ID_ANVIL)
    escrow_id = "0x" + uuid.uuid4().hex + uuid.uuid4().hex

    # 1. Authenticate Client
    client_token, client_user_id, client_addr = await create_auth_user(async_client, db_session, role=UserRole.CLIENT)
    beneficiary_addr = Account.create().address

    # Seed EscrowChainState
    record = EscrowChainState(
        chain_id=CHAIN_ID_ANVIL,
        escrow_id=escrow_id,
        contract_address=cfg.contract_addresses["Escrow"],
        client=client_addr,
        developer=beneficiary_addr,
        amount=10_000_000,
        reference_id="0x" + "01" * 32,
        current_chain_state=STATE_LOCKED,
        creation_tx_hash="0x" + "02" * 32,
        latest_tx_hash="0x" + "02" * 32,
        is_canonical=True,
        confirmation_status=EventStatus.CONFIRMED.value,
    )
    db_session.add(record)
    await db_session.commit()

    # 2. POST /api/v1/settlements (Create)
    create_payload = {
        "chain_id": CHAIN_ID_ANVIL,
        "escrow_id": escrow_id,
        "action": "RELEASE",
        "metadata_json": {"note": "API test release"},
    }
    create_res = await async_client.post(
        "/api/v1/settlements",
        json=create_payload,
        headers={"Authorization": f"Bearer {client_token}"},
    )
    assert create_res.status_code == 201
    settlement_data = create_res.json()
    settlement_id = settlement_data["id"]
    assert settlement_data["status"] == "PENDING_AUTHORIZATION"
    assert settlement_data["escrow_id"] == escrow_id
    assert len(settlement_data["history"]) >= 1

    # 3. POST /api/v1/settlements again (Duplicate Idempotency Check -> 200 OK)
    dup_res = await async_client.post(
        "/api/v1/settlements",
        json=create_payload,
        headers={"Authorization": f"Bearer {client_token}"},
    )
    assert dup_res.status_code == 200
    assert dup_res.json()["id"] == settlement_id

    # 4. GET /api/v1/settlements/{id}
    get_res = await async_client.get(
        f"/api/v1/settlements/{settlement_id}",
        headers={"Authorization": f"Bearer {client_token}"},
    )
    assert get_res.status_code == 200
    assert get_res.json()["id"] == settlement_id

    # 5. GET /api/v1/settlements (List)
    list_res = await async_client.get(
        "/api/v1/settlements",
        headers={"Authorization": f"Bearer {client_token}"},
    )
    assert list_res.status_code == 200
    assert list_res.json()["total"] >= 1

    # 6. POST /api/v1/settlements/{id}/authorize
    auth_res = await async_client.post(
        f"/api/v1/settlements/{settlement_id}/authorize",
        json={"reason": "Approved by client via API"},
        headers={"Authorization": f"Bearer {client_token}"},
    )
    assert auth_res.status_code == 200
    auth_data = auth_res.json()
    assert auth_data["status"] == "AUTHORIZED"
    assert auth_data["transaction_intent_id"] is not None


@pytest.mark.asyncio
async def test_settlement_api_mainnet_blocked(async_client, db_session: AsyncSession):
    """Verifies that Base Mainnet settlement is rejected with 403 Forbidden."""
    client_token, _, _ = await create_auth_user(async_client, db_session, role=UserRole.CLIENT)

    create_payload = {
        "chain_id": CHAIN_ID_BASE_MAINNET,
        "escrow_id": "0x" + "ff" * 32,
        "action": "RELEASE",
    }
    res = await async_client.post(
        "/api/v1/settlements",
        json=create_payload,
        headers={"Authorization": f"Bearer {client_token}"},
    )
    assert res.status_code == 403
    assert "hard-disabled" in res.json()["detail"].lower()


@pytest.mark.asyncio
async def test_settlement_api_unauthorized_beneficiary_release(async_client, db_session: AsyncSession):
    """Verifies that a beneficiary attempting release receives 403 Forbidden."""
    cfg = get_chain_config(CHAIN_ID_ANVIL)
    escrow_id = "0x" + uuid.uuid4().hex + uuid.uuid4().hex

    client_token, client_user_id, client_addr = await create_auth_user(async_client, db_session, role=UserRole.CLIENT)
    dev_token, dev_user_id, dev_addr = await create_auth_user(async_client, db_session, role=UserRole.DEVELOPER)

    record = EscrowChainState(
        chain_id=CHAIN_ID_ANVIL,
        escrow_id=escrow_id,
        contract_address=cfg.contract_addresses["Escrow"],
        client=client_addr,
        developer=dev_addr,
        amount=10_000_000,
        reference_id="0x" + "01" * 32,
        current_chain_state=STATE_LOCKED,
        creation_tx_hash="0x" + "02" * 32,
        latest_tx_hash="0x" + "02" * 32,
        is_canonical=True,
        confirmation_status=EventStatus.CONFIRMED.value,
    )
    db_session.add(record)
    await db_session.commit()

    # Create settlement as client
    create_res = await async_client.post(
        "/api/v1/settlements",
        json={"chain_id": CHAIN_ID_ANVIL, "escrow_id": escrow_id, "action": "RELEASE"},
        headers={"Authorization": f"Bearer {client_token}"},
    )
    settlement_id = create_res.json()["id"]

    # Developer attempts to authorize -> 403
    auth_res = await async_client.post(
        f"/api/v1/settlements/{settlement_id}/authorize",
        json={"reason": "Developer trying self payout"},
        headers={"Authorization": f"Bearer {dev_token}"},
    )
    assert auth_res.status_code == 403
    assert "unilaterally authorize" in auth_res.json()["detail"].lower()
