from collections import defaultdict
import threading
import time
from typing import Any


class MetricsRegistry:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.counters: dict[str, float] = defaultdict(float)
        self.gauges: dict[str, float] = defaultdict(float)
        self.histograms: dict[str, list[float]] = defaultdict(list)

    def inc_counter(self, name: str, value: float = 1.0, labels: dict[str, str] | None = None) -> None:
        key = self._format_key(name, labels)
        with self._lock:
            self.counters[key] += value

    def set_gauge(self, name: str, value: float, labels: dict[str, str] | None = None) -> None:
        key = self._format_key(name, labels)
        with self._lock:
            self.gauges[key] = value

    def observe_histogram(self, name: str, value: float, labels: dict[str, str] | None = None) -> None:
        key = self._format_key(name, labels)
        with self._lock:
            self.histograms[key].append(value)
            if len(self.histograms[key]) > 1000:
                self.histograms[key] = self.histograms[key][-1000:]

    def _format_key(self, name: str, labels: dict[str, str] | None) -> str:
        if not labels:
            return name
        label_str = ",".join(f'{k}="{v}"' for k, v in sorted(labels.items()))
        return f"{name}{{{label_str}}}"

    def generate_prometheus_text(self) -> str:
        lines: list[str] = []
        with self._lock:
            # Counters
            for key, val in sorted(self.counters.items()):
                lines.append(f"{key} {val}")

            # Gauges
            for key, val in sorted(self.gauges.items()):
                lines.append(f"{key} {val}")

            # Histograms (basic count and sum for Prometheus format)
            for key, samples in sorted(self.histograms.items()):
                count = len(samples)
                total = sum(samples)
                base = key.split("{")[0]
                labels = key[len(base):] if "{" in key else ""
                lines.append(f"{base}_count{labels} {count}")
                lines.append(f"{base}_sum{labels} {total:.4f}")

        return "\n".join(lines) + "\n"


# Global singleton registry
metrics = MetricsRegistry()
metrics_registry = metrics

# Initialize required Phase 4 & Phase 4.1 metric names with defaults
metrics.set_gauge("queue_depth", 0)
metrics.set_gauge("queue_wait_seconds", 0)
metrics.set_gauge("worker_active_executions", 0)
metrics.set_gauge("worker_heartbeat_age", 0)
metrics.inc_counter("executions_total", 0)
metrics.inc_counter("executions_succeeded", 0)
metrics.inc_counter("executions_failed", 0)
metrics.inc_counter("executions_timed_out", 0)
metrics.inc_counter("executions_cancelled", 0)
metrics.inc_counter("sandbox_failures", 0)

# Phase 4.1 metrics
metrics.set_gauge("outbox_pending", 0)
metrics.inc_counter("outbox_publish_success_total", 0)
metrics.inc_counter("outbox_publish_failure_total", 0)
metrics.inc_counter("execution_duplicate_delivery_total", 0)
metrics.inc_counter("execution_stale_lease_total", 0)
metrics.inc_counter("execution_finalization_conflict_total", 0)
metrics.inc_counter("sse_auth_failure_total", 0)
metrics.inc_counter("sandbox_docker_integration_failures_total", 0)

# Phase 5 Orchestrator metrics
metrics.inc_counter("orchestration_created_total", 0)
metrics.inc_counter("orchestration_started_total", 0)
metrics.inc_counter("orchestration_succeeded_total", 0)
metrics.inc_counter("orchestration_failed_total", 0)
metrics.inc_counter("orchestration_cancelled_total", 0)

metrics.inc_counter("orchestration_task_created_total", 0)
metrics.inc_counter("orchestration_task_started_total", 0)
metrics.inc_counter("orchestration_task_succeeded_total", 0)
metrics.inc_counter("orchestration_task_failed_total", 0)
metrics.inc_counter("orchestration_task_retry_total", 0)

metrics.set_gauge("orchestration_active_tasks", 0)
metrics.observe_histogram("orchestration_duration_seconds", 0)

metrics.inc_counter("orchestration_checkpoint_save_total", 0)
metrics.inc_counter("orchestration_checkpoint_restore_total", 0)
metrics.inc_counter("orchestration_checkpoint_failure_total", 0)

