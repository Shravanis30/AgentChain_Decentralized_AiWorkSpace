"""Observability metrics for Phase 6.5 — Reputation Scoring & Policy Engine.

Enforces bounded label cardinality:
- NEVER use agent_id, wallet, execution_id, or transaction hashes as labels.
- Only reason_code (low-cardinality) is used as a label.
"""

from app.core.metrics import metrics_registry


class ScoringMetrics:
    """Prometheus metrics for reputation scoring engine."""

    @staticmethod
    def record_calculation(reason_code: str = "unknown") -> None:
        metrics_registry.inc_counter(
            "reputation_score_calculation_total",
            1.0,
            {"reason_code": reason_code[:32]},
        )

    @staticmethod
    def record_failed(reason_code: str = "failed") -> None:
        metrics_registry.inc_counter(
            "reputation_score_calculation_failed_total",
            1.0,
            {"reason_code": reason_code[:32]},
        )

    @staticmethod
    def record_recalculation() -> None:
        metrics_registry.inc_counter("reputation_score_recalculation_total", 1.0)

    @staticmethod
    def record_reorg_recalculation() -> None:
        metrics_registry.inc_counter("reputation_score_reorg_recalculation_total", 1.0)

    @staticmethod
    def record_duration(seconds: float) -> None:
        metrics_registry.observe_histogram(
            "reputation_score_calculation_duration_seconds", seconds
        )

    @staticmethod
    def record_policy_version(policy_name: str, version_number: int) -> None:
        metrics_registry.inc_counter(
            "reputation_policy_version_total",
            1.0,
            {"policy_name": policy_name[:32]},
        )
