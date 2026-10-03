# AgentChain Artifact System & Data Passing Specification

## 1. Overview & Objectives

In a multi-agent orchestration, tasks generate intermediate datasets, structured outputs, code snippets, and synthesis reports. 

Blindly copying massive JSON payloads across all downstream nodes risks:
- Excessive checkpoint database bloat
- Memory exhaustion in LangGraph and worker containers
- Loss of data lineage and cryptographic non-repudiation

The AgentChain **Artifact Model** provides typed, cryptographically verified, and provenance-tracked data references.

---

## 2. Artifact Provenance Schema

Every artifact record stored in PostgreSQL (`artifacts` table) satisfies the following schema:

```json
{
  "id": "art-018f3b20-1a2b-7c8d-9e0f-123456789abc",
  "orchestration_id": "orch-018f3b19-...",
  "task_id": "otask-018f3b19-...",
  "producer_agent_id": "agent-research",
  "producer_agent_version": "1.0.0",
  "execution_id": "exec-018f3b20-...",
  "artifact_key": "market_summary",
  "content_type": "application/json",
  "schema_version": "1.0.0",
  "sha256": "3a7b9c1d2e...",
  "size_bytes": 1420,
  "created_at": "2026-09-27T00:30:00Z"
}
```

### Provenance Tracking
Each artifact records:
1. `orchestration_id`: Originating workflow.
2. `task_id`: Exact DAG task that produced the artifact.
3. `producer_agent_id` + `producer_agent_version`: The immutable agent identity and semantic version that generated the payload.
4. `execution_id`: The sandboxed execution run that computed the output.
5. `sha256`: Hex-encoded SHA-256 digest calculated over the canonically sorted JSON (`json.dumps(obj, sort_keys=True, separators=(",", ":"))`).

---

## 3. Data Passing Rules

### A. Inline vs Referenced Payloads
- **Small Structured Data (< 64 KB)**:
  Small JSON objects (status summaries, parameters, metrics) can be passed directly inside downstream task `input_data`.
- **Intermediate Artifacts (64 KB - 1 MB)**:
  Recorded in the `artifacts` table and referenced by `artifact_id` or `artifact_key`. Downstream agents receive the artifact metadata reference.
- **Hard Size Limit**:
  `MAX_ARTIFACT_BYTES = 1,048,576` (1 MB). Payloads exceeding this limit are rejected by the output validation layer.

### B. Output Validation & Non-Repudiation
1. Worker completes execution and returns output dictionary.
2. PostgreSQL finalization computes canonical SHA-256 and compares it against the worker-reported `output_hash`.
3. The orchestrator validates that the output matches the agent's declared schema in `AgentManifest`.
4. Artifact records are created within the same database transaction that marks the DAG task as `SUCCEEDED`.
5. An artifact cannot be mutated or overwritten once committed.
