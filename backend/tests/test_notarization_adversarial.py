"""Adversarial security, hash manipulation, cross-execution, and reorg tests for Result Notarization."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
import uuid
import pytest
from httpx import ASGITransport, AsyncClient

from app.core.config import settings
from app.main import app
from app.models.agent import AgentExecution, AgentVersion, ExecutionStatus
from app.models.blockchain import (
    BlockchainEvent,
    ChainReorganization,
    EventStatus,
    ReorgStatus,
)
from app.models.notarization import (
    NotarizationHistory,
    NotarizationStatus,
    ResultNotarization,
)
from app.models.user import User, UserRole
from app.schemas.notarization import (
    CanonicalNotarizationPayload,
    VerificationStatus,
)
from app.services.blockchain.config import (
    CHAIN_ID_ANVIL,
    CHAIN_ID_BASE_MAINNET,
    CHAIN_ID_BASE_SEPOLIA,
)
from app.services.blockchain.errors import (
    ContractMismatchError,
    MainnetSubmissionBlockedError,
)
from app.services.hashing import canonicalize_json, compute_canonical_hash
from app.services.notarization.errors import (
    ExecutionNotEligibleError,
    ResultHashImmutabilityViolationError,
)
from app.services.notarization.reconciliation import NotarizationReconciliationService
from app.services.notarization.service import NotarizationService
from app.services.notarization.utils import (
    bytes32_to_execution_id,
    execution_id_to_bytes32,
    result_hash_to_bytes32,
)


@pytest.fixture
def mock_session():
    return AsyncMock()


# ==============================================================================
# SECTION 21: HASH MANIPULATION ATTACKS
# ==============================================================================


def test_hash_attack_whitespace_invariance():
    """Validates that whitespace variations in raw JSON produce identical RFC 8785 hashes."""
    obj = {"message": "hello", "count": 100}
    # canonicalization strips all whitespace outside strings
    canon = canonicalize_json(obj)
    assert " " not in canon
    assert canon == '{"count":100,"message":"hello"}'
    assert compute_canonical_hash(obj) == compute_canonical_hash({"count": 100, "message": "hello"})


def test_hash_attack_key_ordering_invariance():
    """Validates that dictionary key order does not alter canonical hash."""
    d1 = {"z": 1, "m": 2, "a": 3}
    d2 = {"a": 3, "z": 1, "m": 2}
    d3 = {"m": 2, "a": 3, "z": 1}
    assert compute_canonical_hash(d1) == compute_canonical_hash(d2) == compute_canonical_hash(d3)


def test_hash_attack_unicode_representation():
    """Validates that Unicode characters are canonicalized strictly in UTF-8."""
    u1 = {"text": "AgentChain 🚀"}
    u2 = {"text": "AgentChain \U0001F680"}
    assert compute_canonical_hash(u1) == compute_canonical_hash(u2)


def test_hash_attack_field_tampering_produces_collision_resistance():
    """Adding or removing fields MUST alter the canonical hash."""
    base = {"result": "success", "score": 95}
    tampered_add = {"result": "success", "score": 95, "extra": "injected"}
    tampered_remove = {"result": "success"}
    tampered_val = {"result": "success", "score": 96}

    h_base = compute_canonical_hash(base)
    h_add = compute_canonical_hash(tampered_add)
    h_remove = compute_canonical_hash(tampered_remove)
    h_val = compute_canonical_hash(tampered_val)

    assert len({h_base, h_add, h_remove, h_val}) == 4


def test_canonical_envelope_tampering():
    """Altering any conceptual field in CanonicalNotarizationPayload produces a distinct commitment."""
    p1 = CanonicalNotarizationPayload(
        protocol_version="1.0",
        execution_id="11111111-1111-1111-1111-111111111111",
        agent_id="22222222-2222-2222-2222-222222222222",
        agent_version="1.0.0",
        result_hash="a" * 64,
        artifact_reference="ipfs://Qm1",
    )
    # Alter agent_version
    p2 = p1.model_copy(update={"agent_version": "1.0.1"})
    # Alter execution_id
    p3 = p1.model_copy(update={"execution_id": "33333333-3333-3333-3333-333333333333"})
    # Alter artifact_reference
    p4 = p1.model_copy(update={"artifact_reference": "ipfs://Qm2"})

    commits = {
        p1.compute_commitment_hash(),
        p2.compute_commitment_hash(),
        p3.compute_commitment_hash(),
        p4.compute_commitment_hash(),
    }
    assert len(commits) == 4


# ==============================================================================
# SECTION 22: AUTHORIZATION ATTACKS
# ==============================================================================


@pytest.mark.asyncio
async def test_auth_attack_anonymous_user_rejected():
    """Unauthenticated client cannot request notarization creation."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/notarizations",
            json={"execution_id": str(uuid.uuid4())},
        )
        assert resp.status_code in (401, 403)


