# AgentChain: Production Architecture Specification

## 1. Executive Summary & Architecture Philosophy

AgentChain is engineered around three foundational architectural pillars:
1. **Decoupled Off-Chain Orchestration with On-Chain Settlement:** Heavy computation, LLM reasoning, vector search, and tool execution occur off-chain with deterministic state persistence. Financial escrow, milestone guarantees, reputation attestations, and deliverable hash commitments occur on-chain.
2. **Deterministic State-Graph Execution:** Workflows are orchestrated using LangGraph state machines, providing durable pause/resume capabilities, full step checkpointing, time-travel debugging, and multi-validator verification loops.
3. **Defense-in-Depth Security & Zero-Trust Agent Sandbox:** Agents and external workers run in isolated runtimes with strictly bounded tool permissions, comprehensive input sanitization, and cryptographic payload verification.

---

## 2. High-Level System Architecture Diagram

```
+---------------------------------------------------------------------------------------------------+
|                                        CLIENT TIER                                                |
|                                                                                                   |
|  +---------------------------------------------------------------------------------------------+  |
|  | Next.js App Router (TypeScript, Tailwind CSS, shadcn/ui, TanStack Query)                    |  |
|  |                                                                                             |  |
|  |  +--------------------+  +----------------------+  +---------------------+  +-------------+  |  |
|  |  | Task Creator & DAG |  | Live Graph Visualizer|  | Agent Marketplace & |  | Escrow & Tx |  |  |
|  |  | Prompt Studio      |  | (SSE/WebSocket)      |  | Operator Staking    |  | Dashboard   |  |  |
|  |  +--------------------+  +----------------------+  +---------------------+  +-------------+  |  |
|  |                                                                                             |  |
|  |  [ wagmi / viem Web3 Connector ] <---- EIP-4361 SIWE Session ----> [ HTTP-only JWT Cookie ]  |  |
|  +---------------------------------------------------------------------------------------------+  |
+---------------------------------------------------------------------------------------------------+
                                         |                      |
                    HTTPS REST / JSON    |                      | Direct EVM RPC / WebSockets
                    SSE / WebSocket Feed |                      | (viem contract reads/writes)
                                         v                      v
+-------------------------------------------------------+  +---------------------------------------+
|                 BACKEND SERVICE TIER                  |  |          BLOCKCHAIN TIER              |
|                                                       |  |       (EVM L2: Arbitrum/Base)         |
|  +-------------------------------------------------+  |  |                                       |
|  | FastAPI Gateway (Reverse Proxy / Auth Guard)    |  |  |  +---------------------------------+  |
|  | - SIWE Signature Verification & JWT Issuer      |  |  |  | AgentRegistry.sol               |  |
|  | - Rate Limiter & Granular RBAC Middleware       |  |  |  | - Operator Registration & Staking| |
|  | - Task & Deliverable REST Endpoints             |  |  |  | - Capability URIs & Status      |  |
|  | - SSE Event Broadcaster (Graph Execution Feed)  |  |  |  +---------------------------------+  |
|  +-------------------------------------------------+  |  |                  |                    |
|                          |                            |  |                  v                    |
|                          v                            |  |  +---------------------------------+  |
|  +-------------------------------------------------+  |  |  | Escrow.sol                      |  |
|  | LangGraph Orchestrator & Task Engine            |  |  |  | - USDC Multi-Agent Lock/Release |  |
|  |                                                 |  |  |  | - Result Hash Notarization      |  |
|  |  +---------------+       +-------------------+  |  |  |  | - Dispute Arbitration Locks     |  |
|  |  | AI Planner    | ----> | Subtask DAG       |  |  |  |  +---------------------------------+  |
|  |  | Decomposer    |       | Dependency Engine |  |  |  |                  |                    |
|  |  +---------------+       +-------------------+  |  |  |                  v                    |
|  |          |                         |            |  |  |  +---------------------------------+  |
|  |          v                         v            |  |  |  | Reputation.sol & Treasury.sol   |  |
|  |  +---------------+       +-------------------+  |  |  |  | - Performance Decay Scoring     |  |
|  |  | Agent Matcher | ----> | Verification Loop |  |  |  |  | - Protocol Fee Splitting (USDC) |  |
|  |  | & Dispatcher  |       | & Consensus Engine|  |  |  |  +---------------------------------+  |
|  |  +---------------+       +-------------------+  |  |  +---------------------------------------+
|  +-------------------------------------------------+  |                      ^
|                          |                            |                      |
|                          v                            |                      | Webhook / Event Indexer
|  +-------------------------------------------------+  |                      | (Alchemy / viem watcher)
|  | Agent Worker Runtime & Tooling Fabric           |  |                      |
|  |                                                 |  |  +------------------------------------+
|  |  +-------------------------------------------+  |  |  | Blockchain Event Listener /        |
|  |  | Modular LLM Provider Adapter              |  |  |  | Relayer Worker                     |
|  |  | (Anthropic / OpenAI / Gemini / vLLM)      |  |<-+--| - Ingests EscrowDeposited Events   |
|  |  +-------------------------------------------+  |  |  | - Submits Deliverable Result Hashes|
|  |  | Sandbox Tool Execution Layer              |  |  |  | - Dispatches Release Callbacks     |
|  |  | (Web Search / Code Sandbox / DB / Scraper)|  |  |  +------------------------------------+
|  |  +-------------------------------------------+  |
|  +-------------------------------------------------+
+---------------------------------------------------------------------------------------------------+
                                         |
     +-----------------------------------+-----------------------------------+
     |                                   |                                   |
     v                                   v                                   v
+------------------------+  +------------------------+  +-----------------------------------+
|      DATA LAYER        |  |    CACHE & PUBSUB      |  |         VECTOR & ARTIFACTS        |
|                        |  |                        |  |                                   |
| PostgreSQL 16 (Relational)| Redis 7 (In-Memory)    |  | Qdrant Cluster (Vector DB)       |
| - Users, Wallets, RBAC |  | - LangGraph Checkpoint |  | - Multi-tenant Task Collections   |
| - Tasks, DAG Steps     |  | - Distributed Locks    |  | - Semantic Artifact Search        |
| - Agent Registry Cache |  | - Celery/ARQ Job Queue |  | - Capability Embeddings           |
| - Audit Logs & Hashes  |  | - Live SSE Pub/Sub     |  | S3 / MinIO Object Storage         |
|                        |  |                        |  | - Raw Deliverables, Code, Logs    |
+------------------------+  +------------------------+  +-----------------------------------+
```

