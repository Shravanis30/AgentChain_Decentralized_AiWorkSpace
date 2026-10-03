# AgentChain Agent Execution Model & Service Boundary

## 1. Overview

The **AgentChain Execution Model** establishes an isolated, auditable execution pipeline connecting client invocation requests to autonomous agent runners.

In Phase 3, execution is proven with an in-process service boundary executing the reference `ResearchAgent`. The architecture strictly decouples the HTTP API layer from the execution runner so that containerized or distributed workers can be plugged in without altering caller contracts.

---

## 2. Execution Pipeline

```
Client (HTTP POST /agents/{id}/execute)
   │
   ▼
[ 1. Authorization & Role Check ] ── (Authenticated User)
   │
   ▼
[ 2. Lifecycle Status Gate ] ─────── (Must be PUBLISHED)
   │
   ▼
[ 3. Manifest Input Validation ] ─── (jsonschema Draft 2020-12)
   │
   ▼
[ 4. Canonical Input Hashing ] ───── (RFC 8785 Canonical JSON -> SHA-256)
   │
   ▼
[ 5. Persistence: QUEUED ] ───────── (agent_executions row created)
   │
   ▼
[ 6. Service Runner Boundary ] ───── (In-process execution runner)
   │    ├─ AgentContext propagation (execution_id, agent_id, timeout)
   │    └─ Timeout awareness & graceful error catching
   │
   ▼
[ 7. Manifest Output Validation ] ── (jsonschema Draft 2020-12)
   │
   ▼
[ 8. Canonical Output Hashing ] ──── (RFC 8785 Canonical JSON -> SHA-256)
   │
   ▼
[ 9. Persistence: SUCCEEDED ] ────── (Stores output_data, hashes, timing)
   │
   ▼
[ 10. Audit Logging ] ────────────── (AGENT_EXECUTION_STARTED, AGENT_EXECUTION_SUCCEEDED)
```

---

## 3. Cryptographic Hashing (RFC 8785)

For all executions, AgentChain computes deterministic, canonical SHA-256 hashes of input and output data.

### Canonicalization Rules:
- Keys are sorted lexicographically by Unicode code points.
- Whitespace between tokens is stripped.
- Floating point values follow IEEE 754 canonical formatting.
- Characters are UTF-8 encoded.

```python
import hashlib
import rfc8785

def compute_canonical_hash(data: dict) -> str:
    canonical_bytes = rfc8785.dumps(data)
    return hashlib.sha256(canonical_bytes).hexdigest()
```

These hashes provide non-repudiation and will serve as the immutable cryptographic anchor when payments and escrow settlement are committed to the blockchain in Phase 4.

---

## 4. Execution Persistence Schema

Table: `agent_executions`

| Column | Type | Constraints | Description |
|---|---|---|---|
| `id` | UUID | Primary Key | Execution identifier |
| `agent_id` | UUID | Foreign Key (`agents.id`) | Target agent |
| `agent_version_id` | UUID | Foreign Key (`agent_versions.id`) | Target version |
| `requested_by` | UUID | Foreign Key (`users.id`) | Requester user ID |
| `status` | VARCHAR(32) | Indexed | Status enum |
| `input_data` | JSONB | Not Null | Original input payload |
| `output_data` | JSONB | Nullable | Result payload (when succeeded) |
| `input_hash` | VARCHAR(64) | Not Null, Indexed | Canonical SHA-256 of input |
| `output_hash` | VARCHAR(64) | Nullable, Indexed | Canonical SHA-256 of output |
| `started_at` | TIMESTAMPTZ | Not Null | Start time |
| `completed_at` | TIMESTAMPTZ | Nullable | Completion time |
| `error_code` | VARCHAR(64) | Nullable | Machine-readable error code |
| `error_message` | TEXT | Nullable | Human-readable failure explanation |
| `metadata_json` | JSONB | Default: `{}` | Execution timing, runner details |

### Execution Statuses:
- `QUEUED`: Request validated and queued for dispatch.
- `RUNNING`: Actively executing within the worker runner boundary.
- `SUCCEEDED`: Finished successfully with valid output deliverable and computed hash.
- `FAILED`: Execution terminated with an error or schema violation.
- `CANCELLED`: Aborted by caller before completion.
- `TIMED_OUT`: Exceeded configured `timeout_seconds`.

---

## 5. Security & Access Control

Access to execution details (`GET /api/v1/executions/{id}`) is strictly restricted:
- **Requester**: The user who initiated the execution.
- **Agent Owner**: The developer or operator who owns the agent.
- **Verifier**: Platform verifier accounts inspecting results.
- **Admin**: Platform administrators.

Third-party unauthorized access attempts are rejected with `403 Forbidden`.
Raw secret keys or sensitive environment variables are strictly prevented from appearing in execution payloads or logs.
