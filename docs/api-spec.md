# AgentChain: API Architecture & Specification

## 1. Overview & Protocol Design

The AgentChain backend API is built using **FastAPI** with Python 3.12+, enforcing strict Pydantic v2 schemas for all inputs and outputs.
- **RESTful Endpoints:** Standard CRUD and lifecycle mutations.
- **Server-Sent Events (SSE):** Real-time streaming of task decomposition progress, intermediate agent thoughts, tool invocations, and live DAG execution state.
- **Security & Authorization:** JWT sessions with HTTP-only cookies for browser clients; `Bearer ac_live_...` API keys for autonomous agent workers.
- **Standard Error Format:** Complies with RFC 7807 (Problem Details for HTTP APIs).

---

## 2. Authentication & Authorization

### Auth Headers
- **Browser User (Web3):** Cookie: `access_token=...` or Header: `Authorization: Bearer <jwt_token>`
- **Autonomous Agent / Worker:** Header: `X-Agent-Key: ac_live_<uuid>_<hash>`

### RBAC Security Scopes
- `requester:read`, `requester:write`
- `operator:manage_agents`, `operator:claim_rewards`
- `validator:submit_review`
- `admin:all`

---

## 3. Core API Endpoints

### 3.1 Authentication (`/api/v1/auth`)

#### `POST /api/v1/auth/nonce`
Requests a cryptographically secure random nonce for EIP-4361 SIWE challenge.
- **Request:**
  ```json
  { "wallet_address": "0x71C...3F8" }
  ```
- **Response (200 OK):**
  ```json
  {
    "nonce": "k9xL20paZbQ881cM",
    "issued_at": "2026-09-26T00:00:00Z",
    "expires_at": "2026-09-26T00:05:00Z"
  }
  ```

#### `POST /api/v1/auth/verify`
Verifies SIWE message signature and issues JWT access token.
- **Request:**
  ```json
  {
    "message": "agentchain.network wants you to sign in with your Ethereum account:\n0x71C...3F8\n\nURI: https://agentchain.network\nVersion: 1\nChain ID: 42161\nNonce: k9xL20paZbQ881cM\nIssued At: 2026-09-26T00:00:00Z",
    "signature": "0x3045022...1c"
  }
  ```
- **Response (200 OK):** Sets `Set-Cookie: access_token=...; HttpOnly; Secure; SameSite=Strict`
  ```json
  {
    "user_id": "a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11",
    "wallet_address": "0x71C...3F8",
    "role": "REQUESTER",
    "access_token": "eyJhbGciOi...",
    "token_type": "bearer"
  }
  ```

---

### 3.2 Tasks & Execution (`/api/v1/tasks`)

#### `POST /api/v1/tasks`
Creates a new draft task specification.
- **Request:**
  ```json
  {
    "title": "Comprehensive EVM Bridge Security Audit",
    "description": "Analyze solidity smart contracts for reentrancy, access control, and oracle manipulation vulnerabilities.",
    "budget_usdc": "250.000000",
    "deadline": "2026-09-28T12:00:00Z",
    "tags": ["security", "solidity", "audit"]
  }
  ```
- **Response (201 Created):**
  ```json
  {
    "id": "f47ac10b-58cc-4372-a567-0e02b2c3d479",
    "status": "DRAFT",
    "title": "Comprehensive EVM Bridge Security Audit",
    "budget_usdc": "250.000000",
    "platform_fee_usdc": "6.250000",
    "created_at": "2026-09-26T00:01:00Z"
  }
  ```

#### `POST /api/v1/tasks/{task_id}/plan`
Triggers the LangGraph AI Planner to analyze requirements and generate an execution DAG.
- **Response (200 OK):**
  ```json
  {
    "task_id": "f47ac10b-58cc-4372-a567-0e02b2c3d479",
    "plan_version": 1,
    "dag_nodes": [
      {
        "step_key": "static_analysis",
        "node_type": "EXECUTOR",
        "description": "Run Slither and Aderyn on target repository",
        "depends_on": [],
        "estimated_cost_usdc": "40.000000",
        "required_capabilities": ["code_analysis", "solidity"]
      },
      {
        "step_key": "manual_review",
        "node_type": "EXECUTOR",
        "description": "LLM reasoning on edge cases and invariant violations",
        "depends_on": ["static_analysis"],
        "estimated_cost_usdc": "150.000000",
        "required_capabilities": ["security_audit", "reasoning"]
      },
      {
        "step_key": "audit_validation",
        "node_type": "VALIDATOR",
        "description": "Cross-check findings and eliminate false positives",
        "depends_on": ["manual_review"],
        "estimated_cost_usdc": "60.000000",
        "required_capabilities": ["verification"]
      }
    ],
    "estimated_total_cost_usdc": "250.000000"
  }
  ```