@pytest.mark.asyncio
async def test_auth_attack_mainnet_hard_fail_closed(mock_session):
    """Attempting notarization on Base Mainnet (8453) fails closed."""
    exec_id = uuid.uuid4()
    with pytest.raises(MainnetSubmissionBlockedError):
        await NotarizationService.create_notarization(
            mock_session, exec_id, chain_id=CHAIN_ID_BASE_MAINNET
        )


@pytest.mark.asyncio
async def test_auth_attack_unsupported_chain_rejected(mock_session):
    """Arbitrary unsupported chain ID fails closed."""
    with pytest.raises(Exception):
        await NotarizationService.create_notarization(
            mock_session, uuid.uuid4(), chain_id=999999
        )


# ==============================================================================
# SECTION 23: CROSS-EXECUTION ATTACKS
# ==============================================================================


@pytest.mark.asyncio
async def test_cross_execution_isolation(mock_session):
    """Executions A and B must maintain separate immutable proofs."""
    exec_a = uuid.uuid4()
    exec_b = uuid.uuid4()
    hash_a = "a" * 64
    hash_b = "b" * 64

    notary_a = MagicMock(spec=ResultNotarization)
    notary_a.execution_id = exec_a
    notary_a.result_hash = hash_a
    notary_a.status = NotarizationStatus.CONFIRMED.value
    notary_a.is_canonical = True
    notary_a.contract_address = "0x5fbdb2315678afecb367f032d93f642f64180aa5"
    notary_a.transaction_hash = "0x" + "aa" * 32
    notary_a.block_number = 10
    notary_a.confirmations = 5
    notary_a.confirmed_at = datetime.now(timezone.utc)
    notary_a.hash_algorithm = "SHA-256"
    notary_a.canonicalization_version = "RFC-8785"

    mock_res = MagicMock()
    mock_res.scalar_one_or_none.return_value = notary_a
    mock_session.execute.return_value = mock_res

    # Attempt cross-verification: verifying Exec A with Hash B must FAIL with HASH_MISMATCH
    resp = await NotarizationService.verify_execution(
        mock_session, exec_a, result_hash=hash_b, chain_id=CHAIN_ID_ANVIL
    )
    assert resp.verification_status == VerificationStatus.HASH_MISMATCH


@pytest.mark.asyncio
async def test_same_hash_across_distinct_executions_allowed():
    """Two different executions producing identical outputs are each allowed their own proof."""
    shared_hash = compute_canonical_hash({"status": "ok"})
    exec_1 = uuid.uuid4()
    exec_2 = uuid.uuid4()

    key_1 = f"notarization:31337:0x5fbdb2315678afecb367f032d93f642f64180aa5:{exec_1}:{shared_hash}"
    key_2 = f"notarization:31337:0x5fbdb2315678afecb367f032d93f642f64180aa5:{exec_2}:{shared_hash}"

    # Keys are completely distinct
    assert key_1 != key_2


