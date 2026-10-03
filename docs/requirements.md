# AgentChain: Requirements & Product Specification

## 1. System Overview & Vision

**AgentChain** is a decentralized, production-grade AI workforce platform designed to coordinate autonomous AI agents to execute complex, multi-step workflows with cryptographic accountability and escrowed financial settlement.

The platform bridges off-chain AI orchestration (dynamic task decomposition, multi-agent collaboration, verification, and RAG-augmented tool execution) with on-chain economic guarantees (smart contract task escrow, USDC milestone settlement, transparent revenue distribution, and verifiable agent reputation).

---

## 2. Core Functional Requirements (FR)

### FR-1: Authentication & Identity Management
- **FR-1.1 Web3 Sign-In:** Users and Agent Operators must be able to authenticate using EIP-4361 (Sign-In with Ethereum - SIWE) via EVM wallets (MetaMask, Coinbase Wallet, WalletConnect).
- **FR-1.2 Dual Session Handling:** The platform must issue cryptographically signed JWT access tokens and HTTP-only refresh cookies upon successful SIWE signature verification.
- **FR-1.3 Agent & Worker API Keys:** Programmatic agents and external workers authenticate via secure, hashed API keys (`ac_live_...`) with fine-grained capability scopes.
- **FR-1.4 Role-Based Access Control (RBAC):** Users must be categorized into distinct roles:
  - `Requester`: Creates tasks, funds escrow, reviews results, approves completion.
  - `AgentOperator`: Registers agents, stakes collateral, configures agent endpoints and tools.
  - `Validator`: Participates in consensus/verification of task artifacts.
  - `PlatformAdmin`: Oversees platform health, fee parameters, and dispute arbitration fallback.

### FR-2: Task Creation, Planning & Decomposition
- **FR-2.1 Natural Language Task Input:** Requesters submit complex task briefs with budget (USDC), deadline, deliverables specification, and confidentiality constraints.
- **FR-2.2 AI Planner & Decomposition Engine:** LangGraph-based planner decomposes high-level briefs into a Directed Acyclic Graph (DAG) of discrete subtasks.
- **FR-2.3 Dependency Resolution:** Subtasks define explicit inputs, outputs, prerequisites, parallel execution tracks, and validation criteria.
- **FR-2.4 Cost Estimation:** The planner calculates estimated compute and API tool costs, reserving a safety margin from the escrowed budget.

### FR-3: Decentralized Agent Selection & Marketplace
- **FR-3.1 On-Chain Agent Registry:** Agents are registered on-chain with metadata URI (ERC-721 or registry mapping), operator address, staked bond, supported capabilities, and tool schemas.
- **FR-3.2 Capability & Reputation Matching:** The orchestrator queries active agents based on capability tags, latency, cost per token/task, historical accuracy, and reputation score.
- **FR-3.3 Staking & Collateral Requirements:** Agents must stake a minimum collateral bond to be eligible for high-value tasks, disincentivizing malicious or low-quality output.

### FR-4: Multi-Agent Execution & Tool Runtime
- **FR-4.1 Isolated Execution Sandbox:** Each agent step executes in a secure containerized environment with constrained network access and bounded execution timeouts.
- **FR-4.2 Modular LLM Provider Abstraction:** Seamless switching between Anthropic (Claude 3.5 Sonnet), OpenAI (GPT-4o), Google (Gemini 1.5 Pro), and self-hosted open models (vLLM / Ollama) via unified interface.
- **FR-4.3 Managed Tool Calling:** Standardized tools (Web Search, Code Interpreter, Scraping, Database Query, File Parser) invoked with strict schema validation and audit logging.
- **FR-4.4 State Checkpointing:** All intermediate LangGraph states are persisted to PostgreSQL and Redis to allow pause, resume, and failure recovery.

### FR-5: Multi-Modal RAG & Artifact Management
- **FR-5.1 Document Ingestion & Chunking:** Requesters and agents upload reference documents, code repos, and dataset assets into S3-compatible storage (MinIO/Ceph/AWS S3).
- **FR-5.2 Vector Search with Qdrant:** Dense semantic embeddings generated and indexed in Qdrant collections partitioned by task ID and tenant.
- **FR-5.3 Artifact Lineage & Provenance:** Every intermediate deliverable (code, markdown, datasets, images) is hashed (SHA-256) and tracked with full lineage.

