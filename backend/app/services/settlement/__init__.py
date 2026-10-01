"""Settlement Subsystem Package for AgentChain (Phase 6.2B)."""

from app.models.settlement import (
    Settlement,
    SettlementAction,
    SettlementHistory,
    SettlementStatus,
)
from app.services.settlement.authorizer import (
    AuthorizationDecision,
    SettlementAuthorizationEngine,
)
from app.services.settlement.config import (
    BLOCK_MAINNET_SETTLEMENT,
    SETTLEMENT_CONFIRMATION_THRESHOLDS,
    SUPPORTED_SETTLEMENT_CHAINS,
    get_required_confirmations,
    is_chain_supported,
)
from app.services.settlement.errors import (
    SettlementAlreadyExistsError,
    SettlementAuthorizationError,
    SettlementConfirmationError,
    SettlementError,
    SettlementEscrowVerificationError,
    SettlementMainnetBlockedError,
    SettlementNotFoundError,
    SettlementOrchestrationMismatchError,
    SettlementStateConflictError,
)
from app.services.settlement.idempotency import generate_settlement_idempotency_key
from app.services.settlement.metrics import SettlementMetrics
from app.services.settlement.reconciliation import SettlementReconciliationService
from app.services.settlement.service import SettlementService
from app.services.settlement.verifier import (
    EscrowVerificationBoundary,
    EscrowVerificationResult,
)

__all__ = [
    "AuthorizationDecision",
    "BLOCK_MAINNET_SETTLEMENT",
    "EscrowVerificationBoundary",
    "EscrowVerificationResult",
    "SETTLEMENT_CONFIRMATION_THRESHOLDS",
    "SUPPORTED_SETTLEMENT_CHAINS",
    "Settlement",
    "SettlementAction",
    "SettlementAlreadyExistsError",
    "SettlementAuthorizationEngine",
    "SettlementAuthorizationError",
    "SettlementConfirmationError",
    "SettlementError",
    "SettlementEscrowVerificationError",
    "SettlementHistory",
    "SettlementMainnetBlockedError",
    "SettlementMetrics",
    "SettlementNotFoundError",
    "SettlementOrchestrationMismatchError",
    "SettlementReconciliationService",
    "SettlementService",
    "SettlementStateConflictError",
    "SettlementStatus",
    "generate_settlement_idempotency_key",
    "get_required_confirmations",
    "is_chain_supported",
]
