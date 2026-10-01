"""Verified Reputation Service package."""

from app.services.reputation.errors import (
    AgentIdentityMismatchError,
    ConflictingReputationEventError,
    ExecutionNotFoundError,
    ExecutionNotTerminalError,
    InsufficientConfirmationsError,
    InvalidOutcomeClassificationError,
    MainnetReputationBlockedError,
    NotarizationNotCanonicalError,
    ReputationError,
    ResultHashMismatchError,
    ResultNotNotarizedError,
)
from app.services.reputation.metrics import ReputationMetrics
from app.services.reputation.reconciliation import ReputationReconciliationService
from app.services.reputation.service import ReputationService
from app.services.reputation.utils import (
    bytes32_to_uuid,
    canonicalize_evidence,
    compute_evidence_hash,
    compute_reputation_key,
    uuid_to_bytes32,
)

__all__ = [
    "AgentIdentityMismatchError",
    "ConflictingReputationEventError",
    "ExecutionNotFoundError",
    "ExecutionNotTerminalError",
    "InsufficientConfirmationsError",
    "InvalidOutcomeClassificationError",
    "MainnetReputationBlockedError",
    "NotarizationNotCanonicalError",
    "ReputationError",
    "ReputationMetrics",
    "ReputationReconciliationService",
    "ReputationService",
    "ResultHashMismatchError",
    "ResultNotNotarizedError",
    "bytes32_to_uuid",
    "canonicalize_evidence",
    "compute_evidence_hash",
    "compute_reputation_key",
    "uuid_to_bytes32",
]
