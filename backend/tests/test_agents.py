import copy
import uuid
from eth_account import Account
import pytest
from sqlalchemy import select

from app.agents.research_agent import RESEARCH_AGENT_MANIFEST
from app.models.agent import Agent, AgentStatus, ExecutionStatus
from app.models.audit import AuditEventType, AuditLog
from app.models.user import User, UserRole
from app.services.hashing import compute_canonical_hash
from tests.conftest import create_siwe_payload


async def create_authenticated_user(async_client, db_session, role=UserRole.DEVELOPER):
    """Helper to authenticate a test user and assign specific role."""
    async_client.cookies.clear()
    wallet = Account.create()
    nonce_res = await async_client.post(
        "/api/v1/auth/nonce",
        json={"wallet_address": wallet.address, "chain_id": 31337},
    )
    nonce = nonce_res.json()["nonce"]
    payload = create_siwe_payload(wallet, nonce=nonce, chain_id=31337)
    verify_res = await async_client.post("/api/v1/auth/verify", json=payload)
    token = verify_res.json()["token"]
    user_id = verify_res.json()["user"]["id"]
    async_client.cookies.clear()

    # Upgrade role if needed
    if role != UserRole.CLIENT:
        user_db = (await db_session.execute(select(User).where(User.id == user_id))).scalar_one()
        user_db.primary_role = role.value
        await db_session.commit()

    return token, user_id, wallet.address


@pytest.mark.asyncio
async def test_agent_creation_and_authorization(async_client, db_session):
    # 1. CLIENT role cannot create agent -> 403 Forbidden
    client_token, _, _ = await create_authenticated_user(async_client, db_session, role=UserRole.CLIENT)
    manifest = RESEARCH_AGENT_MANIFEST.model_dump()
    slug = f"research-agent-{uuid.uuid4().hex[:8]}"
    create_payload = {
        "name": "Research Agent",
        "slug": slug,
        "description": "Deterministic text summarization agent",
        "manifest": manifest,
    }

    forbidden_res = await async_client.post(
        "/api/v1/agents",
        json=create_payload,
        headers={"Authorization": f"Bearer {client_token}"},
    )
    assert forbidden_res.status_code == 403

    # 2. DEVELOPER role can create agent -> 201 Created
    dev_token, dev_user_id, _ = await create_authenticated_user(async_client, db_session, role=UserRole.DEVELOPER)
    create_res = await async_client.post(
        "/api/v1/agents",
        json=create_payload,
        headers={"Authorization": f"Bearer {dev_token}"},
    )
    assert create_res.status_code == 201
    agent_data = create_res.json()
    assert agent_data["name"] == "Research Agent"
    assert agent_data["slug"] == slug
    assert agent_data["status"] == "DRAFT"
    assert agent_data["owner_user_id"] == str(dev_user_id)
    assert "text_summarization" in agent_data["capabilities"]
    assert agent_data["current_version"]["version"] == "1.0.0"

    # 3. Duplicate slug rejected -> 409 Conflict
    dup_res = await async_client.post(
        "/api/v1/agents",
        json=create_payload,
        headers={"Authorization": f"Bearer {dev_token}"},
    )
    assert dup_res.status_code == 409


@pytest.mark.asyncio
async def test_manifest_validation(async_client, db_session):
    dev_token, _, _ = await create_authenticated_user(async_client, db_session, role=UserRole.DEVELOPER)

    # Invalid SemVer in manifest
    bad_manifest = RESEARCH_AGENT_MANIFEST.model_dump()
    bad_manifest["agent"]["version"] = "v1-invalid"

    res = await async_client.post(
        "/api/v1/agents",
        json={
            "name": "Invalid Agent",
            "slug": f"invalid-agent-{uuid.uuid4().hex[:8]}",
            "manifest": bad_manifest,
        },
        headers={"Authorization": f"Bearer {dev_token}"},
    )
    assert res.status_code in (400, 422)

    # Invalid JSON Schema (not an object)
    bad_schema_manifest = RESEARCH_AGENT_MANIFEST.model_dump()
    bad_schema_manifest["input_schema"] = {"type": "string"}  # root must be object
    res2 = await async_client.post(
        "/api/v1/agents",
        json={
            "name": "Invalid Schema Agent",
            "slug": f"invalid-schema-{uuid.uuid4().hex[:8]}",
            "manifest": bad_schema_manifest,
        },
        headers={"Authorization": f"Bearer {dev_token}"},
    )
    assert res2.status_code in (400, 422)


