# AgentChain Agent Registry Specification

## 1. Purpose

The **Agent Registry** serves as the single source of truth for agent identity, capability discovery, manifest versioning, and lifecycle management across the AgentChain ecosystem. It lays the architectural foundation for the decentralized AI marketplace and multi-agent orchestrator.

---

## 2. Agent Lifecycle

Every agent in the registry transitions through an explicit, server-validated state machine:

```
[ DRAFT ]
    │
    ▼ (validate)
[ VALIDATING ]
    │
    ▼ (publish)
[ PUBLISHED ] ◄────────────┐
    │                      │
    ▼ (suspend)            │ (reactivate / validate)
[ SUSPENDED ] ─────────────┘
    │
    ▼ (deprecate)
[ DEPRECATED ]
```

### Lifecycle Rules & Invariants:
1. **DRAFT**:
   - Newly created agents begin in `DRAFT`.
   - Manifest schemas, runtime configs, and tool definitions can be modified by the owner.
   - Hidden from public discovery.
   - **Cannot** be invoked or executed.
2. **VALIDATING**:
   - Transient or verified state triggered by `POST /api/v1/agents/{id}/validate`.
   - Validates semantic versioning (SemVer 2.0.0), JSON Schema Draft 2020-12 compliance, tool bounds, and pricing syntax.
   - If validation fails, errors are returned structured and status remains `DRAFT`.
3. **PUBLISHED**:
   - Validated agent promoted to `PUBLISHED` via `POST /api/v1/agents/{id}/publish`.
   - Becomes discoverable via `GET /api/v1/agents`.
   - Version manifest is locked and immutable.
   - **Only** `PUBLISHED` agents may be invoked via `POST /api/v1/agents/{id}/execute`.
4. **SUSPENDED**:
   - Suspended by the owner or platform administrators (`ADMIN`) via `POST /api/v1/agents/{id}/suspend`.
   - Hidden from active discovery and blocked from new executions.
   - May be reactivated upon inspection.
5. **DEPRECATED**:
   - Retired versions or superseded agents. Cannot be invoked for new executions; existing audit trails remain immutable.

---

## 3. Database Schema

The database model is fully normalized with PostgreSQL 16:

### `agents`
- `id`: UUID (Primary Key)
- `owner_user_id`: UUID (Foreign Key -> `users.id`)
- `name`: VARCHAR(255)
- `slug`: VARCHAR(128) (Unique, Indexed)
- `description`: TEXT (Nullable)
- `status`: VARCHAR(32) (Indexed, Default: 'DRAFT')
- `current_version_id`: UUID (Foreign Key -> `agent_versions.id`, Nullable)
- `created_at`: TIMESTAMPTZ
- `updated_at`: TIMESTAMPTZ
- `published_at`: TIMESTAMPTZ (Nullable)
- `onchain_agent_id`: BIGINT (Nullable, for Phase 4+ on-chain registry sync)
- `reputation_score`: NUMERIC(5,2) (Default: 100.00)
- `is_active`: BOOLEAN (Default: True)

### `agent_versions`
- `id`: UUID (Primary Key)
- `agent_id`: UUID (Foreign Key -> `agents.id`, Indexed)
- `version`: VARCHAR(64) (SemVer, Unique per agent_id)
- `manifest`: JSONB (Complete Agent Manifest)
- `input_schema`: JSONB (JSON Schema for inputs)
- `output_schema`: JSONB (JSON Schema for outputs)
- `runtime_config`: JSONB (Timeout, retries, memory limits)
- `pricing_config`: JSONB (Pricing model, rate, currency)
- `verification_config`: JSONB (Deterministic rules, verification criteria)
- `created_at`: TIMESTAMPTZ
- `published_at`: TIMESTAMPTZ (Nullable)

### `agent_capabilities`
- `id`: UUID (Primary Key)
- `agent_id`: UUID (Foreign Key -> `agents.id`, Indexed)
- `capability`: VARCHAR(128) (Indexed)
- `description`: TEXT (Nullable)

### `agent_tools`
- `id`: UUID (Primary Key)
- `agent_id`: UUID (Foreign Key -> `agents.id`, Indexed)
- `tool_name`: VARCHAR(128)
- `configuration`: JSONB
- `enabled`: BOOLEAN (Default: True)

---

## 4. Manifest Specification

```json
{
  "protocol_version": "1.0",
  "agent": {
    "name": "Research Agent",
    "version": "1.0.0",
    "slug": "research-agent"
  },
  "description": "Deterministic local research and text summarization agent",
  "capabilities": ["text_summarization"],
  "input_schema": {
    "type": "object",
    "properties": {
      "text": { "type": "string", "minLength": 1, "maxLength": 100000 }
    },
    "required": ["text"],
    "additionalProperties": false
  },
  "output_schema": {
    "type": "object",
    "properties": {
      "summary": { "type": "string" },
      "word_count": { "type": "integer", "minimum": 0 },
      "sentence_count": { "type": "integer", "minimum": 0 },
      "character_count": { "type": "integer", "minimum": 0 }
    },
    "required": ["summary", "word_count", "sentence_count", "character_count"],
    "additionalProperties": false
  },
  "tools": [],
  "runtime": {
    "timeout_seconds": 30,
    "max_retries": 1,
    "memory_mb": 256
  },
  "pricing": {
    "model": "per_execution",
    "amount": "0.50",
    "currency": "USDC"
  },
  "verification": {
    "type": "deterministic",
    "rules": { "deterministic_output": true }
  }
}
```

---

## 5. Role-Based Authorization & Ownership

| Action | Allowed Roles | Ownership Restriction |
|---|---|---|
| Register Agent (`POST /agents`) | `DEVELOPER`, `AGENT_OPERATOR`, `ADMIN` | Logged in user becomes `owner_user_id` |
| Update Agent (`PATCH /agents/{id}`) | `DEVELOPER`, `AGENT_OPERATOR`, `ADMIN` | Caller must be owner or `ADMIN` |
| Delete Agent (`DELETE /agents/{id}`) | `DEVELOPER`, `AGENT_OPERATOR`, `ADMIN` | Caller must be owner or `ADMIN` (draft only) |
| Validate Agent (`POST /agents/{id}/validate`) | `DEVELOPER`, `AGENT_OPERATOR`, `ADMIN` | Caller must be owner or `ADMIN` |
| Publish Agent (`POST /agents/{id}/publish`) | `DEVELOPER`, `AGENT_OPERATOR`, `ADMIN` | Caller must be owner or `ADMIN` |
| Suspend Agent (`POST /agents/{id}/suspend`) | `DEVELOPER`, `AGENT_OPERATOR`, `ADMIN` | Caller must be owner or `ADMIN` |
| Public Discovery (`GET /agents`) | Public (Any role / Unauthenticated) | Returns only `PUBLISHED` agents by default |
| Execute Agent (`POST /agents/{id}/execute`) | Any authenticated user (`CLIENT`+) | Target agent must be `PUBLISHED` |
| Read Execution (`GET /executions/{id}`) | Requester, Agent Owner, Verifier, Admin | Strict authorization enforcement |
