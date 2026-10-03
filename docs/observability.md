# AgentChain: Observability & Telemetry Architecture

## 1. Observability Pillars & Principles

AgentChain implements end-to-end distributed observability across API requests, LangGraph task executions, LLM provider reasoning steps, vector searches, and on-chain transactions.

```
+-----------------------------------------------------------------------------------+
|                            UNIFIED TELEMETRY PIPELINE                             |
|                                                                                   |
|  [ FastAPI Gateway ]      [ LangGraph Engine ]      [ Blockchain Relayer Worker ] |
|  - Request Latency (p99)  - Node Execution Time     - Gas Used & Tx Latency       |
|  - HTTP Status Codes      - LLM Token & Cost Meter  - Block Confirmation Lag      |
|  - Correlation IDs        - Step Transition Status  - On-Chain Event Depth        |
|           |                         |                          |                  |
|           +-------------------------+--------------------------+                  |
|                                     |                                             |
|                                     v                                             |
|                     +-------------------------------+                             |
|                     | OpenTelemetry Collector (OTel)|                             |
|                     +-------------------------------+                             |
|                                     |                                             |
|             +-----------------------+-----------------------+                     |
|             v                       v                       v                     |
|   +--------------------+  +--------------------+  +--------------------+          |
|   | Prometheus / Mimir |  | Jaeger / Tempo     |  | LangSmith / Arize  |          |
|   | (Metrics Engine)   |  | (Distributed Trace)|  | (LLM Tracing & Eval|          |
|   +--------------------+  +--------------------+  +--------------------+          |
|             |                       |                                             |
|             v                       v                                             |
|   +--------------------------------------------+                                  |
|   | Grafana Central Dashboards & Alertmanager  |                                  |
|   +--------------------------------------------+                                  |
+-----------------------------------------------------------------------------------+
```

---

## 2. Distributed Tracing & Correlation IDs

Every inbound request, task dispatch, and background worker job is stamped with a unique `X-Correlation-ID` and OpenTelemetry `trace_id`.
- **Propagation:**
  - Injected by Next.js client or API Gateway.
  - Forwarded through FastAPI middleware into LangGraph execution context.
  - Attached to Redis job messages, Qdrant vector queries, and S3 object metadata.
  - Included in all structured JSON log lines:
    ```json
    {
      "timestamp": "2026-09-26T00:15:30.124Z",
      "level": "INFO",
      "trace_id": "4bf92f3577b34da6a3ce929d0e0e4736",
      "correlation_id": "corr_8fa01b22",
      "task_id": "f47ac10b-58cc-4372-a567-0e02b2c3d479",
      "step_key": "static_analysis",
      "agent_id": "7b8e5c1a-8210-41ab-85d1-6d7b4e9f1234",
      "message": "Invoked tool slither_runner with target Bridge.sol"
    }
    ```

---

## 3. Metrics Specification (Prometheus)

### 3.1 Business & Financial Metrics
| Metric Name | Type | Labels | Description |
| :--- | :--- | :--- | :--- |
| `agentchain_escrow_tvl_usdc` | Gauge | `chain_id` | Current total value locked in `Escrow.sol` |
| `agentchain_task_created_total` | Counter | `requester` | Total tasks created |
| `agentchain_task_settled_total` | Counter | `status` | Completed vs Disputed vs Failed tasks |
| `agentchain_platform_fee_collected_usdc` | Counter | `chain_id` | Cumulative protocol revenue |

### 3.2 AI & Orchestration Metrics
| Metric Name | Type | Labels | Description |
| :--- | :--- | :--- | :--- |
| `agentchain_dag_node_duration_seconds` | Histogram | `step_key`, `node_type` | Latency distribution of individual DAG nodes |
| `agentchain_llm_tokens_total` | Counter | `provider`, `model`, `token_type` | Prompt vs Completion tokens consumed |
| `agentchain_llm_cost_estimated_usd` | Counter | `provider`, `model` | Estimated API compute spend |
| `agentchain_agent_verification_score` | Histogram | `agent_id` | Distribution of QA verification scores |
| `agentchain_qdrant_query_duration_seconds` | Histogram | `collection` | Vector search retrieval latency |

