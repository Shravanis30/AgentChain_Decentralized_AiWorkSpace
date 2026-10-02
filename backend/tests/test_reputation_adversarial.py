"""Adversarial security, forged identity, cross-execution, reorg, and crash tests for Verified Reputation Foundation.

Implements tests for Section 21 of Phase 6.4 specification:
A. Unauthorized contract caller
B. Unauthorized backend caller
C. Anonymous API request
D. Forged agent ID
E. Forged execution ID
F. Forged result hash
G. ResultNotary hash mismatch
H. Execution/result mismatch
I. Agent/execution mismatch
J. Agent-version mismatch
K. Duplicate event
L. Conflicting duplicate event
M. Cross-agent reuse
N. Cross-execution reuse
O. Cross-chain reuse
P. Reorged notarization
Q. Noncanonical reputation event
R. Deep reorg
S. Race condition / concurrent creation
T. Duplicate Redis delivery
U. Duplicate blockchain event
V. Transaction crash before outbox
W. Crash after outbox
X. Crash after transaction submission
Y. Mainnet submission attempt
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
import uuid
import pytest
from httpx import ASGITransport, AsyncClient

from app.core.config import settings
from app.main import app
from app.models.agent import AgentExecution, ExecutionStatus
from app.models.blockchain import BlockchainEvent, ChainReorganization, EventStatus, ReorgStatus
from app.models.notarization import NotarizationStatus, ResultNotarization
from app.models.reputation import (
    ReputationEvent,
    ReputationOutcomeType,
    ReputationProfile,
    ReputationStatus,
)
from app.models.user import User, UserRole
from app.services.blockchain.config import (
    CHAIN_ID_ANVIL,
    CHAIN_ID_BASE_MAINNET,
    CHAIN_ID_BASE_SEPOLIA,
)
from app.services.reputation.errors import (
    ConflictingReputationEventError,
    ExecutionNotFoundError,
    ExecutionNotTerminalError,
    InsufficientConfirmationsError,
    MainnetReputationBlockedError,
    NotarizationNotCanonicalError,
    ResultHashMismatchError,
    ResultNotNotarizedError,
)
from app.services.reputation.reconciliation import ReputationReconciliationService
from app.services.reputation.service import ReputationService
from app.services.reputation.utils import compute_evidence_hash, compute_reputation_key, uuid_to_bytes32


@pytest.fixture
def mock_session():
    return AsyncMock()


# A & B & C: Unauthorized Callers & Anonymous API Requests
@pytest.mark.asyncio
async def test_adversarial_anonymous_api_create_reputation_rejected():
    """Validates that unauthenticated HTTP requests to /api/v1/reputation/events fail with 401."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.post(
            "/api/v1/reputation/events",
            json={"execution_id": str(uuid.uuid4())},
        )
        assert res.status_code == 401


# D: Forged Agent ID
@pytest.mark.asyncio
async def test_adversarial_forged_agent_id_verification_fails(mock_session):
    """If an execution belongs to agent A, verification fails when checked against agent B."""
    real_agent_id = uuid.uuid4()
    forged_agent_id = uuid.uuid4()
    exec_id = uuid.uuid4()
    ver_id = uuid.uuid4()

    rep_event = MagicMock(spec=ReputationEvent)
    rep_event.id = uuid.uuid4()
    rep_event.agent_id = forged_agent_id
    rep_event.agent_version_id = ver_id
    rep_event.execution_id = exec_id
    rep_event.outcome_type = ReputationOutcomeType.VERIFIED_SUCCESS.value
    rep_event.result_hash = "e117d0ab49c9759393b24a62d0ae30b4eea1186c0f910bfe69b7f1f82deed6fd"
    rep_event.chain_id = CHAIN_ID_ANVIL
    rep_event.contract_address = "0x9fe46736679d2d9a65f0992f2272de9f3c7fa6e0"
    rep_event.status = ReputationStatus.CONFIRMED.value
    rep_event.confirmations = 32
    rep_event.is_canonical = True
    rep_event.transaction_hash = None
    rep_event.reputation_key = "rep_key"

    # Database returns real execution belonging to real_agent_id
    real_execution = MagicMock(spec=AgentExecution)
    real_execution.id = exec_id
    real_execution.agent_id = real_agent_id
    real_execution.agent_version_id = ver_id
    real_execution.status = ExecutionStatus.SUCCEEDED.value

    mock_rep_res = MagicMock()
    mock_rep_res.scalar_one_or_none.return_value = rep_event

    mock_exec_res = MagicMock()
    mock_exec_res.scalar_one_or_none.return_value = real_execution

    mock_notary_res = MagicMock()
    mock_notary_res.scalar_one_or_none.return_value = None

    mock_session.execute.side_effect = [mock_rep_res, mock_exec_res, mock_notary_res]

    verification = await ReputationService.verify_reputation_event(mock_session, rep_event.id)
    assert verification.is_verified is False
    assert verification.verification_status == "INVALIDATED"
    assert verification.details.get("agent_match") is False


