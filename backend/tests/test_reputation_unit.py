"""Unit and behavioral tests for Verified Reputation Foundation."""

from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch
import uuid
import pytest

from app.models.agent import AgentExecution, ExecutionStatus
from app.models.notarization import NotarizationStatus, ResultNotarization
from app.models.reputation import (
    ReputationEvent,
    ReputationOutcomeType,
    ReputationProfile,
    ReputationStatus,
)
from app.services.blockchain.config import CHAIN_ID_ANVIL, CHAIN_ID_BASE_MAINNET
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
from app.services.reputation.service import ReputationService
from app.services.reputation.utils import (
    bytes32_to_uuid,
    canonicalize_evidence,
    compute_evidence_hash,
    compute_reputation_key,
    uuid_to_bytes32,
)


@pytest.fixture
def mock_session():
    return AsyncMock()


@pytest.fixture
def sample_execution():
    ex = MagicMock(spec=AgentExecution)
    ex.id = uuid.uuid4()
    ex.agent_id = uuid.uuid4()
    ex.agent_version_id = uuid.uuid4()
    ex.status = ExecutionStatus.SUCCEEDED.value
    ex.output_hash = "e117d0ab49c9759393b24a62d0ae30b4eea1186c0f910bfe69b7f1f82deed6fd"
    ex.created_at = datetime.now(timezone.utc)
    return ex


def test_uuid_bytes32_roundtrip():
    original_id = uuid.uuid4()
    b32 = uuid_to_bytes32(original_id)
    assert b32.startswith("0x")
    assert len(b32) == 66
    restored = bytes32_to_uuid(b32)
    assert restored == original_id


def test_compute_evidence_hash_rfc8785():
    # Key ordering in dict must produce the exact same deterministic SHA-256 hash
    d1 = {"z": 1, "a": "hello", "m": [1, 2, 3]}
    d2 = {"a": "hello", "m": [1, 2, 3], "z": 1}
    h1 = compute_evidence_hash(d1)
    h2 = compute_evidence_hash(d2)
    assert h1 == h2
    assert len(h1) == 64


def test_compute_reputation_key_determinism():
    cid = 31337
    aid = uuid.uuid4()
    eid = uuid.uuid4()
    outcome = "VERIFIED_SUCCESS"
    rhash = "e117d0ab49c9759393b24a62d0ae30b4eea1186c0f910bfe69b7f1f82deed6fd"
    ehash = "ca2101c614f66ac878b48078b2cb743489e56ac13a3831cbc5f522746fa98c43"

    k1 = compute_reputation_key(cid, aid, eid, outcome, rhash, ehash)
    k2 = compute_reputation_key(cid, aid, eid, outcome, rhash, ehash)
    assert k1 == k2

    # Different outcome produces different key
    k3 = compute_reputation_key(cid, aid, eid, "VERIFIED_FAILURE", rhash, ehash)
    assert k1 != k3


@pytest.mark.asyncio
async def test_reputation_blocks_base_mainnet(mock_session):
    with pytest.raises(MainnetReputationBlockedError):
        await ReputationService.create_reputation_event(
            mock_session,
            execution_id=uuid.uuid4(),
            chain_id=CHAIN_ID_BASE_MAINNET,
        )


@pytest.mark.asyncio
async def test_reputation_fails_on_non_terminal_execution(mock_session, sample_execution):
    sample_execution.status = ExecutionStatus.RUNNING.value
    mock_res = MagicMock()
    mock_res.scalar_one_or_none.return_value = sample_execution
    mock_session.execute.return_value = mock_res

    with pytest.raises(ExecutionNotTerminalError):
        await ReputationService.create_reputation_event(
            mock_session,
            execution_id=sample_execution.id,
            chain_id=CHAIN_ID_ANVIL,
        )


@pytest.mark.asyncio
async def test_verified_success_requires_notarization_proof(mock_session, sample_execution):
    # Execution exists, but notarization proof returns None
    mock_exec_res = MagicMock()
    mock_exec_res.scalar_one_or_none.return_value = sample_execution

    mock_notary_res = MagicMock()
    mock_notary_res.scalar_one_or_none.return_value = None

    mock_session.execute.side_effect = [mock_exec_res, mock_notary_res]

    with pytest.raises(ResultNotNotarizedError):
        await ReputationService.create_reputation_event(
            mock_session,
            execution_id=sample_execution.id,
            chain_id=CHAIN_ID_ANVIL,
        )


@pytest.mark.asyncio
async def test_verified_success_rejects_unconfirmed_notarization(mock_session, sample_execution):
    notarization = MagicMock(spec=ResultNotarization)
    notarization.id = uuid.uuid4()
    notarization.result_hash = sample_execution.output_hash
    notarization.is_canonical = True
    notarization.status = NotarizationStatus.SUBMITTED.value  # Not CONFIRMED

    mock_exec_res = MagicMock()
    mock_exec_res.scalar_one_or_none.return_value = sample_execution

    mock_notary_res = MagicMock()
    mock_notary_res.scalar_one_or_none.return_value = notarization

    mock_session.execute.side_effect = [mock_exec_res, mock_notary_res]

    with pytest.raises(InsufficientConfirmationsError):
        await ReputationService.create_reputation_event(
            mock_session,
            execution_id=sample_execution.id,
            chain_id=CHAIN_ID_ANVIL,
        )


