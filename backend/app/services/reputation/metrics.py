"""Observability metrics for Verified Reputation Foundation.

Enforces:
- Bounded label set: only chain_id, outcome_type, or low-cardinality status codes.
- NEVER use agent_id, execution_id, wallet addresses, or transaction hashes as Prometheus labels.
- High-cardinality identifiers are reserved for structured logs and audit records.
"""

from app.core.metrics import metrics_registry


class ReputationMetrics:
    """Helper wrapper for recording reputation metrics with strictly bounded cardinality."""

    @staticmethod
    def record_created(chain_id: int, outcome_type: str) -> None:
        metrics_registry.inc_counter(
            "reputation_event_created_total",
            1.0,
            {"chain_id": str(chain_id), "outcome_type": outcome_type[:32]},
        )

    @staticmethod
    def record_confirmed(chain_id: int, outcome_type: str) -> None:
        metrics_registry.inc_counter(
            "reputation_event_confirmed_total",
            1.0,
            {"chain_id": str(chain_id), "outcome_type": outcome_type[:32]},
        )

    @staticmethod
    def record_failed(chain_id: int, reason_code: str = "failed") -> None:
        metrics_registry.inc_counter(
            "reputation_event_failed_total",
            1.0,
            {"chain_id": str(chain_id), "reason_code": reason_code[:32]},
        )

    @staticmethod
    def record_reorged(chain_id: int) -> None:
        metrics_registry.inc_counter(
            "reputation_event_reorged_total",
            1.0,
            {"chain_id": str(chain_id)},
        )

    @staticmethod
    def record_duplicate(chain_id: int) -> None:
        metrics_registry.inc_counter(
            "reputation_event_duplicate_total",
            1.0,
            {"chain_id": str(chain_id)},
        )

    @staticmethod
    def record_verification_failed(chain_id: int, reason_code: str = "verification_failed") -> None:
        metrics_registry.inc_counter(
            "reputation_verification_failed_total",
            1.0,
            {"chain_id": str(chain_id), "reason_code": reason_code[:32]},
        )

    @staticmethod
    def record_reconciliation_drift(chain_id: int) -> None:
        metrics_registry.inc_counter(
            "reputation_reconciliation_drift_total",
            1.0,
            {"chain_id": str(chain_id)},
        )

    @staticmethod
    def record_pending_age(chain_id: int, age_seconds: float) -> None:
        metrics_registry.record_histogram(
            "reputation_pending_age_seconds",
            age_seconds,
            {"chain_id": str(chain_id)},
        )
