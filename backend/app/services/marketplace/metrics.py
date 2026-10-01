"""Prometheus / telemetry metrics for marketplace execution and escrow lifecycle."""

import time
from app.core.metrics import metrics


class MarketplaceMetrics:
    """Telemetry helper for marketplace lifecycle transitions."""

    @staticmethod
    def record_order_created(status: str) -> None:
        metrics.inc_counter("marketplace_orders_created_total", labels={"status": status})

    @staticmethod
    def record_state_transition(from_state: str, to_state: str) -> None:
        metrics.inc_counter("marketplace_state_transitions_total", labels={"from": from_state, "to": to_state})

    @staticmethod
    def record_ordering_violation(operation: str) -> None:
        metrics.inc_counter("marketplace_ordering_violations_total", labels={"operation": operation})

    @staticmethod
    def record_escrow_verification(outcome: str) -> None:
        metrics.inc_counter("marketplace_escrow_verifications_total", labels={"outcome": outcome})

    @staticmethod
    def record_settlement(action: str, status: str) -> None:
        metrics.inc_counter("marketplace_settlements_total", labels={"action": action, "status": status})

    @staticmethod
    def record_distribution(status: str) -> None:
        metrics.inc_counter("marketplace_distributions_total", labels={"status": status})
