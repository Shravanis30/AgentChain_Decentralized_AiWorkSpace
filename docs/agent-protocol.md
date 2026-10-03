# AgentChain Agent Protocol Specification (v1.0)

## 1. Overview

The **AgentChain Agent Protocol** establishes a standardized, transport-independent message envelope and communication pattern for registering, validating, publishing, invoking, and auditing autonomous AI agents within the AgentChain decentralized platform.

The protocol ensures:
1. **Determinism & Reproducibility**: Structured schemas with strict JSON Schema Draft 2020-12 validation.
2. **Cryptographic Verifiability**: Canonical serialization (RFC 8785) producing deterministic SHA-256 hashes of input payloads and output deliverables.
3. **Transport Independence**: Designed to operate cleanly over HTTP (Request/Response & SSE) and easily extensible to WebSockets or decentralized libp2p pubsub channels.
4. **Execution Auditability**: Complete end-to-end provenance linking caller identity, agent version, input hash, output hash, timing, and lifecycle transitions.

---

## 2. Protocol Message Envelope

All protocol interactions adhere to a uniform, typed JSON envelope:

```json
{
  "protocol_version": "1.0",
  "message_id": "550e8400-e29b-41d4-a716-446655440000",
  "message_type": "INVOKE",
  "timestamp": "2026-09-27T00:00:00Z",
  "request_id": "req-98765",
  "agent_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
  "execution_id": "7c9e6679-7425-40de-944b-e07fc1f90ae7",
  "payload": {}
}
```

### Envelope Fields

| Field | Type | Required | Description |
|---|---|---|---|
| `protocol_version` | `string` | Yes | Protocol version string, currently `"1.0"`. |
| `message_id` | `UUID (string)` | Yes | Globally unique message ID for deduplication and tracing. |
| `message_type` | `string (enum)` | Yes | One of the 9 defined protocol message types. |
| `timestamp` | `ISO 8601 UTC` | Yes | Generation timestamp in UTC. |
| `request_id` | `string` | No | Client or gateway request correlation ID. |
| `agent_id` | `UUID (string)` | No | Target agent UUID (required for agent-scoped messages). |
| `execution_id` | `UUID (string)` | No | Unique execution run ID. |
| `payload` | `object` | Yes | Type-specific message payload dictionary. |

---

## 3. Protocol Message Types & Payloads

The protocol explicitly defines 9 message types:

### 3.1. `REGISTER`
Sent by a developer or operator to create or register an agent and its initial version draft.

**Payload:**
```json
{
  "name": "Research Agent",
  "slug": "research-agent",
  "description": "Deterministic text summarization and extraction",
  "manifest": { ... }
}
```

### 3.2. `VALIDATE`
Initiates server-side validation of the agent manifest (SemVer format, JSON Schema syntax, tool permissions, and runtime constraints).

**Payload:**
```json
{
  "agent_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
  "version": "1.0.0"
}
```

### 3.3. `PUBLISH`
Promotes a validated agent from `DRAFT` or `VALIDATING` to `PUBLISHED`, activating it in the public registry for execution.

**Payload:**
```json
{
  "agent_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
  "version": "1.0.0"
}
```

### 3.4. `INVOKE`
Dispatched by a client or orchestration workflow to request execution of a published agent.

**Payload:**
```json
{
  "input": {
    "text": "AgentChain is a decentralized AI workforce platform..."
  },
  "timeout_seconds": 300
}
```

### 3.5. `PROGRESS`
Emitted by the execution runner (via Server-Sent Events or WebSocket) during execution to stream intermediate status, milestone steps, or progress percentage.

**Payload:**
```json
{
  "stage": "parsing_input",
  "progress_pct": 25.0,
  "message": "Extracted 14 sentences for processing"
}
```

### 3.6. `RESULT`
Returned upon successful execution completion, carrying the validated output deliverable and canonical hashes.

**Payload:**
```json
{
  "status": "SUCCEEDED",
  "output": {
    "summary": "AgentChain is a decentralized AI workforce platform.",
    "word_count": 8,
    "sentence_count": 1,
    "character_count": 52
  },
  "input_hash": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
  "output_hash": "2c26b46b68ffc68ff99b453c1d30413413422d706483bfa0f98a5e886266e7ae",
  "execution_time_ms": 42
}
```

### 3.7. `ERROR`
Returned when execution fails, times out, or encounters schema validation errors.

**Payload:**
```json
{
  "error_code": "INPUT_VALIDATION_ERROR",
  "error_message": "'text' is a required property",
  "details": {
    "validator": "required",
    "missing": ["text"]
  }
}
```

### 3.8. `CANCEL`
Requests early cancellation of an active or queued execution.

**Payload:**
```json
{
  "reason": "Caller aborted task"
}
```

### 3.9. `HEARTBEAT`
Periodically exchanged between the platform and worker runners to guarantee liveness and detect stalled executions.

**Payload:**
```json
{
  "worker_id": "worker-local-01",
  "active_executions": 1,
  "load_factor": 0.15
}
```

---

## 4. Transport Bindings

### 4.1. HTTP Request / Response (Primary Phase 3)
- Execution trigger: `POST /api/v1/agents/{agent_id}/execute`
  - Headers: `Authorization: Bearer <token>`, `Content-Type: application/json`
  - Body: `{ "input": { ... } }`
  - Response: HTTP 200/202 with `ExecutionSubmitResponse`.
- Execution retrieval: `GET /api/v1/executions/{execution_id}`
  - Headers: `Authorization: Bearer <token>`
  - Response: HTTP 200 with full execution details, inputs, outputs, and canonical hashes.

### 4.2. Server-Sent Events (SSE) (Progress Streaming)
- Endpoint: `GET /api/v1/executions/{execution_id}/events`
- Event stream format:
  ```http
  event: progress
  data: {"message_type": "PROGRESS", "payload": {"progress_pct": 50, "stage": "summarizing"}}

  event: result
  data: {"message_type": "RESULT", "payload": {"status": "SUCCEEDED", ...}}
  ```

---

## 5. Security & Canonical Hashing

1. **RFC 8785 Canonicalization**: Prior to hashing, all JSON inputs and outputs are normalized using RFC 8785 (keys alphabetically sorted, whitespace stripped, IEEE 754 floats standard formatted).
2. **SHA-256 Digest**: `SHA-256(canonical_bytes)` produces an immutable 64-character hexadecimal digest stored with the execution record.
3. **No Shell Ingestion**: Manifests, tool configs, and inputs are strictly parsed data structures; arbitrary shell execution is prohibited.