metrics.inc_counter("orchestration_duplicate_schedule_total", 0)
metrics.inc_counter("orchestration_cycle_rejection_total", 0)
metrics.inc_counter("orchestration_graph_limit_rejection_total", 0)

# Phase 5.1 Reliability Hardening metrics
metrics.inc_counter("orchestration_wakeup_total", 0)
metrics.inc_counter("orchestration_wakeup_duplicate_total", 0)
metrics.inc_counter("orchestration_wakeup_failure_total", 0)
metrics.inc_counter("orchestration_concurrent_advance_conflict_total", 0)
metrics.inc_counter("orchestration_recovery_total", 0)
metrics.inc_counter("orchestration_execution_attempt_duplicate_total", 0)

# Phase 5.2 Durable Wake-Up & Recovery Hardening metrics
metrics.inc_counter("orchestration_wakeup_outbox_created_total", 0)
metrics.inc_counter("orchestration_wakeup_dispatch_total", 0)
metrics.inc_counter("orchestration_wakeup_dispatch_failure_total", 0)
metrics.inc_counter("orchestration_wakeup_dispatch_retry_total", 0)
metrics.inc_counter("orchestration_wakeup_recovery_total", 0)
metrics.inc_counter("orchestration_wakeup_recovery_stale_total", 0)
metrics.inc_counter("orchestration_wakeup_reconciliation_total", 0)

# Phase 6.2A Blockchain Indexer & Tx Infrastructure metrics
metrics.inc_counter("blockchain_blocks_scanned_total", 0)
metrics.inc_counter("blockchain_blocks_confirmed_total", 0)
metrics.inc_counter("blockchain_events_indexed_total", 0)
metrics.inc_counter("blockchain_duplicate_events_total", 0)
metrics.inc_counter("blockchain_reorgs_total", 0)
metrics.inc_counter("blockchain_rpc_errors_total", 0)
metrics.set_gauge("blockchain_indexing_lag_blocks", 0)
metrics.inc_counter("blockchain_intents_created_total", 0)
metrics.inc_counter("blockchain_transactions_submitted_total", 0)
metrics.inc_counter("blockchain_confirmed_transactions_total", 0)
metrics.inc_counter("blockchain_failed_transactions_total", 0)
metrics.inc_counter("blockchain_replaced_transactions_total", 0)
metrics.inc_counter("blockchain_nonce_conflicts_total", 0)
metrics.inc_counter("blockchain_gas_bumps_total", 0)
metrics.set_gauge("blockchain_pending_transactions", 0)
metrics.set_gauge("blockchain_outbox_pending_gauge", 0)
metrics.inc_counter("blockchain_outbox_dispatched_total", 0)
metrics.inc_counter("blockchain_outbox_retries_total", 0)
metrics.inc_counter("blockchain_outbox_dead_letters_total", 0)
metrics.inc_counter("blockchain_reconciliation_mismatches_found_total", 0)
metrics.inc_counter("blockchain_reconciliation_mismatches_repaired_total", 0)

# Phase 6.4 Reputation Evidence metrics
metrics.inc_counter("reputation_event_created_total", 0)
metrics.inc_counter("reputation_event_confirmed_total", 0)
metrics.inc_counter("reputation_event_failed_total", 0)
metrics.inc_counter("reputation_event_reorged_total", 0)
metrics.inc_counter("reputation_event_duplicate_total", 0)
metrics.inc_counter("reputation_verification_failed_total", 0)
metrics.inc_counter("reputation_reconciliation_drift_total", 0)
metrics.observe_histogram("reputation_pending_age_seconds", 0)

# Phase 6.5 Reputation Scoring & Policy Engine metrics
metrics.inc_counter("reputation_score_calculation_total", 0)
metrics.inc_counter("reputation_score_calculation_failed_total", 0)
metrics.inc_counter("reputation_score_recalculation_total", 0)
metrics.inc_counter("reputation_score_reorg_recalculation_total", 0)
metrics.inc_counter("reputation_policy_version_total", 0)
metrics.observe_histogram("reputation_score_calculation_duration_seconds", 0)
