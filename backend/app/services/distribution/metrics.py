"""Observability metrics for the distribution subsystem using MetricsRegistry.

Enforces:
- No high-cardinality labels (no wallet addresses, tx hashes, or IDs).
"""

from app.core.metrics import metrics_registry


class DistributionMetrics:
    """Helper wrapper for recording distribution metrics without high-cardinality labels."""

    @staticmethod
    def record_created(chain_id: int, version: int = 1) -> None:
        metrics_registry.inc_counter(
            "distribution_created_total",
            1.0,
            {"chain_id": str(chain_id), "version": str(version)},
        )

    @staticmethod
    def record_submitted(chain_id: int, version: int = 1) -> None:
        metrics_registry.inc_counter(
            "distribution_submitted_total",
            1.0,
            {"chain_id": str(chain_id), "version": str(version)},
        )

    @staticmethod
    def record_confirmed(chain_id: int, version: int = 1) -> None:
        metrics_registry.inc_counter(
            "distribution_confirmed_total",
            1.0,
            {"chain_id": str(chain_id), "version": str(version)},
        )

    @staticmethod
    def record_failed(chain_id: int, reason_code: str = "failed") -> None:
        metrics_registry.inc_counter(
            "distribution_failed_total",
            1.0,
            {"chain_id": str(chain_id), "reason_code": reason_code[:32]},
        )

    @staticmethod
    def record_reorged(chain_id: int) -> None:
        metrics_registry.inc_counter(
            "distribution_reorged_total",
            1.0,
            {"chain_id": str(chain_id)},
        )

    @staticmethod
    def record_blocked(chain_id: int, reason_code: str = "blocked") -> None:
        metrics_registry.inc_counter(
            "distribution_blocked_total",
            1.0,
            {"chain_id": str(chain_id), "reason_code": reason_code[:32]},
        )

    @staticmethod
    def record_reconciliation(chain_id: int, outcome: str) -> None:
        metrics_registry.inc_counter(
            "distribution_reconciliation_total",
            1.0,
            {"chain_id": str(chain_id), "outcome": outcome[:32]},
        )
