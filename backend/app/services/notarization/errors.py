"""Domain errors for Cryptographic Result Notarization."""


class NotarizationError(Exception):
    """Base exception for notarization subsystem."""


class ExecutionNotFoundError(NotarizationError):
    """Raised when the specified AgentExecution cannot be found."""


class ExecutionNotEligibleError(NotarizationError):
    """Raised when the AgentExecution is not eligible for notarization (e.g. not succeeded, cancelled)."""


class NotarizationNotFoundError(NotarizationError):
    """Raised when a notarization record is not found."""


class ResultHashImmutabilityViolationError(NotarizationError):
    """Raised when an attempt is made to mutate or overwrite an existing result hash."""


class NotarizationAuthorizationError(NotarizationError):
    """Raised on unauthorized notarization requests."""


class NotarizationVerificationError(NotarizationError):
    """Raised when verification encounters an invalid state."""
