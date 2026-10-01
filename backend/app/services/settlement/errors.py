"""Settlement domain errors for Phase 6.2B."""


class SettlementError(Exception):
    """Base exception for all settlement operations."""
    pass


class SettlementNotFoundError(SettlementError):
    """Raised when a settlement record cannot be found."""
    pass


class SettlementAlreadyExistsError(SettlementError):
    """Raised when a settlement with identical logical parameters already exists."""
    pass


class SettlementAuthorizationError(SettlementError):
    """Raised when caller, role, or state conditions reject authorization."""
    pass


class SettlementEscrowVerificationError(SettlementError):
    """Raised when escrow validation fails against canonical chain state."""
    pass


class SettlementStateConflictError(SettlementError):
    """Raised when attempting an invalid state transition in the settlement lifecycle."""
    pass


class SettlementConfirmationError(SettlementError):
    """Raised when an unconfirmed or non-canonical escrow event is used."""
    pass


class SettlementOrchestrationMismatchError(SettlementError):
    """Raised when linked orchestration or execution has not completed successfully."""
    pass


class SettlementMainnetBlockedError(SettlementError):
    """Raised when an operation attempts settlement on Base Mainnet while disabled."""
    pass


class SettlementConcurrencyError(SettlementError):
    """Raised when concurrent operations attempt conflicting mutations on a settlement."""
    pass