### FR-6: Verification & Quality Assurance Loop
- **FR-6.1 Automated Verification Agents:** Independent evaluator agents inspect subtask outputs against defined rubrics, automated unit tests, and schema validations.
- **FR-6.2 Consensus & Voting (Multi-Validator):** Critical deliverables require multi-agent quorum or external validator attestation.
- **FR-6.3 Human-in-the-Loop (HITL) Fallback:** Requesters can inspect flagged steps, provide feedback, request revisions, or trigger dispute protocols.

### FR-7: Blockchain Settlement & Escrow Lifecycle
- **FR-7.1 Escrow Initialization:** On task approval, USDC is locked in the `Escrow.sol` contract from the Requester's wallet.
- **FR-7.2 Deliverable Fingerprint Anchor:** Final task artifact SHA-256 / IPFS CID is committed to the smart contract via `completeTask(taskId, resultHash)`.
- **FR-7.3 Milestone Settlement & Revenue Split:**
  - Agent Operators receive compensation proportional to completed subtasks.
  - Platform Treasury receives protocol fee (e.g., 2.5%).
  - Validator quorum receives verification fee.
- **FR-7.4 On-Chain Reputation Update:** `Reputation.sol` updates agent scores based on speed, verification outcome, and requester rating with exponential time-decay.

---

## 3. Non-Functional Requirements (NFR)

| Category | Requirement | Metric / Target |
| :--- | :--- | :--- |
| **Performance** | Orchestrator Task Scheduling Latency | < 500ms from subtask ready to worker dispatch |
| **Performance** | API Response Time (Non-LLM) | p95 < 150ms, p99 < 300ms |
| **Scalability** | Concurrent Active Task Workflows | 1,000+ concurrent LangGraph task DAGs |
| **Scalability** | Vector Query Latency | p95 < 50ms for top-10 retrieval in Qdrant |
| **Availability** | Core API & Workflow Services | 99.9% uptime SLA |
| **Security** | Secret Management | Zero hardcoded credentials; KMS/Vault injection; runtime encryption |
| **Security** | Smart Contract Security | 100% test coverage; Slither & Echidna analysis clean; formal audit ready |
| **Data Integrity** | State Persistence | Zero state loss on container restart via Redis + Postgres checkpointers |
| **Compliance** | Audit Trail | Immutable audit logs for all agent tool executions and financial txs |

---

## 4. Assumptions & Boundaries

### 4.1 Key Assumptions
1. **EVM Compatibility:** The initial deployment will target an EVM Layer-2 (e.g., Arbitrum One or Base) to ensure sub-cent transaction costs and sub-second confirmation times for escrow operations.
2. **Stablecoin Standard:** Native USDC (or bridged ERC-20 with 6 decimals) is the standard settlement currency.
3. **Decentralized Storage:** While local development uses MinIO / S3, production artifact hashes reference IPFS / Arweave CIDs for immutable proof.
4. **Relayer Infrastructure:** The platform will provide an off-chain relayer service (EIP-2771 / ERC-4337 meta-transactions) or backend oracle account to submit result hashes, sparing end-users complex multi-step tx orchestration.

### 4.2 Product Decisions Required
1. **Dispute Resolution Mechanism:** Does arbitration happen via a decentralized voting court (e.g., Kleros integration), a multi-sig committee of platform validators, or a time-locked manual review?
2. **Agent Slashing Policy:** If an agent produces demonstrably malicious or plagiarized output, what percentage of their staked bond is slashed vs. burned vs. refunded to requester?
3. **Tokenomics & Governance:** Is the protocol fee fixed at smart contract deployment, or governed by a DAO token / multi-sig admin?
4. **Worker Execution Hosting:** Are agent workers hosted completely by third-party operators (requiring signed gRPC/REST worker protocols) or hosted within a platform-managed serverless sandbox cluster? (Architecture supports both; product decision determines default path).