### 3.3 Infrastructure, Worker & Execution Metrics (Phase 4)
| Metric Name | Type | Labels | Description |
| :--- | :--- | :--- | :--- |
| `executions_total` | Counter | - | Total agent executions received |
| `executions_succeeded` | Counter | - | Total agent executions completed successfully |
| `executions_failed` | Counter | - | Total agent executions that ended in failure |
| `executions_timed_out` | Counter | - | Total executions terminating due to timeout |
| `executions_cancelled` | Counter | - | Total executions cancelled by client |
| `execution_duration_seconds` | Histogram | - | Sandbox execution run latency in seconds |
| `queue_depth` | Gauge | - | Active jobs waiting in Redis Streams |
| `queue_wait_seconds` | Gauge | - | Queue waiting duration before worker pickup |
| `worker_active_executions` | Gauge | - | Currently running sandboxes across workers |
| `worker_heartbeat_age` | Gauge | - | Seconds since last heartbeat recorded |
| `sandbox_failures` | Counter | - | Failures attributable to container sandbox startup |

### 3.5 Verified Reputation Metrics (Phase 6.4)
Bounded cardinality Prometheus metrics for the verified reputation foundation:

| Metric Name | Type | Labels | Description |
| :--- | :--- | :--- | :--- |
| `reputation_event_created_total` | Counter | `chain_id`, `outcome_type` | Total reputation events registered in backend |
| `reputation_event_confirmed_total` | Counter | `chain_id` | Reputation events reaching authoritative confirmation |
| `reputation_event_failed_total` | Counter | `chain_id`, `reason` | Reputation creation / reconciliation failures |
| `reputation_event_reorged_total` | Counter | `chain_id` | Reputation events invalidated due to blockchain reorg |
| `reputation_event_duplicate_total` | Counter | `chain_id` | Idempotent duplicate event registration requests |
| `reputation_verification_failed_total` | Counter | `reason` | Verification requests failing proof checks |
| `reputation_reconciliation_drift_total` | Counter | `chain_id` | Discrepancies detected between DB and chain status |
| `reputation_pending_age_seconds` | Gauge | `chain_id` | Age of oldest unconfirmed reputation event |

*Note: In accordance with cardinality constraints, high-cardinality values such as `agent_id`, `execution_id`, `wallet_address`, or `tx_hash` are strictly excluded from Prometheus labels and recorded solely in structured JSON audit logs.*

### 3.4 Structured Logging & Security Redaction Rules
Every worker log line must be structured JSON containing:
`request_id`, `execution_id`, `agent_id`, `agent_version`, `worker_id`, `attempt`, `status`, `duration_ms`.

**Mandatory Security Redaction:**
Workers must NEVER log:
- Wallet private keys or seed phrases
- Session tokens or SIWE authorization headers
- Raw un-sanitized user input payloads
- Unrestricted raw agent outputs
- Environment secrets (AWS, DB, Redis passwords)

---

## 4. LLM Tracing & Deep Inspection

For deep inspection of multi-agent reasoning, tool calling, and RAG retrieval quality:
- Integration with **OpenInference / LangSmith**:
  - Full prompt-response pairs, token counts, and temperature settings.
  - Tool calling schemas, arguments, and raw returned payloads.
  - Retrieval context relevance scores from Qdrant.
  - Trace lineage visualizing parent-child step execution graphs.

---

## 5. Alerting Policies (Alertmanager)

1. **Escrow Underflow / Discrepancy (CRITICAL):**
   - Condition: `Postgres total escrow budget != Smart Contract locked balance`
   - Action: PagerDuty alert to on-call engineer; automated safety pause on task scheduling.
2. **Blockchain Sync Stalled (HIGH):**
   - Condition: `agentchain_blockchain_sync_lag_blocks > 50` for > 5 minutes
   - Action: Slack alert; restart event indexing worker container.
3. **High Subtask Failure Rate (MEDIUM):**
   - Condition: Error rate on DAG nodes exceeds 15% over a 15-minute sliding window.
   - Action: Flag agent for review, trigger fallback routing.
