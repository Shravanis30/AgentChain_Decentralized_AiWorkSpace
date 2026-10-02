"""Unit and behavioral tests for Cryptographic Result Notarization."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
import uuid
import pytest

from app.models.agent import Agent, AgentExecution, AgentVersion, ExecutionStatus
from app.models.blockchain import (
    BlockchainEvent,
    BlockchainTransaction,
    ChainReorganization,
    EventStatus,
    ReorgStatus,
)
from app.models.notarization import (
    NotarizationHistory,
    NotarizationStatus,
    ResultNotarization,
)
from app.schemas.notarization import (
    CanonicalNotarizationPayload,
    VerificationStatus,
)
from app.services.blockchain.config import (
    CHAIN_ID_ANVIL,
    CHAIN_ID_BASE_MAINNET,
    CHAIN_ID_BASE_SEPOLIA,
)
from app.services.blockchain.errors import MainnetSubmissionBlockedError
from app.services.hashing import canonicalize_json, compute_canonical_hash
from app.services.notarization.errors import (
    ExecutionNotEligibleError,
    ExecutionNotFoundError,
    ResultHashImmutabilityViolationError,
)
from app.services.notarization.reconciliation import NotarizationReconciliationService
from app.services.notarization.service import NotarizationService
from app.services.notarization.utils import (
    bytes32_to_execution_id,
    bytes32_to_result_hash,
    execution_id_to_bytes32,
    result_hash_to_bytes32,
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
    ex.cancellation_requested = False
    ex.cancelled_at = None
    ex.output_data = {"score": 42, "summary": "completed"}
    ex.output_hash = compute_canonical_hash(ex.output_data)
    ex.completed_at = datetime.now(timezone.utc)
    return ex


@pytest.mark.asyncio
async def test_create_notarization_success(mock_session, sample_execution):
    # Execution lookup returns sample_execution, existing notarization lookup returns None
    agent_ver = MagicMock(spec=AgentVersion)
    agent_ver.version = "1.0.0"

    mock_exec_res = MagicMock()
    mock_exec_res.scalar_one_or_none.return_value = sample_execution

    mock_existing_res = MagicMock()
    mock_existing_res.scalar_one_or_none.return_value = None

    mock_ver_res = MagicMock()
    mock_ver_res.scalar_one_or_none.return_value = agent_ver

    mock_session.execute.side_effect = [mock_exec_res, mock_existing_res, mock_ver_res]

    with patch("app.services.blockchain.transaction_intent.TransactionIntentService.create_intent") as mock_intent:
        mock_intent_obj = MagicMock()
        mock_intent_obj.id = uuid.uuid4()
        mock_intent.return_value = (mock_intent_obj, True)

        notarization, was_created = await NotarizationService.create_notarization(
            mock_session,
            sample_execution.id,
            chain_id=CHAIN_ID_ANVIL,
            artifact_reference="ipfs://QmXoypizjW3WknFiJnKLwHCnL72vedxjQkDDP1mXWo6uco",
        )

        assert was_created is True
        assert notarization.execution_id == sample_execution.id
        assert notarization.result_hash == sample_execution.output_hash
        assert notarization.chain_id == CHAIN_ID_ANVIL
        assert notarization.status == NotarizationStatus.PENDING.value
        assert notarization.artifact_reference == "ipfs://QmXoypizjW3WknFiJnKLwHCnL72vedxjQkDDP1mXWo6uco"


@pytest.mark.asyncio
async def test_create_notarization_execution_not_found(mock_session):
    mock_res = MagicMock()
    mock_res.scalar_one_or_none.return_value = None
    mock_session.execute.return_value = mock_res

    with pytest.raises(ExecutionNotFoundError):
        await NotarizationService.create_notarization(mock_session, uuid.uuid4())


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_status", [
    ExecutionStatus.QUEUED.value,
    ExecutionStatus.RUNNING.value,
    ExecutionStatus.FAILED.value,
    ExecutionStatus.TIMED_OUT.value,
])
async def test_create_notarization_rejects_non_succeeded(mock_session, sample_execution, bad_status):
    sample_execution.status = bad_status
    mock_res = MagicMock()
    mock_res.scalar_one_or_none.return_value = sample_execution
    mock_session.execute.return_value = mock_res

    with pytest.raises(ExecutionNotEligibleError):
        await NotarizationService.create_notarization(mock_session, sample_execution.id)


@pytest.mark.asyncio
async def test_create_notarization_rejects_cancelled(mock_session, sample_execution):
    sample_execution.cancellation_requested = True
    mock_res = MagicMock()
    mock_res.scalar_one_or_none.return_value = sample_execution
    mock_session.execute.return_value = mock_res

    with pytest.raises(ExecutionNotEligibleError):
        await NotarizationService.create_notarization(mock_session, sample_execution.id)


@pytest.mark.asyncio
async def test_create_notarization_rejects_missing_hash(mock_session, sample_execution):
    sample_execution.output_hash = None
    mock_res = MagicMock()
    mock_res.scalar_one_or_none.return_value = sample_execution
    mock_session.execute.return_value = mock_res

    with pytest.raises(ExecutionNotEligibleError):
        await NotarizationService.create_notarization(mock_session, sample_execution.id)


@pytest.mark.asyncio
async def test_create_notarization_mainnet_hard_blocked(mock_session, sample_execution):
    mock_res = MagicMock()
    mock_res.scalar_one_or_none.return_value = sample_execution
    mock_session.execute.return_value = mock_res

    with pytest.raises(MainnetSubmissionBlockedError):
        await NotarizationService.create_notarization(
            mock_session, sample_execution.id, chain_id=CHAIN_ID_BASE_MAINNET
        )


@pytest.mark.asyncio
async def test_create_notarization_idempotent_duplicate(mock_session, sample_execution):
    existing_notarization = MagicMock(spec=ResultNotarization)
    existing_notarization.id = uuid.uuid4()
    existing_notarization.result_hash = sample_execution.output_hash
    existing_notarization.execution_id = sample_execution.id

    mock_exec_res = MagicMock()
    mock_exec_res.scalar_one_or_none.return_value = sample_execution

    mock_exist_res = MagicMock()
    mock_exist_res.scalar_one_or_none.return_value = existing_notarization

    mock_session.execute.side_effect = [mock_exec_res, mock_exist_res]

    res, created = await NotarizationService.create_notarization(
        mock_session, sample_execution.id, chain_id=CHAIN_ID_ANVIL
    )
    assert created is False
    assert res == existing_notarization


@pytest.mark.asyncio
async def test_create_notarization_hash_immutability_violation(mock_session, sample_execution):
    # Existing notarization has a different result hash
    existing_notarization = MagicMock(spec=ResultNotarization)
    existing_notarization.id = uuid.uuid4()
    existing_notarization.result_hash = "0" * 64
    existing_notarization.execution_id = sample_execution.id

    mock_exec_res = MagicMock()
    mock_exec_res.scalar_one_or_none.return_value = sample_execution

    mock_exist_res = MagicMock()
    mock_exist_res.scalar_one_or_none.return_value = existing_notarization

    mock_session.execute.side_effect = [mock_exec_res, mock_exist_res]

    with pytest.raises(ResultHashImmutabilityViolationError):
        await NotarizationService.create_notarization(
            mock_session, sample_execution.id, chain_id=CHAIN_ID_ANVIL
        )


def test_rfc8785_canonicalization_determinism():
    data1 = {"z": 1, "a": {"c": 3, "b": 2}}
    data2 = {"a": {"b": 2, "c": 3}, "z": 1}

    canon1 = canonicalize_json(data1)
    canon2 = canonicalize_json(data2)
    assert canon1 == canon2 == '{"a":{"b":2,"c":3},"z":1}'

    hash1 = compute_canonical_hash(data1)
    hash2 = compute_canonical_hash(data2)
    assert hash1 == hash2


def test_uuid_bytes32_roundtrip():
    test_id = uuid.uuid4()
    b32 = execution_id_to_bytes32(test_id)
    assert len(b32) == 66  # "0x" + 64 hex chars
    assert b32.startswith("0x")

    recovered = bytes32_to_execution_id(b32)
    assert recovered == test_id


def test_result_hash_bytes32_roundtrip():
    original_hash = "a" * 64
    b32 = result_hash_to_bytes32(original_hash)
    assert b32 == "0x" + "a" * 64

    recovered = bytes32_to_result_hash(b32)
    assert recovered == original_hash


@pytest.mark.asyncio
async def test_verification_api_verified(mock_session, sample_execution):
    notarization = MagicMock(spec=ResultNotarization)
    notarization.id = uuid.uuid4()
    notarization.execution_id = sample_execution.id
    notarization.result_hash = sample_execution.output_hash
    notarization.hash_algorithm = "SHA-256"
    notarization.canonicalization_version = "RFC-8785"
    notarization.status = NotarizationStatus.CONFIRMED.value
    notarization.is_canonical = True
    notarization.contract_address = "0x5FbDB2315678afecb367f032d93F642f64180aa5"
    notarization.transaction_hash = "0x" + "bb" * 32
    notarization.block_number = 100
    notarization.confirmations = 5
    notarization.confirmed_at = datetime.now(timezone.utc)

    mock_res = MagicMock()
    mock_res.scalar_one_or_none.return_value = notarization
    mock_session.execute.return_value = mock_res

    # 1. Verify with raw payload
    resp = await NotarizationService.verify_execution(
        mock_session,
        sample_execution.id,
        result_payload=sample_execution.output_data,
        chain_id=CHAIN_ID_ANVIL,
    )
    assert resp.verification_status == VerificationStatus.VERIFIED
    assert resp.computed_hash == sample_execution.output_hash

    # 2. Verify with explicit correct hash
    resp2 = await NotarizationService.verify_execution(
        mock_session,
        sample_execution.id,
        result_hash=sample_execution.output_hash,
        chain_id=CHAIN_ID_ANVIL,
    )
    assert resp2.verification_status == VerificationStatus.VERIFIED


@pytest.mark.asyncio
async def test_verification_api_hash_mismatch(mock_session, sample_execution):
    notarization = MagicMock(spec=ResultNotarization)
    notarization.id = uuid.uuid4()
    notarization.execution_id = sample_execution.id
    notarization.result_hash = sample_execution.output_hash
    notarization.hash_algorithm = "SHA-256"
    notarization.canonicalization_version = "RFC-8785"
    notarization.status = NotarizationStatus.CONFIRMED.value
    notarization.is_canonical = True
    notarization.contract_address = "0x5FbDB2315678afecb367f032d93F642f64180aa5"
    notarization.transaction_hash = "0x" + "bb" * 32
    notarization.block_number = 100
    notarization.confirmations = 5
    notarization.confirmed_at = datetime.now(timezone.utc)

    mock_res = MagicMock()
    mock_res.scalar_one_or_none.return_value = notarization
    mock_session.execute.return_value = mock_res

    # Verify with modified payload
    resp = await NotarizationService.verify_execution(
        mock_session,
        sample_execution.id,
        result_payload={"score": 999, "tampered": True},
        chain_id=CHAIN_ID_ANVIL,
    )
    assert resp.verification_status == VerificationStatus.HASH_MISMATCH

    # Verify with mismatched hash
    resp2 = await NotarizationService.verify_execution(
        mock_session,
        sample_execution.id,
        result_hash="0" * 64,
        chain_id=CHAIN_ID_ANVIL,
    )
    assert resp2.verification_status == VerificationStatus.HASH_MISMATCH


@pytest.mark.asyncio
async def test_verification_api_unconfirmed_and_reorged(mock_session, sample_execution):
    notarization = MagicMock(spec=ResultNotarization)
    notarization.id = uuid.uuid4()
    notarization.execution_id = sample_execution.id
    notarization.result_hash = sample_execution.output_hash
    notarization.hash_algorithm = "SHA-256"
    notarization.canonicalization_version = "RFC-8785"
    notarization.status = NotarizationStatus.PENDING.value
    notarization.is_canonical = True
    notarization.contract_address = "0x5FbDB2315678afecb367f032d93F642f64180aa5"
    notarization.transaction_hash = None
    notarization.block_number = None
    notarization.confirmations = 0
    notarization.confirmed_at = None

    mock_res = MagicMock()
    mock_res.scalar_one_or_none.return_value = notarization
    mock_session.execute.return_value = mock_res

    # Unconfirmed proof
    resp = await NotarizationService.verify_execution(
        mock_session, sample_execution.id, chain_id=CHAIN_ID_ANVIL
    )
    assert resp.verification_status == VerificationStatus.NOT_CONFIRMED

    # Reorged proof
    notarization.status = NotarizationStatus.REORGED.value
    notarization.is_canonical = False
    resp_reorg = await NotarizationService.verify_execution(
        mock_session, sample_execution.id, chain_id=CHAIN_ID_ANVIL
    )
    assert resp_reorg.verification_status == VerificationStatus.REORGED


@pytest.mark.asyncio
async def test_reconciliation_confirms_canonical_event(mock_session, sample_execution):
    notarization = MagicMock(spec=ResultNotarization)
    notarization.id = uuid.uuid4()
    notarization.chain_id = CHAIN_ID_ANVIL
    notarization.contract_address = "0x5fbdb2315678afecb367f032d93f642f64180aa5"
    notarization.execution_id = sample_execution.id
    notarization.result_hash = sample_execution.output_hash
    notarization.status = NotarizationStatus.PENDING.value
    notarization.transaction_hash = "0x" + "cc" * 32
    notarization.confirmations = 0

    mock_notary_res = MagicMock()
    mock_notary_res.scalar_one_or_none.return_value = notarization

    # No active reorgs
    mock_reorg_res = MagicMock()
    mock_reorg_res.scalars.return_value.first.return_value = None

    # Canonical event
    event = MagicMock(spec=BlockchainEvent)
    event.chain_id = CHAIN_ID_ANVIL
    event.contract_address = notarization.contract_address
    event.event_name = "ResultNotarized"
    event.is_canonical = True
    event.block_number = 50
    event.block_hash = "0x" + "50" * 32
    event.transaction_hash = notarization.transaction_hash
    event.status = EventStatus.CONFIRMED.value
    event.confirmed_at = datetime.now(timezone.utc)
    event.decoded_data = {
        "executionId": execution_id_to_bytes32(sample_execution.id),
        "resultHash": "0x" + sample_execution.output_hash,
    }

    # Tx events query (reorg check for transaction_hash)
    mock_tx_events_res = MagicMock()
    mock_tx_events_res.scalars.return_value.all.return_value = [event]

    # Canonical events query
    mock_events_res = MagicMock()
    mock_events_res.scalars.return_value.all.return_value = [event]

    # Head block query
    mock_head_res = MagicMock()
    mock_head_res.scalar.return_value = 55

    mock_session.execute.side_effect = [
        mock_notary_res,
        mock_reorg_res,
        mock_tx_events_res,
        mock_events_res,
        mock_head_res,
    ]

    reconciled = await NotarizationReconciliationService.reconcile_notarization(
        mock_session, notarization.id
    )
    assert reconciled.status == NotarizationStatus.CONFIRMED.value
    assert reconciled.confirmations >= 1
    assert reconciled.is_canonical is True
