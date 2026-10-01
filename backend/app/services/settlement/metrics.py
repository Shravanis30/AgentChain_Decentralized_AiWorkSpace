"""Observability metrics for the settlement subsystem using MetricsRegistry."""

from app.core.metrics import metrics_registry


class SettlementMetrics:
    """Helper wrapper for recording settlement metrics."""

    @staticmethod
    def record_authorization(action: str, status: str) -> None:
        metrics_registry.inc_counter(
            "settlement_authorization_total",
            1.0,
            {"action": action, "status": status},
        )

    @staticmethod
    def record_authorization_blocked(action: str, reason: str) -> None:
        metrics_registry.inc_counter(
            "settlement_authorization_blocked_total",
            1.0,
            {"action": action, "reason": reason[:64]},
        )

    @staticmethod
    def record_submitted(chain_id: int, action: str) -> None:
        metrics_registry.inc_counter(
            "settlement_submitted_total",
            1.0,
            {"chain_id": str(chain_id), "action": action},
        )

    @staticmethod
    def record_confirmed(chain_id: int, action: str) -> None:
        metrics_registry.inc_counter(
            "settlement_confirmed_total",
            1.0,
            {"chain_id": str(chain_id), "action": action},
        )

    @staticmethod
    def record_failed(chain_id: int, action: str, error_code: str = "failed") -> None:
        metrics_registry.inc_counter(
            "settlement_failed_total",
            1.0,
            {"chain_id": str(chain_id), "action": action, "error_code": error_code[:32]},
        )

    @staticmethod
    def record_reorg_invalidated(chain_id: int) -> None:
        metrics_registry.inc_counter(
            "settlement_reorg_invalidated_total",
            1.0,
            {"chain_id": str(chain_id)},
        )

    @staticmethod
    def record_duplicate(chain_id: int, action: str) -> None:
        metrics_registry.inc_counter(
            "settlement_duplicate_total",
            1.0,
            {"chain_id": str(chain_id), "action": action},
        )

    @staticmethod
    def record_reconciliation(chain_id: int, action: str) -> None:
        metrics_registry.inc_counter(
            "settlement_reconciliation_total",
            1.0,
            {"chain_id": str(chain_id), "action": action},
        )

    @staticmethod
    def record_mainnet_blocked() -> None:
        metrics_registry.inc_counter(
            "settlement_mainnet_blocked_total",
            1.0,
            {},
        )

    @staticmethod
    def observe_latency(action: str, phase: str, duration_seconds: float) -> None:
        metrics_registry.observe_histogram(
            "settlement_latency_seconds",
            duration_seconds,
            {"action": action, "phase": phase},
        )