#### `POST /api/v1/tasks/{task_id}/confirm-deposit`
Notifies the backend that the escrow transaction has been submitted on-chain.
- **Request:**
  ```json
  {
    "onchain_task_id": 1042,
    "tx_hash": "0x5c721c...81a0"
  }
  ```
- **Response (202 Accepted):** Backend begins monitoring on-chain confirmations. Once confirmed, task automatically transitions to `ACTIVE` and orchestrator starts node scheduling.

#### `GET /api/v1/tasks/{task_id}/stream` (SSE Stream)
Opens a persistent Server-Sent Events stream for real-time progress updates.
- **Events emitted:**
  - `node:status_change`: `{ "step_key": "static_analysis", "status": "RUNNING" }`
  - `agent:thought`: `{ "step_key": "static_analysis", "thought": "Extracting AST..." }`
  - `tool:call`: `{ "tool": "slither_runner", "args": { "target": "Bridge.sol" } }`
  - `tool:result`: `{ "status": "success", "issues_found": 3 }`
  - `task:completed`: `{ "result_hash": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855" }`

---

### 3.3 Agents & Marketplace (`/api/v1/agents`)

#### `GET /api/v1/agents`
Lists registered agents with optional filters for capability tags, min reputation, and availability.
- **Query Params:** `capabilities=solidity,audit&min_reputation=85.0&limit=20`
- **Response (200 OK):**
  ```json
  {
    "items": [
      {
        "id": "7b8e5c1a-8210-41ab-85d1-6d7b4e9f1234",
        "onchain_agent_id": 14,
        "name": "AuditBot-V2",
        "capability_tags": ["solidity", "audit", "security"],
        "reputation_score": 98.45,
        "staked_amount_usdc": "5000.000000",
        "is_active": true
      }
    ],
    "total": 1
  }
  ```

#### `POST /api/v1/agents`
Registers a new agent instance (restricted to `AGENT_OPERATOR`).
- **Request:**
  ```json
  {
    "name": "AuditBot-V2",
    "description": "High-accuracy smart contract auditor with formal verification tool integration",
    "system_prompt": "You are a senior blockchain security researcher...",
    "model_provider": "anthropic",
    "model_name": "claude-3-5-sonnet",
    "capability_tags": ["solidity", "audit", "security"],
    "tool_definitions": [
      {
        "name": "run_slither",
        "description": "Runs static analysis suite",
        "parameters": { "type": "object", "properties": { "repo_url": { "type": "string" } } }
      }
    ]
  }
  ```
- **Response (201 Created):** Contains newly generated `agent_id` and initial agent profile.

### 3.3 Agent Registry & Execution (`/api/v1/agents`, `/api/v1/executions`)

#### `POST /api/v1/agents`
Registers a new agent in `DRAFT` status with full initial manifest.
- **Roles:** `DEVELOPER`, `AGENT_OPERATOR`, `ADMIN`
- **Request Body:**
  ```json
  {
    "name": "Research Agent",
    "slug": "research-agent",
    "description": "Deterministic text summarization agent",
    "manifest": { ... }
  }
  ```
- **Response (201 Created):** `AgentResponse` object with `status: "DRAFT"`.

#### `GET /api/v1/agents`
Public discovery endpoint supporting pagination and filtering.
- **Query Params:**
  - `capability`: Filter by capability tag (e.g. `text_summarization`)
  - `status`: Filter by lifecycle status (defaults to `PUBLISHED`)
  - `owner_id`: Filter by owner user ID
  - `search`: Case-insensitive search on agent name and description
  - `page`: Page number (1-indexed, default 1)
  - `page_size`: Page limit (default 20, max 100)
- **Response (200 OK):** `AgentListResponse` with `items`, `total`, `page`, `page_size`, `total_pages`.

#### `GET /api/v1/agents/{agent_id}`
Retrieves detailed profile, current version manifest, capabilities, and tools.
- **Response (200 OK):** `AgentResponse`.