---

## 3. End-to-End Operational Lifecycle

### Step 1: User Authentication & Task Commissioning
1. User connects wallet (viem/wagmi) and requests a SIWE nonce from `POST /api/v1/auth/nonce`.
2. User signs the SIWE message; frontend submits signature to `POST /api/v1/auth/verify`.
3. Backend validates signature against the recovered EVM address, establishes user identity, and issues signed JWT session cookie.
4. Requester designs a task in the UI, specifying:
   - Objective & requirements description.
   - Deliverable expectations (e.g., Python code, financial audit report, research paper).
   - Maximum budget (e.g., 500 USDC) and completion deadline.

### Step 2: Task Planning & DAG Decomposition
1. The **AI Planner** parses the task brief via LangGraph.
2. The Planner generates a validated JSON execution DAG conforming to `TaskDAGPlan`:
   - Discrete subtasks with explicit dependencies (`depends_on`).
   - Expected input/output schemas.
   - Agent capability tags required (`code_generation`, `data_analysis`, `security_audit`).
   - Allocated sub-budget breakdown per node.
3. The requester inspects the visual DAG representation in the Next.js frontend and approves execution.

### Step 3: Blockchain Escrow Deposit
1. Frontend calls `Escrow.sol::depositForTask(taskId, totalUsdcAmount)` via viem.
2. Requester approves USDC transfer; smart contract locks funds in escrow.
3. The backend `Blockchain Event Listener` catches the `EscrowDeposited(taskId, requester, amount)` event on-chain.
4. The task status in PostgreSQL transitions from `PENDING_DEPOSIT` to `ACTIVE`.

### Step 4: Agent Selection & Dispatch
1. For each ready DAG node (all prerequisite nodes completed):
   - The **Agent Matcher** queries the registry (indexed from `AgentRegistry.sol` + PostgreSQL performance metrics).
   - Evaluates:
     - Capability match (exact tool & domain support).
     - Staked bond size (must exceed threshold for high-value nodes).
     - Reputation score (decay-weighted historical success rate).
     - Quoted cost per unit of work.
   - Assigns selected agent instance or routes to internal worker pool.

### Step 5: Sandboxed Tool Calling & Execution
1. The assigned agent executes within an isolated worker container.
2. Agents utilize the **Modular LLM Provider Abstraction** to call models with standardized schema enforcement.
3. If external tools are invoked (e.g., web search, code execution, SQL query):
   - Tool calls route through the **Sandbox Gateway** where inputs are sanitized, rate-limited, and audited.
4. Intermediate state checkpoints are written synchronously to Redis and PostgreSQL.
5. Large artifacts (source files, generated documents, models) are streamed directly to S3-compatible storage; metadata and SHA-256 hashes are recorded in PostgreSQL.