@pytest.mark.asyncio
async def test_agent_lifecycle_transitions(async_client, db_session):
    dev_token, dev_user_id, _ = await create_authenticated_user(async_client, db_session, role=UserRole.DEVELOPER)
    other_token, _, _ = await create_authenticated_user(async_client, db_session, role=UserRole.DEVELOPER)

    # 1. Create DRAFT agent
    manifest = RESEARCH_AGENT_MANIFEST.model_dump()
    slug = f"lifecycle-agent-{uuid.uuid4().hex[:8]}"
    create_res = await async_client.post(
        "/api/v1/agents",
        json={
            "name": "Lifecycle Agent",
            "slug": slug,
            "manifest": manifest,
        },
        headers={"Authorization": f"Bearer {dev_token}"},
    )
    agent_id = create_res.json()["id"]
    assert create_res.json()["status"] == "DRAFT"

    # 2. Other developer cannot modify or trigger lifecycle -> 403 Forbidden
    unauth_val = await async_client.post(
        f"/api/v1/agents/{agent_id}/validate",
        headers={"Authorization": f"Bearer {other_token}"},
    )
    assert unauth_val.status_code == 403

    # 3. Owner triggers validation -> 200 OK
    val_res = await async_client.post(
        f"/api/v1/agents/{agent_id}/validate",
        headers={"Authorization": f"Bearer {dev_token}"},
    )
    assert val_res.status_code == 200

    # 4. Owner publishes agent -> PUBLISHED
    pub_res = await async_client.post(
        f"/api/v1/agents/{agent_id}/publish",
        headers={"Authorization": f"Bearer {dev_token}"},
    )
    assert pub_res.status_code == 200
    assert pub_res.json()["status"] == "PUBLISHED"
    assert pub_res.json()["published_at"] is not None

    # 5. Suspend agent -> SUSPENDED
    sus_res = await async_client.post(
        f"/api/v1/agents/{agent_id}/suspend?reason=Maintenance",
        headers={"Authorization": f"Bearer {dev_token}"},
    )
    assert sus_res.status_code == 200
    assert sus_res.json()["status"] == "SUSPENDED"


@pytest.mark.asyncio
async def test_discovery_filtering_and_pagination(async_client, db_session):
    dev_token, _, _ = await create_authenticated_user(async_client, db_session, role=UserRole.DEVELOPER)

    # Create & Publish an agent with unique capability
    manifest = RESEARCH_AGENT_MANIFEST.model_dump()
    manifest["capabilities"] = ["custom_analytics"]
    slug = f"analytics-pro-{uuid.uuid4().hex[:8]}"
    create_res = await async_client.post(
        "/api/v1/agents",
        json={
            "name": "Analytics Pro",
            "slug": slug,
            "description": "High performance analytics",
            "manifest": manifest,
        },
        headers={"Authorization": f"Bearer {dev_token}"},
    )
    agent_id = create_res.json()["id"]
    await async_client.post(
        f"/api/v1/agents/{agent_id}/publish",
        headers={"Authorization": f"Bearer {dev_token}"},
    )

    # 1. Public discovery (unauthenticated allowed)
    public_res = await async_client.get("/api/v1/agents")
    assert public_res.status_code == 200
    data = public_res.json()
    assert "items" in data
    assert "total" in data
    assert all(a["status"] == "PUBLISHED" for a in data["items"])

    # 2. Filter by capability
    cap_res = await async_client.get("/api/v1/agents?capability=custom_analytics")
    assert cap_res.status_code == 200
    assert any(a["slug"] == slug for a in cap_res.json()["items"])

    # 3. Filter by search query
    search_res = await async_client.get("/api/v1/agents?search=Analytics")
    assert search_res.status_code == 200
    assert any(a["slug"] == slug for a in search_res.json()["items"])

    # 4. Pagination
    page_res = await async_client.get("/api/v1/agents?page=1&page_size=1")
    assert page_res.status_code == 200
    assert len(page_res.json()["items"]) <= 1
    assert page_res.json()["page_size"] == 1