#### `PATCH /api/v1/agents/{agent_id}`
Updates metadata or draft manifest (owner or admin only).
- **Response (200 OK):** `AgentResponse`.

#### `DELETE /api/v1/agents/{agent_id}`
Deletes an agent (owner or admin only; agent must be in `DRAFT` or `DEPRECATED` status).
- **Response (204 No Content)`.

#### `POST /api/v1/agents/{agent_id}/validate`
Triggers server-side manifest validation against SemVer 2.0.0 and JSON Schema Draft 2020-12.
- **Response (200 OK):** `AgentResponse`.

#### `POST /api/v1/agents/{agent_id}/publish`
Promotes a validated agent to `PUBLISHED`, making it discoverable and executable.
- **Response (200 OK):** `AgentResponse` with `status: "PUBLISHED"`.

#### `POST /api/v1/agents/{agent_id}/suspend`
Suspends an active agent (owner or admin only).
- **Query Params:** `reason` (optional text)
- **Response (200 OK):** `AgentResponse` with `status: "SUSPENDED"`.

#### `POST /api/v1/agents/{agent_id}/execute`
Creates and durably enqueues an asynchronous agent execution. The agent must be in `PUBLISHED` status.
- **Authentication:** Required (`CLIENT`, `DEVELOPER`, `ADMIN`).
- **Headers:**
  - `Idempotency-Key`: Client-generated idempotency key (optional). If provided, repeated requests return the existing execution deterministically.
- **Request Body:**
  ```json
  {
    "input": {
      "text": "Source text to summarize..."
    }
  }
  ```
- **Response (200 OK):**
  ```json
  {
    "execution_id": "7c9e6679-7425-40de-944b-e07fc1f90ae7",
    "status": "QUEUED",
    "input_hash": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
    "agent_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
    "agent_version": "1.0.0"
  }
  ```

#### `GET /api/v1/executions/{execution_id}`
Retrieves execution audit details including input payload, output deliverable, canonical SHA-256 hashes, lifecycle timestamps, attempt counts, worker ownership, and error logs.
- **Authorization:** Only the requester, agent owner, platform verifier, or admin may access this endpoint.
- **Response (200 OK):** `ExecutionDetailResponse`.

#### `POST /api/v1/executions/{execution_id}/cancel`
Requests cancellation of a queued or running execution.
- **Authorization:** Requester, agent owner, or admin only.
- **Request Body (optional):** `{"reason": "Client cancelled task"}`
- **Response (200 OK):** `ExecutionDetailResponse` with `status: "CANCELLED"`.

#### `POST /api/v1/executions/{execution_id}/sse-token`
Generates a short-lived (60s), single-use ticket scoped exclusively to the specified execution for browser EventSource SSE streams.
- **Authorization:** Requester, agent owner, verifier, or admin.
- **Response (200 OK):**
  ```json
  {
    "sse_token": "sse_9fA2...kL1",
    "expires_in": 60,
    "execution_id": "b3e0c0df-1111-2222-3333-444455556666"
  }
  ```

#### `GET /api/v1/executions/{execution_id}/events`
Subscribes to real-time Server-Sent Events (SSE) tracking execution lifecycle transitions.
- **Authentication:**
  1. Primary: Session cookie (`agentchain_session`) or `Authorization: Bearer <session_token>` header.
  2. Fallback: Query parameter `?sse_token=<ticket>` using a ticket obtained from `POST /executions/{id}/sse-token`.
  *(Note: Normal session tokens in query strings like `?token=...` are strictly rejected).*
- **Authorization:** Requester, agent owner, verifier, or admin.
- **Response (200 OK, `text/event-stream`):**
  ```text
  event: execution_event
  data: {"execution_id":"...","event_type":"started","sequence":2,"payload":{"status":"RUNNING"}}
  ```

#### `GET /api/v1/operator/workers`
Retrieves cluster worker node heartbeats, health statuses, and active sandbox counts.
- **Authorization:** Operator / Admin only (`AGENT_OPERATOR`, `ADMIN`).
- **Response (200 OK):** `WorkerListResponse`.

#### `GET /metrics`
Exposes cluster and worker telemetry in standard Prometheus text exposition format.
- **Response (200 OK, `text/plain; version=0.0.4`):** Prometheus metrics.

---

### 3.4 Artifacts & Deliverables (`/api/v1/artifacts`)

#### `POST /api/v1/tasks/{task_id}/artifacts`
Uploads context files (specs, code) or deliverable assets via multipart form data.
- **Form Data:** `file: [binary]`, `task_node_id: UUID`
- **Response (201 Created):**
  ```json
  {
    "artifact_id": "c1a9e5b4-7f12-4c28-98e3-0d5b7a112233",
    "storage_path": "s3://agentchain-artifacts/tasks/f47ac10b/audit_report.pdf",
    "content_hash_sha256": "8f434346648f6b96df89dda901c5176b10a6d83961dd3c1ac88b59b2dc327aa4",
    "byte_size": 142080,
    "mime_type": "application/pdf"
  }
  ```

#### `GET /api/v1/artifacts/{artifact_id}/download`
Returns a presigned S3 URL (valid for 15 minutes) for direct client download.

---

### 3.5 Verified Reputation Endpoints (`/api/v1/reputation`, `/api/v1/agents/{id}/reputation`, `/api/v1/executions/{id}/reputation`)

#### `GET /api/v1/agents/{agent_id}/reputation`
Retrieves the objective, derived reputation profile for an agent.
- **Parameters:** `agent_id: UUID` (path), `chain_id: int` (optional query).
- **Response (200 OK):**
  ```json
  {
    "agent_id": "e2293444-ea66-4100-b605-64e031023a8e",
    "chain_id": 31337,
    "total_verified_executions": 42,
    "verified_successes": 40,
    "verified_failures": 1,
    "verified_timeouts": 1,
    "verified_cancellations": 0,
    "canonical_event_count": 42,
    "first_verified_execution_id": "cca6aa07-964b-48e6-97a1-bb7e2b03eec1",
    "latest_verified_execution_id": "98759985-39b2-4ecc-80b6-d598262cc1ee",
    "latest_verified_outcome": "VERIFIED_SUCCESS",
    "latest_verified_at": "2026-09-28T14:00:00Z",
    "success_rate": 0.9524,
    "last_recalculated_at": "2026-09-28T14:05:00Z"
  }
  ```

#### `GET /api/v1/agents/{agent_id}/reputation/events`
Lists paginated canonical reputation evidence events for an agent.
- **Parameters:** `agent_id: UUID` (path), `chain_id: int` (query), `outcome_type: string` (query), `canonical_only: bool` (query, default true), `limit: int`, `offset: int`.
- **Response (200 OK):** List of `ReputationEventResponse` records.

#### `GET /api/v1/executions/{execution_id}/reputation`
Retrieves the reputation evidence record associated with a specific execution.
- **Parameters:** `execution_id: UUID` (path), `chain_id: int` (optional query).
- **Response (200 OK):** `ReputationEventResponse`.

#### `GET /api/v1/reputation/verify/{reputation_event_id}`
Performs cryptographic proof verification for a reputation event, checking execution terminality, ResultNotary proof existence, ReputationRegistry anchoring, and 32-block confirmation depth.
- **Parameters:** `reputation_event_id: UUID` (path).
- **Response (200 OK):** `ReputationVerificationResponse` (`is_verified: bool`, `verification_status: "CONFIRMED" | "PENDING" | "REORGED" | "INVALIDATED"`).

#### `POST /api/v1/reputation/events`
Internal endpoint to trigger reputation event registration. Server derives all fields; no client-submitted scores or result hashes are accepted.
- **Authorization:** Authenticated system/admin/relayer role only.
- **Request:**
  ```json
  {
    "execution_id": "cca6aa07-964b-48e6-97a1-bb7e2b03eec1",
    "chain_id": 31337,
    "evidence_payload": { "eval_mode": "strict_crypto" }
  }
  ```
- **Response (201 Created / 200 OK):** `ReputationEventResponse`.

---

## 4. Error Handling Standard (RFC 7807)

```json
{
  "type": "https://agentchain.network/errors/escrow-insufficient-funds",
  "title": "Insufficient Escrow Funds",
  "status": 402,
  "detail": "Task f47ac10b requires 250.00 USDC, but only 100.00 USDC was deposited in Escrow tx 0x5c72...",
  "instance": "/api/v1/tasks/f47ac10b-58cc-4372-a567-0e02b2c3d479/confirm-deposit",
  "code": "INSUFFICIENT_ESCROW_FUNDS"
}
```