@pytest.mark.asyncio
async def test_verified_success_rejects_hash_mismatch(mock_session, sample_execution):
    notarization = MagicMock(spec=ResultNotarization)
    notarization.id = uuid.uuid4()
    notarization.result_hash = "ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff"
    notarization.is_canonical = True
    notarization.status = NotarizationStatus.CONFIRMED.value

    mock_exec_res = MagicMock()
    mock_exec_res.scalar_one_or_none.return_value = sample_execution

    mock_notary_res = MagicMock()
    mock_notary_res.scalar_one_or_none.return_value = notarization

    mock_session.execute.side_effect = [mock_exec_res, mock_notary_res]

    with pytest.raises(ResultHashMismatchError):
        await ReputationService.create_reputation_event(
            mock_session,
            execution_id=sample_execution.id,
            chain_id=CHAIN_ID_ANVIL,
        )


@pytest.mark.asyncio
async def test_verified_success_rejects_reorged_notarization(mock_session, sample_execution):
    notarization = MagicMock(spec=ResultNotarization)
    notarization.id = uuid.uuid4()
    notarization.result_hash = sample_execution.output_hash
    notarization.is_canonical = False
    notarization.status = NotarizationStatus.REORGED.value

    mock_exec_res = MagicMock()
    mock_exec_res.scalar_one_or_none.return_value = sample_execution

    mock_notary_res = MagicMock()
    mock_notary_res.scalar_one_or_none.return_value = notarization

    mock_session.execute.side_effect = [mock_exec_res, mock_notary_res]

    with pytest.raises(NotarizationNotCanonicalError):
        await ReputationService.create_reputation_event(
            mock_session,
            execution_id=sample_execution.id,
            chain_id=CHAIN_ID_ANVIL,
        )


@pytest.mark.asyncio
async def test_verified_failure_does_not_require_notarization(mock_session, sample_execution):
    sample_execution.status = ExecutionStatus.FAILED.value
    sample_execution.output_hash = None

    mock_exec_res = MagicMock()
    mock_exec_res.scalar_one_or_none.return_value = sample_execution

    mock_existing_res = MagicMock()
    mock_existing_res.scalar_one_or_none.return_value = None

    mock_session.execute.side_effect = [mock_exec_res, mock_existing_res]

    with patch("app.services.blockchain.transaction_intent.TransactionIntentService.create_intent") as mock_intent:
        mock_intent_obj = MagicMock()
        mock_intent_obj.id = uuid.uuid4()
        mock_intent.return_value = (mock_intent_obj, True)

        rep_event, was_created = await ReputationService.create_reputation_event(
            mock_session,
            execution_id=sample_execution.id,
            chain_id=CHAIN_ID_ANVIL,
        )

        assert was_created is True
        assert rep_event.outcome_type == ReputationOutcomeType.VERIFIED_FAILURE.value
        assert rep_event.status == ReputationStatus.PENDING.value
        assert rep_event.result_hash is None


@pytest.mark.asyncio
async def test_idempotent_duplicate_reputation_creation(mock_session, sample_execution):
    sample_execution.status = ExecutionStatus.TIMED_OUT.value
    sample_execution.output_hash = None

    mock_exec_res = MagicMock()
    mock_exec_res.scalar_one_or_none.return_value = sample_execution

    # Compute expected rep key
    base_evidence = {
        "agent_id": str(sample_execution.agent_id),
        "agent_version_id": str(sample_execution.agent_version_id),
        "execution_id": str(sample_execution.id),
        "outcome_type": ReputationOutcomeType.VERIFIED_TIMEOUT.value,
        "result_hash": None,
        "status": ExecutionStatus.TIMED_OUT.value,
    }
    ehash = compute_evidence_hash(base_evidence)
    rep_key = compute_reputation_key(
        CHAIN_ID_ANVIL,
        sample_execution.agent_id,
        sample_execution.id,
        ReputationOutcomeType.VERIFIED_TIMEOUT.value,
        None,
        ehash,
    )

    existing = MagicMock(spec=ReputationEvent)
    existing.id = uuid.uuid4()
    existing.reputation_key = rep_key

    mock_existing_res = MagicMock()
    mock_existing_res.scalar_one_or_none.return_value = existing

    mock_session.execute.side_effect = [mock_exec_res, mock_existing_res]

    event, was_created = await ReputationService.create_reputation_event(
        mock_session,
        execution_id=sample_execution.id,
        chain_id=CHAIN_ID_ANVIL,
    )
    assert was_created is False
    assert event.id == existing.id


@pytest.mark.asyncio
async def test_conflicting_reputation_creation_fails(mock_session, sample_execution):
    sample_execution.status = ExecutionStatus.CANCELLED.value

    mock_exec_res = MagicMock()
    mock_exec_res.scalar_one_or_none.return_value = sample_execution

    existing = MagicMock(spec=ReputationEvent)
    existing.id = uuid.uuid4()
    existing.reputation_key = "different_reputation_key_1234567890abcdef"

    mock_existing_res = MagicMock()
    mock_existing_res.scalar_one_or_none.return_value = existing

    mock_session.execute.side_effect = [mock_exec_res, mock_existing_res]

    with pytest.raises(ConflictingReputationEventError):
        await ReputationService.create_reputation_event(
            mock_session,
            execution_id=sample_execution.id,
            chain_id=CHAIN_ID_ANVIL,
        )
