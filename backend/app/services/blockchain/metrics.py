"""Blockchain observability metrics using the core MetricsRegistry.

Tracks:
- Indexer: blocks scanned, blocks confirmed, events indexed, duplicates, reorgs, RPC errors, lag.
- Transactions: intents created, transactions submitted, pending, confirmed, failed, replaced, nonce conflicts, gas bumps.
- Outbox: pending, dispatched, retries, dead letters.
- Reconciliation: mismatches found, repaired, stale txs.
"""

from app.core.metrics import metrics_registry


class BlockchainMetrics:
    """Helper wrapper for recording blockchain metrics into global registry."""

    # --- Indexer Metrics ---
    @staticmethod
    def record_block_scanned(chain_id: int, count: int = 1) -> None:
        metrics_registry.inc_counter("blockchain_blocks_scanned_total", float(count), {"chain_id": str(chain_id)})

    @staticmethod
    def record_block_confirmed(chain_id: int, count: int = 1) -> None:
        metrics_registry.inc_counter("blockchain_blocks_confirmed_total", float(count), {"chain_id": str(chain_id)})

    @staticmethod
    def record_event_indexed(chain_id: int, event_name: str, count: int = 1) -> None:
        metrics_registry.inc_counter("blockchain_events_indexed_total", float(count), {"chain_id": str(chain_id), "event": event_name})

    @staticmethod
    def record_duplicate_event(chain_id: int) -> None:
        metrics_registry.inc_counter("blockchain_duplicate_events_total", 1.0, {"chain_id": str(chain_id)})

    @staticmethod
    def record_reorg(chain_id: int, depth: int) -> None:
        metrics_registry.inc_counter("blockchain_reorgs_total", 1.0, {"chain_id": str(chain_id)})
        metrics_registry.observe_histogram("blockchain_reorg_depth", float(depth), {"chain_id": str(chain_id)})

    @staticmethod
    def record_rpc_error(chain_id: int, classification: str) -> None:
        metrics_registry.inc_counter("blockchain_rpc_errors_total", 1.0, {"chain_id": str(chain_id), "type": classification})

    @staticmethod
    def set_indexing_lag(chain_id: int, lag_blocks: int) -> None:
        metrics_registry.set_gauge("blockchain_indexing_lag_blocks", float(lag_blocks), {"chain_id": str(chain_id)})

    # --- Transaction & Relayer Metrics ---
    @staticmethod
    def record_intent_created(chain_id: int, operation: str) -> None:
        metrics_registry.inc_counter("blockchain_intents_created_total", 1.0, {"chain_id": str(chain_id), "operation": operation})

    @staticmethod
    def record_transaction_submitted(chain_id: int) -> None:
        metrics_registry.inc_counter("blockchain_transactions_submitted_total", 1.0, {"chain_id": str(chain_id)})

    @staticmethod
    def record_transaction_confirmed(chain_id: int) -> None:
        metrics_registry.inc_counter("blockchain_confirmed_transactions_total", 1.0, {"chain_id": str(chain_id)})

    @staticmethod
    def record_transaction_failed(chain_id: int, reason: str = "reverted") -> None:
        metrics_registry.inc_counter("blockchain_failed_transactions_total", 1.0, {"chain_id": str(chain_id), "reason": reason})

    @staticmethod
    def record_transaction_replaced(chain_id: int) -> None:
        metrics_registry.inc_counter("blockchain_replaced_transactions_total", 1.0, {"chain_id": str(chain_id)})

    @staticmethod
    def record_nonce_conflict(chain_id: int) -> None:
        metrics_registry.inc_counter("blockchain_nonce_conflicts_total", 1.0, {"chain_id": str(chain_id)})

    @staticmethod
    def record_gas_bump(chain_id: int) -> None:
        metrics_registry.inc_counter("blockchain_gas_bumps_total", 1.0, {"chain_id": str(chain_id)})

    @staticmethod
    def set_pending_transactions(chain_id: int, count: int) -> None:
        metrics_registry.set_gauge("blockchain_pending_transactions", float(count), {"chain_id": str(chain_id)})

    # --- Outbox Metrics ---
    @staticmethod
    def set_outbox_pending(chain_id: int, count: int) -> None:
        metrics_registry.set_gauge("blockchain_outbox_pending_gauge", float(count), {"chain_id": str(chain_id)})

    @staticmethod
    def record_outbox_dispatched(chain_id: int, count: int = 1) -> None:
        metrics_registry.inc_counter("blockchain_outbox_dispatched_total", float(count), {"chain_id": str(chain_id)})

    @staticmethod
    def record_outbox_retry(chain_id: int) -> None:
        metrics_registry.inc_counter("blockchain_outbox_retries_total", 1.0, {"chain_id": str(chain_id)})

    @staticmethod
    def record_outbox_dead_letter(chain_id: int) -> None:
        metrics_registry.inc_counter("blockchain_outbox_dead_letters_total", 1.0, {"chain_id": str(chain_id)})

    # --- Reconciliation Metrics ---
    @staticmethod
    def record_reconciliation_mismatches(chain_id: int, found: int, repaired: int) -> None:
        metrics_registry.inc_counter("blockchain_reconciliation_mismatches_found_total", float(found), {"chain_id": str(chain_id)})
        metrics_registry.inc_counter("blockchain_reconciliation_mismatches_repaired_total", float(repaired), {"chain_id": str(chain_id)})

    @staticmethod
    def observe_reconciliation_duration(chain_id: int, seconds: float) -> None:
        metrics_registry.observe_histogram("blockchain_reconciliation_duration_seconds", seconds, {"chain_id": str(chain_id)})

    # --- Phase 6.2A.1 Hardening Metrics ---
    @staticmethod
    def record_block_reorg_conflict(chain_id: int) -> None:
        metrics_registry.inc_counter("blockchain_block_reorg_conflicts_total", 1.0, {"chain_id": str(chain_id)})

    @staticmethod
    def record_canonical_block_replacement(chain_id: int) -> None:
        metrics_registry.inc_counter("blockchain_canonical_block_replacements_total", 1.0, {"chain_id": str(chain_id)})

    @staticmethod
    def record_orphaned_event(chain_id: int, count: int = 1) -> None:
        metrics_registry.inc_counter("blockchain_orphaned_events_total", float(count), {"chain_id": str(chain_id)})

    @staticmethod
    def record_event_reindex(chain_id: int, count: int = 1) -> None:
        metrics_registry.inc_counter("blockchain_event_reindex_operations_total", float(count), {"chain_id": str(chain_id)})

    @staticmethod
    def record_replacement_safety_check(chain_id: int, decision: str) -> None:
        metrics_registry.inc_counter("blockchain_replacement_safety_checks_total", 1.0, {"chain_id": str(chain_id), "decision": decision})

    @staticmethod
    def record_replacement_deferred_unknown_rpc(chain_id: int) -> None:
        metrics_registry.inc_counter("blockchain_replacement_deferred_unknown_rpc_total", 1.0, {"chain_id": str(chain_id)})

    @staticmethod
    def record_replacement_race_prevented(chain_id: int) -> None:
        metrics_registry.inc_counter("blockchain_replacement_race_prevented_total", 1.0, {"chain_id": str(chain_id)})
