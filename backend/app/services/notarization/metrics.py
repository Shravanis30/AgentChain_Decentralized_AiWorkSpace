"""Observability metrics for Cryptographic Result Notarization.

Enforces:
- Bounded label set: only chain_id, status, or low-cardinality reason codes.
- NEVER use execution IDs, wallet addresses, transaction hashes, or result hashes as labels.
"""

from app.core.metrics import metrics_registry


class NotarizationMetrics:
    """Helper wrapper for recording notarization metrics with strictly bounded cardinality."""

    @staticmethod
    def record_requested(chain_id: int) -> None:
        metrics_registry.inc_counter(
            "notarization_requested_total",
            1.0,
            {"chain_id": str(chain_id)},
        )

    @staticmethod
    def record_created(chain_id: int) -> None:
        metrics_registry.inc_counter(
            "notarization_created_total",
            1.0,
            {"chain_id": str(chain_id)},
        )

    @staticmethod
    def record_submitted(chain_id: int) -> None:
        metrics_registry.inc_counter(
            "notarization_submitted_total",
            1.0,
            {"chain_id": str(chain_id)},
        )

    @staticmethod
    def record_confirmed(chain_id: int) -> None:
        metrics_registry.inc_counter(
            "notarization_confirmed_total",
            1.0,
            {"chain_id": str(chain_id)},
        )

    @staticmethod
    def record_failed(chain_id: int, reason_code: str = "failed") -> None:
        metrics_registry.inc_counter(
            "notarization_failed_total",
            1.0,
            {"chain_id": str(chain_id), "reason_code": reason_code[:32]},
        )

    @staticmethod
    def record_reorged(chain_id: int) -> None:
        metrics_registry.inc_counter(
            "notarization_reorged_total",
            1.0,
            {"chain_id": str(chain_id)},
        )

    @staticmethod
    def record_duplicate(chain_id: int) -> None:
        metrics_registry.inc_counter(
            "notarization_duplicate_total",
            1.0,
            {"chain_id": str(chain_id)},
        )

    @staticmethod
    def record_verification_mismatch(chain_id: int) -> None:
        metrics_registry.inc_counter(
            "notarization_verification_mismatch_total",
            1.0,
            {"chain_id": str(chain_id)},
        )

    @staticmethod
    def record_unauthorized_attempt(reason_code: str = "unauthorized") -> None:
        metrics_registry.inc_counter(
            "unauthorized_notarization_attempt_total",
            1.0,
            {"reason_code": reason_code[:32]},
        )
