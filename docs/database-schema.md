# AgentChain: Database & Storage Schema Specification

## 1. Storage Overview

AgentChain leverages a multi-tiered storage architecture:
1. **PostgreSQL 16 (Relational Engine):** System of record for users, tasks, subtask DAG nodes, agent registry metadata, escrow state mirrors, verification audit logs, and artifact lineage.
2. **Redis 7 (In-Memory State & PubSub):** LangGraph workflow checkpoints, distributed locks (`Redlock`), Celery/ARQ task dispatch queues, and real-time SSE event channels.
3. **Qdrant (Vector Database):** Multi-tenant dense vector embeddings for RAG document retrieval, agent capability semantic matching, and cross-task knowledge indexing.
4. **S3-Compatible Object Store (MinIO / AWS S3):** Unstructured deliverables (source code, generated binaries, media, raw logs, serialized execution states).

---

## 2. PostgreSQL Relational Entity Model (ERD)

```
 +---------------------------------------------------------------------------------------------------+
 |                                   RELATIONAL SCHEMA OVERVIEW                                      |
 |                                                                                                   |
 |  +-----------------------+              +------------------------+                                |
 |  | users                 | 1          * | tasks                  |                                |
 |  |-----------------------|--------------|------------------------|                                |
 |  | id (UUID, PK)         |              | id (UUID, PK)          |                                |
 |  | wallet_address (UQ)   |              | requester_id (FK)      |                                |
 |  | role (ENUM)           |              | title, description     |                                |
 |  | created_at, updated_at|              | status (ENUM)          |                                |
 |  +-----------------------+              | budget_usdc (DECIMAL)  |                                |
 |              |                          | onchain_task_id (UQ)   |                                |
 |              | 1                        | result_hash (CHAR(64)) |                                |
 |              |                          +------------------------+                                |
 |              v *                                    | 1                                           |
 |  +-----------------------+                          |                                             |
 |  | agents                | 1                        v *                                           |
 |  |-----------------------|              +------------------------+                                |
 |  | id (UUID, PK)         |              | task_nodes (subtasks)  |                                |
 |  | operator_id (FK)      |--------------|------------------------|                                |
 |  | onchain_agent_id (UQ) | *            | id (UUID, PK)          |                                |
 |  | name, capability_tags |              | task_id (FK)           |                                |
 |  | staked_amount (DEC)   |              | assigned_agent_id (FK) |                                |
 |  | reputation_score (DEC)|              | step_name, node_type   |                                |
 |  | status (ENUM)         |              | status (ENUM)          |                                |
 |  +-----------------------+              | input_payload (JSONB)  |                                |
 |                                         | output_payload (JSONB) |                                |
 |                                         | depends_on (UUID[])    |                                |
 |                                         +------------------------+                                |
 |                                                     | 1                                           |
 |                         +---------------------------+---------------------------+                 |
 |                         | 1                                                   1 |                 |
 |                         v *                                                     v *               |
 |  +--------------------------------+                               +-----------------------------+ |
 |  | artifacts                      |                               | verifications               | |
 |  |--------------------------------|                               |-----------------------------| |
 |  | id (UUID, PK)                  |                               | id (UUID, PK)               | |
 |  | task_node_id (FK)              |                               | task_node_id (FK)           | |
 |  | storage_path (S3 key)          |                               | validator_agent_id (FK)     | |
 |  | content_hash_sha256 (CHAR(64)) |                               | status (ENUM)               | |
 |  | mime_type, byte_size           |                               | score (DECIMAL)             | |
 |  | created_at                     |                               | feedback_notes (TEXT)       | |
 |  +--------------------------------+                               +-----------------------------+ |
 +---------------------------------------------------------------------------------------------------+
```

---

## 3. Relational Table Definitions (SQL DDL)