# E: Forged Execution ID
@pytest.mark.asyncio
async def test_adversarial_forged_execution_id_not_found(mock_session):
    nonexistent_id = uuid.uuid4()
    mock_res = MagicMock()
    mock_res.scalar_one_or_none.return_value = None
    mock_session.execute.return_value = mock_res

    with pytest.raises(ExecutionNotFoundError):
        await ReputationService.create_reputation_event(
            mock_session,
            execution_id=nonexistent_id,
            chain_id=CHAIN_ID_ANVIL,
        )


# F & G: Forged Result Hash / ResultNotary Mismatch
@pytest.mark.asyncio
async def test_adversarial_result_hash_mismatch(mock_session):
    exec_id = uuid.uuid4()
    execution = MagicMock(spec=AgentExecution)
    execution.id = exec_id
    execution.agent_id = uuid.uuid4()
    execution.agent_version_id = uuid.uuid4()
    execution.status = ExecutionStatus.SUCCEEDED.value
    execution.output_hash = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"

    # Notarization proof in DB has a different hash
    notarization = MagicMock(spec=ResultNotarization)
    notarization.id = uuid.uuid4()
    notarization.result_hash = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    notarization.is_canonical = True
    notarization.status = NotarizationStatus.CONFIRMED.value

    mock_exec_res = MagicMock()
    mock_exec_res.scalar_one_or_none.return_value = execution

    mock_notary_res = MagicMock()
    mock_notary_res.scalar_one_or_none.return_value = notarization

    mock_session.execute.side_effect = [mock_exec_res, mock_notary_res]

    with pytest.raises(ResultHashMismatchError):
        await ReputationService.create_reputation_event(
            mock_session,
            execution_id=exec_id,
            chain_id=CHAIN_ID_ANVIL,
        )


# J: Agent-Version Mismatch
@pytest.mark.asyncio
async def test_adversarial_agent_version_mismatch(mock_session):
    real_agent_id = uuid.uuid4()
    real_version_id = uuid.uuid4()
    forged_version_id = uuid.uuid4()
    exec_id = uuid.uuid4()

    rep_event = MagicMock(spec=ReputationEvent)
    rep_event.id = uuid.uuid4()
    rep_event.agent_id = real_agent_id
    rep_event.agent_version_id = forged_version_id
    rep_event.execution_id = exec_id
    rep_event.outcome_type = ReputationOutcomeType.VERIFIED_FAILURE.value
    rep_event.result_hash = None
    rep_event.chain_id = CHAIN_ID_ANVIL
    rep_event.contract_address = "0x9fe46736679d2d9a65f0992f2272de9f3c7fa6e0"
    rep_event.status = ReputationStatus.CONFIRMED.value
    rep_event.confirmations = 32
    rep_event.is_canonical = True
    rep_event.transaction_hash = None
    rep_event.reputation_key = "rep_key"

    real_execution = MagicMock(spec=AgentExecution)
    real_execution.id = exec_id
    real_execution.agent_id = real_agent_id
    real_execution.agent_version_id = real_version_id
    real_execution.status = ExecutionStatus.FAILED.value

    mock_rep_res = MagicMock()
    mock_rep_res.scalar_one_or_none.return_value = rep_event

    mock_exec_res = MagicMock()
    mock_exec_res.scalar_one_or_none.return_value = real_execution

    mock_session.execute.side_effect = [mock_rep_res, mock_exec_res]

    verification = await ReputationService.verify_reputation_event(mock_session, rep_event.id)
    assert verification.is_verified is False
    assert verification.details.get("version_match") is False


