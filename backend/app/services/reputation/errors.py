"""Domain exceptions for verified reputation evidence layer.
All validation errors fail closed.
"""


class ReputationError(Exception):
    """Base exception for all reputation domain errors."""


class ExecutionNotFoundError(ReputationError):
    """Raised when the referenced agent execution cannot be found."""


class ExecutionNotTerminalError(ReputationError):
    """Raised when attempting to create reputation evidence for a non-terminal execution."""


class ResultNotNotarizedError(ReputationError):
    """Raised when VERIFIED_SUCCESS outcome references an execution that has no canonical ResultNotary proof."""


class ResultHashMismatchError(ReputationError):
    """Raised when execution result hash does not match ResultNotary proof."""


class NotarizationNotCanonicalError(ReputationError):
    """Raised when ResultNotary proof is non-canonical or reorged."""


class InsufficientConfirmationsError(ReputationError):
    """Raised when ResultNotary proof has not satisfied required confirmation depth."""


class AgentIdentityMismatchError(ReputationError):
    """Raised when execution agent ID does not match the reputation record or caller claim."""


class VersionIdentityMismatchError(ReputationError):
    """Raised when execution version does not match agent version."""


class InvalidOutcomeClassificationError(ReputationError):
    """Raised when outcome type is invalid or incompatible with the execution terminal state."""


class ReputationEventAlreadyExistsError(ReputationError):
    """Raised on idempotent duplicate registration attempt."""


class ConflictingReputationEventError(ReputationError):
    """Raised when attempting to register a second reputation event for the same execution with differing parameters."""


class MainnetReputationBlockedError(ReputationError):
    """Raised when an operation attempts to submit reputation evidence to Base Mainnet."""
