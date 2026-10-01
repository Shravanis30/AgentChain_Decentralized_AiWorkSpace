"""Domain-specific exceptions for AgentChain distribution subsystem."""


class DistributionError(Exception):
    """Base exception for all distribution operations."""
    pass


class DistributionNotFoundError(DistributionError):
    """Raised when a referenced distribution cannot be found."""
    pass


class DistributionStateConflictError(DistributionError):
    """Raised when an action is invalid for the distribution's current state."""
    pass


class DistributionAuthorizationError(DistributionError):
    """Raised when a caller or policy rejects distribution creation/execution."""
    pass


class DistributionConservationError(DistributionError):
    """Raised when the financial conservation invariant is violated."""
    pass


class DistributionDuplicateRecipientError(DistributionError):
    """Raised when any two recipients (developer, staker, dao) share the same address."""
    pass


class DistributionMainnetBlockedError(DistributionError):
    """Raised when distribution on Base Mainnet is requested while mainnet settlement is hard-blocked."""
    pass
