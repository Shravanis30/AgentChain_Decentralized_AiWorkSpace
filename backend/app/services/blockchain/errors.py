"""Blockchain error classification and specific exception hierarchy.

Categorizes errors into:
- TRANSIENT: Network timeouts, 429 rate limits, connection drops, temporary node lag (safe to retry with backoff).
- PERMANENT: Bad calldata, unauthorized operations, contract reverts, chain ID mismatches (never retry).
- UNKNOWN: Unclassified exceptions (treated conservatively).
"""

import enum
from typing import Any


class ErrorClassification(str, enum.Enum):
    TRANSIENT = "TRANSIENT"
    PERMANENT = "PERMANENT"
    UNKNOWN = "UNKNOWN"


class BlockchainError(Exception):
    """Base exception for all blockchain errors."""

    classification: ErrorClassification = ErrorClassification.UNKNOWN

    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}


class TransientRpcError(BlockchainError):
    """Temporary RPC communication failure, retryable with exponential backoff."""
    classification = ErrorClassification.TRANSIENT


class PermanentRpcError(BlockchainError):
    """Permanent RPC failure, non-retryable."""
    classification = ErrorClassification.PERMANENT


class ChainMismatchError(PermanentRpcError):
    """RPC chain ID does not match configured chain ID."""
    pass


class ContractMismatchError(PermanentRpcError):
    """Contract address does not match allowlisted addresses."""
    pass


class MainnetSubmissionBlockedError(PermanentRpcError):
    """Hard safety barrier: Base Mainnet transaction submission is disabled in Phase 6.2A."""
    pass


class SignerNetworkMismatchError(PermanentRpcError):
    """Signer configuration does not match target blockchain network."""
    pass


class SignerIdentityMismatchError(PermanentRpcError):
    """Derived signer address does not match expected signer identity."""
    pass


class ProductionSignerUnavailableError(PermanentRpcError):
    """Production signer provider (KMS/HSM/MPC) is not integrated or available."""
    pass



class SecurityConfigurationError(PermanentRpcError):
    """Security invariant violated in configuration or execution parameters."""
    pass


class InvalidRoleAddressError(PermanentRpcError):
    """Role address configuration is invalid, zero, or defaults dangerously."""
    pass


class UnauthorizedOperationError(PermanentRpcError):
    """Contract operation is not in the allowlist."""
    pass


class DeepReorganizationError(PermanentRpcError):
    """Chain reorganization depth exceeds configured maximum tracking window."""
    pass


class ArbitraryCalldataBlockedError(PermanentRpcError):
    """Arbitrary raw calldata submission is strictly forbidden."""
    pass


class NonceCollisionError(BlockchainError):
    """Nonce collision or out-of-order nonce reservation detected."""
    classification = ErrorClassification.TRANSIENT


class TransactionRevertedError(PermanentRpcError):
    """Transaction mined but on-chain execution reverted."""
    pass


class ReorgDetectedError(BlockchainError):
    """Chain reorganization detected."""
    classification = ErrorClassification.TRANSIENT


class StuckTransactionError(BlockchainError):
    """Transaction stuck in mempool beyond threshold."""
    classification = ErrorClassification.TRANSIENT


class ReceiptTimeoutError(TransientRpcError):
    """Receipt polling timed out."""
    pass


def classify_rpc_error(exc: Exception) -> ErrorClassification:
    """Classify an arbitrary exception into TRANSIENT, PERMANENT, or UNKNOWN."""
    if isinstance(exc, BlockchainError):
        return exc.classification

    err_str = str(exc).lower()

    # Known transient error patterns
    transient_patterns = [
        "timeout",
        "timed out",
        "rate limit",
        "too many requests",
        "429",
        "connection reset",
        "connection refused",
        "temporarily unavailable",
        "502",
        "503",
        "504",
        "econnreset",
        "etimedout",
        "socket",
        "network error",
        "nonce too low",  # Re-fetching latest nonce can resolve
    ]
    for pattern in transient_patterns:
        if pattern in err_str:
            return ErrorClassification.TRANSIENT

    # Known permanent error patterns
    permanent_patterns = [
        "execution reverted",
        "invalid opcode",
        "insufficient funds",
        "gas required exceeds allowance",
        "invalid argument",
        "method not found",
        "unauthorized",
        "invalid signature",
        "decode error",
    ]
    for pattern in permanent_patterns:
        if pattern in err_str:
            return ErrorClassification.PERMANENT

    return ErrorClassification.UNKNOWN