### Step 6: Multi-Stage Verification & Consensus
1. When a subtask deliverable is produced, it transitions to `VERIFYING`.
2. An independent **Validator Agent** inspects the deliverable against the subtask's validation rules:
   - Automated test runner executes code deliverables in a sandbox.
   - Syntax, schema, and hallucination checks run on analytical reports.
3. If verification passes:
   - The subtask is marked `COMPLETED`.
   - Dependent nodes in the DAG are unlocked for execution.
4. If verification fails:
   - Deliverable is rejected with feedback sent back to the agent for a maximum of $N$ retries.
   - If retries fail, an alternative agent is dynamically selected, or human-in-the-loop escalation is triggered.

### Step 7: Final Result Compilation & On-Chain Notarization
1. Once all DAG terminal nodes complete, the **Synthesizer Agent** aggregates all artifacts into a unified package.
2. The final bundle is hashed:
   $$\text{ResultHash} = \text{SHA256}(\text{CanonicalJSON}(\text{ArtifactMetadata}) \mathbin{\Vert} \text{RootPayloadHash})$$
3. Platform Relayer (or Requester) calls `Escrow.sol::completeTask(taskId, ResultHash)`.
4. Smart contract records the irreversible fingerprint on-chain.

### Step 8: Fund Release, Fee Splitting & Reputation Update
1. Upon notarization (and completion of any dispute window):
   - `Escrow.sol` distributes escrowed USDC:
     - Agent Operators receive their allocated subtask rewards.
     - Platform Treasury receives protocol fee (e.g., 2.5%).
     - Validator pool receives verification reward.
2. Smart contract emits `TaskSettled(taskId, totalPayout)`.
3. `Reputation.sol` automatically updates the on-chain scores for all participating agents:
   $$R_{t} = R_{t-1} \cdot e^{-\lambda \Delta t} + \Delta R_{\text{performance}}$$
4. The frontend displays the completed task, downloadable artifacts, verified hashes, and on-chain transaction receipt.

---

## 4. System Boundaries & Cross-Cutting Concerns

| Concern | Implementation Strategy |
| :--- | :--- |
| **State Consistency** | Distributed Redis locks (`Redlock`) guarantee single-writer concurrency across worker nodes. Postgres transactions wrap DAG state changes. |
| **Fault Tolerance** | LangGraph checkpointers persist state at every node. Worker crash recovers from last valid checkpoint without re-running completed steps. |
| **Secrets Management** | Zero raw keys stored in code or DB. LLM API keys and operator private keys managed via environment variables / AWS Secrets Manager / HashiCorp Vault. |
| **Real-Time Streaming** | Server-Sent Events (SSE) stream node status changes, live tool invocation logs, and partial token output directly to the Next.js UI. |

---

## 5. Phase 3: Agent Registry, Manifest & Protocol Layer

In Phase 3, the foundational Agent Registry, Agent Manifest schema, and Agent Execution Boundary are operational:

1. **Normalized Registry Schema**:
   - `agents`, `agent_versions`, `agent_capabilities`, `agent_tools`, and `agent_executions` provide normalized, relational persistence.
   - Rigorous lifecycle: `DRAFT` -> `VALIDATING` -> `PUBLISHED` -> `SUSPENDED` -> `DEPRECATED`.
2. **Machine-Readable Agent Manifest**:
   - Versioned manifests validated via Pydantic and JSON Schema Draft 2020-12.
   - Declares input/output schemas, runtime bounds (timeouts, memory limits), pricing, and capabilities.
3. **Agent Protocol (v1.0)**:
   - Transport-independent typed JSON message envelope (`protocol_version`, `message_id`, `message_type`, `timestamp`, `request_id`, `agent_id`, `execution_id`, `payload`).
   - 9 standard message types: `REGISTER`, `VALIDATE`, `PUBLISH`, `INVOKE`, `PROGRESS`, `RESULT`, `ERROR`, `CANCEL`, `HEARTBEAT`.
4. **Isolated Service Boundary & Cryptographic Hashing**:
   - Executes agents in-process behind a clean service boundary, preparing for containerized sandboxing in later phases.
   - RFC 8785 canonical JSON serialization generates deterministic SHA-256 hashes of inputs and outputs for non-repudiation.
5. **Reference Agent**:
   - `ResearchAgent` text summarization reference implementation proves the registry, manifest, SDK, and execution pipeline deterministically without external LLM dependencies.

---

## 6. Phase 4: Production Worker Runtime, Sandbox & Asynchronous Execution

Phase 4 transforms the execution model from API in-process synchronous invocation into an isolated, durable, asynchronous worker architecture:

```
Client ──> API Controller ──> PostgreSQL Execution Record (QUEUED)
                                 │
                                 ├──> Redis Stream (agent_executions)
                                 │       │
                                 │       ▼
                                 └──> Worker Service (claim_execution)
                                         │
                                         ▼
                                     SandboxRunner (Docker / Local Process)
                                         │
                                         ▼
                                     Agent Execution Boundary (SDK)
                                         │
                                         ├──> Canonical Output Hash (SHA-256)
                                         ├──> State Machine (SUCCEEDED / FAILED / TIMED_OUT)
                                         ├──> Audit Event Log
                                         └──> Redis SSE Stream & XACK
```

1. **API Execution Controller**:
   - Never executes agent code directly.
   - Enforces authentication, authorization, agent availability (`PUBLISHED`), input schema validation, payload quotas (1MB max input, 5MB max output).
   - Generates deterministic input SHA-256 hash (RFC 8785).
   - Enforces idempotency via `Idempotency-Key` header with database unique constraint.
   - Persists initial `QUEUED` record and dispatches durable job to Redis Streams.

2. **Durable Redis Streams Queue**:
   - Append-only file persistence (`appendonly yes`).
   - Consumer groups (`agentchain:workers`) deliver jobs to active workers.
   - Pending Entries List (PEL) tracks in-flight executions for at-least-once delivery.
   - Stale worker reclamation recovers orphaned executions if a worker crashes.

3. **Isolated Sandbox Boundary**:
   - `SandboxRunner` abstraction with `DockerSandboxRunner` and `LocalProcessSandboxRunner`.
   - Docker security controls: `privileged=False`, `cap_drop=["ALL"]`, `security_opt=["no-new-privileges:true"]`, `read_only=True`, `network_mode="none"`, `user="1000:1000"`, `tmpfs={"/tmp": ...}`.
   - Zero Docker socket exposure and zero host filesystem mounts.
   - Strict environment-variable allowlisting prevents leaking host credentials or API tokens.

4. **Server-Controlled State Machine**:
   - Valid state transitions: `QUEUED -> RUNNING -> SUCCEEDED | FAILED | TIMED_OUT | CANCELLED` and `RUNNING -> QUEUED` (transient retry).
   - Terminal states reject further transitions.

5. **Observability, Heartbeats & Retries**:
   - Workers publish periodic heartbeats (`worker_id`, `timestamp`, `status`, `active_execution_count`, `version`).
   - Operator endpoint `GET /api/v1/operator/workers` exposes cluster telemetry.
   - Failures partitioned into transient (retried with exponential backoff) and permanent (routed to dead-letter storage).
   - Prometheus metrics endpoint at `/metrics`.
   - Real-time Server-Sent Events (SSE) stream execution state changes to the web client.

## 7. Verified Reputation Foundation (Phase 6.4)

Phase 6.4 establishes an auditable reputation evidence layer based exclusively on cryptographically verified execution outcomes. Subjective star ratings, self-reported scores, and arbitrary marketplace rankings are strictly rejected.

```
Agent Execution (Terminal)
   │
   ├──> Canonical Result Hash (RFC 8785 + SHA-256)
   │
   ├──> ResultNotary Proof (ResultNotary.sol)
   │       │
   │       └──> On-Chain verifyProof(executionId, resultHash) == true
   │
   ├──> ReputationService (Enforces Idempotency & Deterministic Keys)
   │       │
   │       ├──> BlockchainTransactionIntent (registerReputationEvent)
   │       └──> BlockchainTxOutbox ──> Redis Stream ──> BlockchainRelayer
   │                                                         │
   │                                                         ▼
   │                                                 ReputationRegistry.sol
   │                                                         │
   │                                                         ▼
   ├──> EventIndexer (ReputationEventRegistered)
   │       │
   │       └──> 32-Block Canonical Confirmation Window
   │
   └──> State Projection (ReputationProfile)
           │
           └──> Aggregated Derived Counts (Canonical Events Only)
```

1. **Reputation Evidence vs. Scores**: The system records objective execution facts (`VERIFIED_SUCCESS`, `VERIFIED_FAILURE`, `VERIFIED_TIMEOUT`, `VERIFIED_CANCELLATION`), not arbitrary weights or scores.
2. **Cryptographic ResultNotary Anchor**: `VERIFIED_SUCCESS` strictly requires verified on-chain proof from `ResultNotary.sol`.
3. **No Mutable On-Chain Counters**: In adherence to reorg safety, `ReputationRegistry.sol` holds no aggregate counters and zero funds. Derived profiles are projected from canonical log evidence off-chain.
4. **Deterministic Idempotency**: Stable reputation keys prevent duplicate or conflicting reputation records across workers, retries, and blockchain re-minings.
5. **Reorg Invalidation**: Reorganized or orphaned blockchain events trigger automatic reprojection, immediately dropping affected reputation events from active profiles.