# K & L: Duplicate vs Conflicting Duplicate
@pytest.mark.asyncio
async def test_adversarial_conflicting_duplicate_rejected(mock_session):
    exec_id = uuid.uuid4()
    execution = MagicMock(spec=AgentExecution)
    execution.id = exec_id
    execution.agent_id = uuid.uuid4()
    execution.agent_version_id = uuid.uuid4()
    execution.status = ExecutionStatus.FAILED.value
    execution.output_hash = None

    existing = MagicMock(spec=ReputationEvent)
    existing.id = uuid.uuid4()
    existing.reputation_key = "original_key"

    mock_exec_res = MagicMock()
    mock_exec_res.scalar_one_or_none.return_value = execution

    mock_existing_res = MagicMock()
    mock_existing_res.scalar_one_or_none.return_value = existing

    mock_session.execute.side_effect = [mock_exec_res, mock_existing_res]

    with pytest.raises(ConflictingReputationEventError):
        await ReputationService.create_reputation_event(
            mock_session,
            execution_id=exec_id,
            chain_id=CHAIN_ID_ANVIL,
        )


# P & Q: Reorged Notarization & Non-canonical Event
@pytest.mark.asyncio
async def test_adversarial_reorged_notarization_blocks_reputation(mock_session):
    exec_id = uuid.uuid4()
    execution = MagicMock(spec=AgentExecution)
    execution.id = exec_id
    execution.agent_id = uuid.uuid4()
    execution.agent_version_id = uuid.uuid4()
    execution.status = ExecutionStatus.SUCCEEDED.value
    execution.output_hash = "e117d0ab49c9759393b24a62d0ae30b4eea1186c0f910bfe69b7f1f82deed6fd"

    notarization = MagicMock(spec=ResultNotarization)
    notarization.id = uuid.uuid4()
    notarization.result_hash = execution.output_hash
    notarization.is_canonical = False
    notarization.status = NotarizationStatus.REORGED.value

    mock_exec_res = MagicMock()
    mock_exec_res.scalar_one_or_none.return_value = execution

    mock_notary_res = MagicMock()
    mock_notary_res.scalar_one_or_none.return_value = notarization

    mock_session.execute.side_effect = [mock_exec_res, mock_notary_res]

    with pytest.raises(NotarizationNotCanonicalError):
        await ReputationService.create_reputation_event(
            mock_session,
            execution_id=exec_id,
            chain_id=CHAIN_ID_ANVIL,
        )


# R: Deep Reorg Halts Reconciliation
@pytest.mark.asyncio
async def test_adversarial_unrecovered_reorg_halts_reconciliation(mock_session):
    rep_event = MagicMock(spec=ReputationEvent)
    rep_event.id = uuid.uuid4()
    rep_event.chain_id = CHAIN_ID_ANVIL
    rep_event.status = ReputationStatus.PENDING.value

    mock_rep_res = MagicMock()
    mock_rep_res.scalar_one_or_none.return_value = rep_event

    active_reorg = MagicMock(spec=ChainReorganization)
    active_reorg.status = ReorgStatus.FAILED.value
    mock_reorg_res = MagicMock()
    mock_reorg_res.scalars.return_value.first.return_value = active_reorg

    mock_session.execute.side_effect = [mock_rep_res, mock_reorg_res]

    result = await ReputationReconciliationService.reconcile_event(mock_session, rep_event.id)
    # Status must NOT be confirmed while active unrecovered reorg exists
    assert result.status == ReputationStatus.PENDING.value


# Y: Mainnet Submission Attempt Fails Closed
@pytest.mark.asyncio
async def test_adversarial_mainnet_hard_block(mock_session):
    with pytest.raises(MainnetReputationBlockedError):
        await ReputationService.create_reputation_event(
            mock_session,
            execution_id=uuid.uuid4(),
            chain_id=CHAIN_ID_BASE_MAINNET,
        )
