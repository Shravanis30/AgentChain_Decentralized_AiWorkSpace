"""Marketplace lifecycle domain exceptions."""


class MarketplaceLifecycleError(Exception):
    """Base exception for marketplace lifecycle operations."""


class MarketplaceOrderNotFoundError(MarketplaceLifecycleError):
    """Raised when a marketplace order is not found."""


class MarketplaceOrderingViolationError(MarketplaceLifecycleError):
    """Raised when an operation violates strict lifecycle ordering (e.g. execution before confirmed escrow)."""


class MarketplaceEscrowMismatchError(MarketplaceLifecycleError):
    """Raised when escrow parameters (amount, client, beneficiary, token) diverge from authoritative pinned selection."""


class MarketplaceEscrowUnconfirmedError(MarketplaceLifecycleError):
    """Raised when escrow has not reached canonical confirmation (32-block depth policy)."""


class MarketplaceStateConflictError(MarketplaceLifecycleError):
    """Raised when attempting an invalid state transition in the marketplace state machine."""


class MarketplacePriceTamperError(MarketplaceLifecycleError):
    """Raised when an attempted action violates price pinning invariants."""


class MarketplaceAuthorizationError(MarketplaceLifecycleError):
    """Raised when caller lacks required authorization or role for a marketplace transition."""