@pytest.mark.asyncio
async def test_agent_execution_end_to_end(async_client, db_session):
    dev_token, _, _ = await create_authenticated_user(async_client, db_session, role=UserRole.DEVELOPER)
    client_token, client_user_id, _ = await create_authenticated_user(async_client, db_session, role=UserRole.CLIENT)
    third_party_token, _, _ = await create_authenticated_user(async_client, db_session, role=UserRole.CLIENT)

    # 1. Create and publish reference ResearchAgent
    manifest = RESEARCH_AGENT_MANIFEST.model_dump()
    slug = f"research-agent-{uuid.uuid4().hex[:8]}"
    create_res = await async_client.post(
        "/api/v1/agents",
        json={
            "name": "Research Agent",
            "slug": slug,
            "description": "Deterministic local research agent",
            "manifest": manifest,
        },
        headers={"Authorization": f"Bearer {dev_token}"},
    )
    agent_id = create_res.json()["id"]

    # Try executing draft agent -> rejected (must be PUBLISHED)
    draft_exec_res = await async_client.post(
        f"/api/v1/agents/{agent_id}/execute",
        json={"input": {"text": "AgentChain is decentralized workforce."}},
        headers={"Authorization": f"Bearer {client_token}"},
    )
    assert draft_exec_res.status_code == 400
    assert "PUBLISHED" in draft_exec_res.json()["detail"]

    # Publish agent
    await async_client.post(
        f"/api/v1/agents/{agent_id}/publish",
        headers={"Authorization": f"Bearer {dev_token}"},
    )

    # 2. Test invalid input schema rejection
    invalid_input_res = await async_client.post(
        f"/api/v1/agents/{agent_id}/execute",
        json={"input": {"invalid_field": 123}},  # missing required "text"
        headers={"Authorization": f"Bearer {client_token}"},
    )
    assert invalid_input_res.status_code == 422

    # 3. Successful execution (asynchronous queue submission -> worker processing)
    input_text = "AgentChain is a decentralized AI workforce platform. It coordinates multi-agent workflows with cryptographic escrow settlement."
    exec_res = await async_client.post(
        f"/api/v1/agents/{agent_id}/execute",
        json={"input": {"text": input_text}},
        headers={"Authorization": f"Bearer {client_token}"},
    )
    assert exec_res.status_code == 200
    exec_data = exec_res.json()
    execution_id = exec_data["execution_id"]
    # Phase 4 API returns QUEUED execution record
    assert exec_data["status"] == "QUEUED"
    expected_input_hash = compute_canonical_hash({"text": input_text})
    assert exec_data["input_hash"] == expected_input_hash

    # Worker claims and processes job from queue
    from agentchain_worker.sandbox.local_runner import LocalProcessSandboxRunner
    from agentchain_worker.worker import AgentExecutionWorker

    worker = AgentExecutionWorker(sandbox_runner=LocalProcessSandboxRunner())
    claimed = await worker.queue.claim_execution(worker.worker_id)
    assert len(claimed) >= 1
    target_job = next(j for j in claimed if j.execution_id == execution_id)
    processed = await worker.process_job(target_job)
    assert processed is True

    # 4. Retrieve execution as requester -> 200 OK (Now SUCCEEDED)
    get_res = await async_client.get(
        f"/api/v1/executions/{execution_id}",
        headers={"Authorization": f"Bearer {client_token}"},
    )
    assert get_res.status_code == 200
    details = get_res.json()
    assert details["status"] == "SUCCEEDED"
    assert details["output_data"] is not None
    assert "summary" in details["output_data"]
    assert details["output_data"]["word_count"] > 0
    assert details["output_hash"] == compute_canonical_hash(details["output_data"])

    # 5. Third-party unprivileged user cannot access execution -> 403 Forbidden
    unauth_get = await async_client.get(
        f"/api/v1/executions/{execution_id}",
        headers={"Authorization": f"Bearer {third_party_token}"},
    )
    assert unauth_get.status_code == 403

    # 6. Verify audit logs
    audit_started = (
        await db_session.execute(
            select(AuditLog).where(
                AuditLog.user_id == client_user_id,
                AuditLog.event_type == AuditEventType.AGENT_EXECUTION_STARTED.value,
            )
        )
    ).scalars().first()
    assert audit_started is not None

    audit_succeeded = (
        await db_session.execute(
            select(AuditLog).where(
                AuditLog.user_id == client_user_id,
                AuditLog.event_type == AuditEventType.AGENT_EXECUTION_SUCCEEDED.value,
            )
        )
    ).scalars().first()
    assert audit_succeeded is not None