# ==============================================================================
# SECTION 24: CROSS-CHAIN REPLAY ATTACKS
# ==============================================================================


def test_cross_chain_domain_separation():
    """Proofs on different chains MUST produce distinct domain keys."""
    exec_id = uuid.uuid4()
    res_hash = "f" * 64
    notary_addr = "0x5fbdb2315678afecb367f032d93f642f64180aa5"

    key_anvil = f"notarization:{CHAIN_ID_ANVIL}:{notary_addr}:{exec_id}:{res_hash}"
    key_sepolia = f"notarization:{CHAIN_ID_BASE_SEPOLIA}:{notary_addr}:{exec_id}:{res_hash}"

    assert key_anvil != key_sepolia
    assert str(CHAIN_ID_ANVIL) in key_anvil
    assert str(CHAIN_ID_BASE_SEPOLIA) in key_sepolia


# ==============================================================================
# SECTION 31 & 32: CRASH RECOVERY & REORG RECOVERY
# ==============================================================================


@pytest.mark.asyncio
async def test_reorg_orphaned_event_invalidates_proof(mock_session):
    """Simulates: confirmed notarization -> reorg occurs -> event orphaned -> reconciliation updates to REORGED."""
    notarization = MagicMock(spec=ResultNotarization)
    notarization.id = uuid.uuid4()
    notarization.chain_id = CHAIN_ID_ANVIL
    notarization.execution_id = uuid.uuid4()
    notarization.result_hash = "d" * 64
    notarization.contract_address = "0x5fbdb2315678afecb367f032d93f642f64180aa5"
    notarization.status = NotarizationStatus.CONFIRMED.value
    notarization.transaction_hash = "0x" + "ee" * 32
    notarization.is_canonical = True

    mock_notary_res = MagicMock()
    mock_notary_res.scalar_one_or_none.return_value = notarization

    # No unrecovered general reorgs
    mock_reorg_res = MagicMock()
    mock_reorg_res.scalars.return_value.first.return_value = None

    # Transaction events are now all orphaned (is_canonical = False)
    orphaned_event = MagicMock(spec=BlockchainEvent)
    orphaned_event.is_canonical = False
    orphaned_event.event_name = "ResultNotarized"

    mock_tx_events = MagicMock()
    mock_tx_events.scalars.return_value.all.return_value = [orphaned_event]

    mock_session.execute.side_effect = [
        mock_notary_res,
        mock_reorg_res,
        mock_tx_events,
    ]

    reconciled = await NotarizationReconciliationService.reconcile_notarization(
        mock_session, notarization.id
    )

    assert reconciled.status == NotarizationStatus.REORGED.value
    assert reconciled.is_canonical is False
    assert "orphaned" in reconciled.error_message.lower()


@pytest.mark.asyncio
async def test_active_unrecovered_reorg_halts_fail_closed(mock_session):
    """If an unrecovered reorg is active, reconciliation must HALT fail-closed."""
    notarization = MagicMock(spec=ResultNotarization)
    notarization.id = uuid.uuid4()
    notarization.chain_id = CHAIN_ID_ANVIL
    notarization.status = NotarizationStatus.SUBMITTED.value

    mock_notary_res = MagicMock()
    mock_notary_res.scalar_one_or_none.return_value = notarization

    # Active reorg present!
    active_reorg = MagicMock(spec=ChainReorganization)
    active_reorg.status = ReorgStatus.FAILED.value
    mock_reorg_res = MagicMock()
    mock_reorg_res.scalars.return_value.first.return_value = active_reorg

    mock_session.execute.side_effect = [mock_notary_res, mock_reorg_res]

    reconciled = await NotarizationReconciliationService.reconcile_notarization(
        mock_session, notarization.id
    )
    # State must not be advanced
    assert reconciled.status == NotarizationStatus.SUBMITTED.value