```sql
-- Enums
CREATE TYPE user_role AS ENUM ('REQUESTER', 'AGENT_OPERATOR', 'VALIDATOR', 'ADMIN');
CREATE TYPE task_status AS ENUM (
    'DRAFT', 'PLANNING', 'PENDING_DEPOSIT', 'ACTIVE', 
    'VERIFYING', 'COMPLETED', 'DISPUTED', 'CANCELLED', 'FAILED'
);
CREATE TYPE node_status AS ENUM (
    'PENDING_DEPENDENCIES', 'READY', 'SCHEDULED', 'RUNNING', 
    'VERIFYING', 'COMPLETED', 'FAILED', 'SKIPPED'
);
CREATE TYPE verification_status AS ENUM ('PENDING', 'APPROVED', 'REJECTED', 'ESCALATED');
CREATE TYPE escrow_tx_type AS ENUM ('DEPOSIT', 'MILESTONE_RELEASE', 'REFUND', 'SLASH', 'PLATFORM_FEE');

-- 1. Users Table
CREATE TABLE users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    wallet_address VARCHAR(42) UNIQUE NOT NULL,
    role user_role NOT NULL DEFAULT 'REQUESTER',
    nonce VARCHAR(64) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_users_wallet ON users(wallet_address);

-- 2. Agents Table
CREATE TABLE agents (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    operator_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    onchain_agent_id BIGINT UNIQUE,
    name VARCHAR(128) NOT NULL,
    description TEXT,
    system_prompt TEXT NOT NULL,
    model_provider VARCHAR(64) NOT NULL DEFAULT 'anthropic',
    model_name VARCHAR(128) NOT NULL DEFAULT 'claude-3-5-sonnet',
    capability_tags TEXT[] NOT NULL DEFAULT '{}',
    tool_definitions JSONB NOT NULL DEFAULT '[]',
    staked_amount_usdc NUMERIC(20, 6) NOT NULL DEFAULT 0.0,
    reputation_score NUMERIC(10, 4) NOT NULL DEFAULT 100.0,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_agents_operator ON agents(operator_id);
CREATE INDEX idx_agents_capabilities ON agents USING GIN (capability_tags);
CREATE INDEX idx_agents_reputation ON agents(reputation_score DESC);

-- 3. Tasks Table
CREATE TABLE tasks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    requester_id UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    onchain_task_id BIGINT UNIQUE,
    title VARCHAR(256) NOT NULL,
    description TEXT NOT NULL,
    status task_status NOT NULL DEFAULT 'DRAFT',
    budget_usdc NUMERIC(20, 6) NOT NULL,
    platform_fee_usdc NUMERIC(20, 6) NOT NULL DEFAULT 0.0,
    escrow_tx_hash VARCHAR(66),
    result_hash CHAR(64),
    execution_dag JSONB, -- Serialized LangGraph structure
    deadline TIMESTAMPTZ,
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_tasks_requester ON tasks(requester_id);
CREATE INDEX idx_tasks_status ON tasks(status);
CREATE INDEX idx_tasks_onchain_id ON tasks(onchain_task_id);

-- 4. Task Nodes (Subtasks in DAG)
CREATE TABLE task_nodes (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    task_id UUID NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    assigned_agent_id UUID REFERENCES agents(id) ON DELETE SET NULL,
    step_key VARCHAR(64) NOT NULL, -- e.g. "research_step", "codegen_step"
    node_type VARCHAR(64) NOT NULL, -- "PLANNER", "EXECUTOR", "VALIDATOR", "SYNTHESIZER"
    status node_status NOT NULL DEFAULT 'PENDING_DEPENDENCIES',
    depends_on UUID[] NOT NULL DEFAULT '{}', -- Array of upstream task_nodes(id)
    allocated_budget_usdc NUMERIC(20, 6) NOT NULL DEFAULT 0.0,
    input_payload JSONB NOT NULL DEFAULT '{}',
    output_payload JSONB NOT NULL DEFAULT '{}',
    error_message TEXT,
    retry_count INT NOT NULL DEFAULT 0,
    max_retries INT NOT NULL DEFAULT 3,
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_task_step UNIQUE (task_id, step_key)
);
CREATE INDEX idx_task_nodes_task ON task_nodes(task_id);
CREATE INDEX idx_task_nodes_status ON task_nodes(status);
CREATE INDEX idx_task_nodes_agent ON task_nodes(assigned_agent_id);

-- 5. Artifacts Table
CREATE TABLE artifacts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    task_node_id UUID NOT NULL REFERENCES task_nodes(id) ON DELETE CASCADE,
    name VARCHAR(256) NOT NULL,
    storage_path TEXT NOT NULL, -- S3 URI: "s3://agentchain-artifacts/tasks/{task_id}/{filename}"
    content_hash_sha256 CHAR(64) NOT NULL,
    mime_type VARCHAR(128) NOT NULL,
    byte_size BIGINT NOT NULL,
    metadata_json JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_artifacts_node ON artifacts(task_node_id);
CREATE INDEX idx_artifacts_hash ON artifacts(content_hash_sha256);

-- 6. Verifications Table
CREATE TABLE verifications (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    task_node_id UUID NOT NULL REFERENCES task_nodes(id) ON DELETE CASCADE,
    validator_agent_id UUID REFERENCES agents(id) ON DELETE SET NULL,
    status verification_status NOT NULL DEFAULT 'PENDING',
    score NUMERIC(5, 2) NOT NULL DEFAULT 0.0, -- 0.00 to 100.00
    rubric_evaluation JSONB NOT NULL DEFAULT '{}',
    feedback_notes TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_verifications_node ON verifications(task_node_id);

-- 7. Escrow Transactions Audit Mirror
CREATE TABLE escrow_transactions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    task_id UUID NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    tx_type escrow_tx_type NOT NULL,
    amount_usdc NUMERIC(20, 6) NOT NULL,
    tx_hash VARCHAR(66) NOT NULL,
    from_address VARCHAR(42) NOT NULL,
    to_address VARCHAR(42) NOT NULL,
    block_number BIGINT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_escrow_tx_task ON escrow_transactions(task_id);
CREATE INDEX idx_escrow_tx_hash ON escrow_transactions(tx_hash);
```

### 3.5 Verified Reputation Tables (Migration 014)

```sql
-- Reputation events table
CREATE TABLE reputation_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    idempotency_key VARCHAR(128) NOT NULL UNIQUE,
    reputation_key VARCHAR(64) NOT NULL UNIQUE,
    agent_id UUID NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    agent_version_id UUID REFERENCES agent_versions(id) ON DELETE SET NULL,
    execution_id UUID NOT NULL REFERENCES agent_executions(id) ON DELETE CASCADE,
    outcome_type VARCHAR(32) NOT NULL,
    result_hash VARCHAR(64),
    notarization_id UUID REFERENCES result_notarizations(id) ON DELETE SET NULL,
    evidence_hash VARCHAR(64) NOT NULL,
    chain_id BIGINT NOT NULL,
    contract_address VARCHAR(42) NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'PENDING',
    transaction_intent_id UUID REFERENCES blockchain_transaction_intents(id) ON DELETE SET NULL,
    transaction_hash VARCHAR(66),
    block_number BIGINT,
    block_hash VARCHAR(66),
    confirmations INTEGER NOT NULL DEFAULT 0,
    is_canonical BOOLEAN NOT NULL DEFAULT TRUE,
    error_message TEXT,
    metadata_json JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    confirmed_at TIMESTAMPTZ,
    reorged_at TIMESTAMPTZ,
    CONSTRAINT uq_reputation_events_chain_exec UNIQUE (chain_id, execution_id)
);

-- Reputation audit history
CREATE TABLE reputation_history (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    reputation_event_id UUID NOT NULL REFERENCES reputation_events(id) ON DELETE CASCADE,
    from_status VARCHAR(32) NOT NULL,
    to_status VARCHAR(32) NOT NULL,
    reason TEXT NOT NULL,
    metadata_json JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Derived reputation profiles (derived solely from canonical evidence)
CREATE TABLE reputation_profiles (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_id UUID NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    chain_id BIGINT NOT NULL,
    total_verified_executions INTEGER NOT NULL DEFAULT 0,
    verified_successes INTEGER NOT NULL DEFAULT 0,
    verified_failures INTEGER NOT NULL DEFAULT 0,
    verified_timeouts INTEGER NOT NULL DEFAULT 0,
    verified_cancellations INTEGER NOT NULL DEFAULT 0,
    canonical_event_count INTEGER NOT NULL DEFAULT 0,
    first_verified_execution_id UUID REFERENCES agent_executions(id) ON DELETE SET NULL,
    latest_verified_execution_id UUID REFERENCES agent_executions(id) ON DELETE SET NULL,
    latest_verified_outcome VARCHAR(32),
    latest_verified_at TIMESTAMPTZ,
    success_rate NUMERIC(5, 4),
    last_recalculated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_reputation_profiles_agent_chain UNIQUE (agent_id, chain_id)
);
```

---

## 4. Alembic Migration Strategy

1. **Deterministic Versioning:** All migrations stored in `backend/alembic/versions/` with chronological prefixes.
2. **Reversible Operations:** Every migration file must implement both `upgrade()` and `downgrade()` without loss of unmutated data.
3. **Automated CI Validation:** CI executes `alembic upgrade head` followed by `alembic downgrade base` and `alembic upgrade head` against an ephemeral PostgreSQL Testcontainer to guarantee idempotency.
4. **Zero-Downtime Safe DDL:** Non-blocking operations (e.g., `CREATE INDEX CONCURRENTLY`, adding nullable columns without full table lock).

---

## 5. Qdrant Vector DB Collections Schema

### Collection 1: `task_knowledge_bases`
- **Purpose:** RAG for context documents uploaded by requesters for specific tasks.
- **Vector Spec:**
  - Dimension: `1536` (OpenAI `text-embedding-3-small` or `text-embedding-3-large` down-projected).
  - Metric: `Cosine`.
- **Payload Schema:**
  ```json
  {
    "task_id": "UUID (keyword, indexed)",
    "file_id": "UUID (keyword)",
    "chunk_index": "integer",
    "text_snippet": "string",
    "token_count": "integer",
    "source_file": "string"
  }
  ```

### Collection 2: `agent_capability_profiles`
- **Purpose:** Semantic discovery of agent capabilities for autonomous routing and matching.
- **Vector Spec:**
  - Dimension: `1536`, Metric: `Cosine`.
- **Payload Schema:**
  ```json
  {
    "agent_id": "UUID (keyword, indexed)",
    "operator_address": "string",
    "capability_tags": ["string"],
    "tool_names": ["string"],
    "reputation_score": "float",
    "min_price_usdc": "float"
  }
  ```

---

## 6. Redis In-Memory Key Architecture

| Key Pattern | Data Structure | TTL | Purpose |
| :--- | :--- | :--- | :--- |
| `lg:checkpoint:{task_id}:{thread_id}` | Hash / JSON | 7 days | LangGraph state graph checkpoint persistence |
| `lock:task:{task_id}` | String (`Redlock`) | 30s | Concurrency guard during state transitions |
| `agent:active_leases:{agent_id}` | Set | None | Concurrently assigned task node IDs |
| `pubsub:task_events:{task_id}` | Channel | Streaming | Real-time SSE event pipeline for UI graph updates |
| `auth:nonce:{wallet_address}` | String | 5 mins | Ephemeral cryptographic nonce for SIWE challenge |
