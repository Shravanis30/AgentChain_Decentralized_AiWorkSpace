# AgentChain: Implementation Plan, Monorepo Structure & Roadmap

## 1. Monorepo Structure

AgentChain is organized as a clean, modular monorepo using standard tooling:

```
agentchain/
├── docs/                         # Architecture, specs, ADRs, schemas
│   ├── architecture.md
│   ├── requirements.md
│   ├── database-schema.md
│   ├── api-spec.md
│   ├── agent-protocol.md
│   ├── orchestration.md
│   ├── blockchain.md
│   ├── security-model.md
│   ├── observability.md
│   ├── deployment.md
│   └── implementation-plan.md
│
├── contracts/                    # Foundry / Solidity smart contracts
│   ├── foundry.toml
│   ├── src/
│   │   ├── AgentRegistry.sol
│   │   ├── Escrow.sol
│   │   ├── Marketplace.sol
│   │   ├── Reputation.sol
│   │   └── Treasury.sol
│   ├── test/
│   │   ├── AgentRegistry.t.sol
│   │   ├── Escrow.t.sol
│   │   └── Reputation.t.sol
│   └── script/
│       ├── Deploy.s.sol
│       └── MockUSDC.s.sol
│
├── backend/                      # Python FastAPI + LangGraph core
│   ├── pyproject.toml
│   ├── alembic.ini
│   ├── alembic/
│   │   └── versions/
│   └── app/
│       ├── main.py
│       ├── core/                 # Config, DB connection, Redis, KMS
│       ├── models/               # SQLAlchemy 2.0 ORM models
│       ├── schemas/              # Pydantic v2 validation schemas
│       ├── api/                  # FastAPI routers (v1)
│       │   ├── auth.py
│       │   ├── tasks.py
│       │   ├── agents.py
│       │   ├── artifacts.py
│       │   └── stream.py
│       ├── orchestrator/         # LangGraph state machine & planner
│       │   ├── graph.py
│       │   ├── state.py
│       │   ├── planner.py
│       │   ├── selector.py
│       │   └── nodes/
│       ├── providers/            # Modular LLM adapters (Anthropic/OpenAI/Gemini)
│       ├── rag/                  # Qdrant client & chunking engine
│       ├── workers/              # Celery/ARQ background runners
│       └── relayer/              # On-chain event indexer & tx submitter
│
├── packages/
│   └── agentchain-sdk/           # Python SDK for custom agent operators
│       ├── pyproject.toml
│       └── agentchain/
│
├── frontend/                     # Next.js web application
│   ├── package.json
│   ├── tsconfig.json
│   ├── tailwind.config.ts
│   ├── components.json           # shadcn/ui configuration
│   └── src/
│       ├── app/                  # Next.js App Router (pages & layouts)
│       ├── components/           # shadcn/ui & custom UI components
│       ├── hooks/                # TanStack Query & viem/wagmi hooks
│       ├── lib/                  # API client, SIWE auth, utils
│       └── types/                # TypeScript shared interfaces
│
├── infra/                        # Infrastructure as Code & Containers
│   ├── docker-compose.yml        # Complete local multi-container stack
│   ├── Dockerfile.backend
│   ├── Dockerfile.frontend
│   ├── Dockerfile.relayer
│   └── k8s/                      # Kubernetes manifests / Helm charts
│
└── .github/
    └── workflows/                # CI/CD pipelines
        ├── backend-ci.yml
        ├── frontend-ci.yml
        └── contracts-ci.yml
```

---

## 2. Testing Strategy

### 2.1 Smart Contracts (Foundry)
- **Unit Tests:** 100% branch and statement coverage across all core contracts (`forge test -vvv`).
- **Fuzzing & Invariants:** Stateless fuzzing on deposit/refund edge cases; stateful invariant testing with Echidna to verify solvency invariants:
  $$\text{Contract USDC Balance} \ge \sum \text{Locked Task Escrows} + \text{Treasury Surplus}$$
- **Static Analysis:** Pre-commit Slither analysis with custom triage whitelist.

### 2.2 Backend & Orchestrator (Pytest)
- **Unit Tests:** Mocked external LLM providers and tool invocations testing DAG validation, state transitions, and Pydantic parsing.
- **Integration Tests (Testcontainers):** Spin up real PostgreSQL, Redis, and Qdrant containers to validate Alembic migrations, LangGraph Redis checkpointers, and vector indexing.
- **Relayer Tests:** Local Anvil node testing end-to-end event subscription and tx submission.

### 2.3 Frontend & UI (Vitest & Playwright)
- **Component Tests:** Vitest + React Testing Library for UI components and form validation.
- **End-to-End Tests:** Playwright testing complete user journey: SIWE login $\to$ task creation $\to$ DAG review $\to$ mock escrow deposit $\to$ live SSE stream.

---

## 3. CI/CD Strategy

All code pushes trigger automated GitHub Actions workflows:
1. **`contracts-ci`**: Runs `forge build`, `forge test`, and `slither . --triage-mode`.
2. **`backend-ci`**: Runs `ruff check .`, `mypy --strict app/`, and `pytest --cov=app tests/`.
3. **`frontend-ci`**: Runs `pnpm lint`, `pnpm typecheck`, and `pnpm test`.
4. **Security Scans**: Dependabot, Trivy container vulnerability scanning, and Gitleaks secret detection.
5. **Deployment**: Automated builds pushed to Amazon ECR / Docker Hub and rolled out to staging environment.

---

## 4. Development Phases & Critical Path

```
[ Phase 0: Architecture & Planning ] <--- (CURRENT STATUS: COMPLETED)
                |
                v
[ Phase 1: Foundation & Data Layer ]
- Monorepo initialization
- Docker Compose dev stack (Postgres, Redis, Qdrant, MinIO, Anvil)
- Alembic database models & migrations
- Modular LLM provider abstraction & Qdrant RAG client
                |
                v
[ Phase 2: LangGraph Orchestrator & Agent Protocol ]
- AI Planner & DAG decomposition engine
- LangGraph state machine with Redis/Postgres checkpointers
- Verification & evaluator nodes
- Python Agent SDK (`agentchain-sdk`)
                |
                v
[ Phase 3: Smart Contracts & On-Chain Settlement ]
- Foundry project setup
- `AgentRegistry.sol`, `Escrow.sol`, `Reputation.sol`, `Treasury.sol`
- Comprehensive Foundry unit & invariant fuzz tests
- Slither static security audit
                |
                v
[ Phase 4: API Gateway, Relayer & Real-Time Streaming ]
- FastAPI v1 endpoints (Auth, Tasks, Agents, Artifacts)
- SIWE authentication & JWT session management
- Server-Sent Events (SSE) live execution stream
- Blockchain event listener & KMS relayer service
                |
                v
[ Phase 5: Frontend Application (Next.js) ]
- Next.js App Router + Tailwind CSS + shadcn/ui
- Web3 wallet connection (wagmi/viem) & SIWE login flow
- Task Creation & interactive DAG Visualizer
- Real-time execution log & deliverable inspector
- Agent Marketplace & Staking dashboard
                |
                v
[ Phase 6: Hardening, Observability & Testnet Launch ]
- OpenTelemetry collector & Grafana dashboards
- End-to-end integration tests on EVM Testnet (Base Sepolia)
- Penetration testing & external smart contract audit

[ Phase 6.3: Result Notarization & Cryptographic Proof ] - COMPLETED & VERIFIED
- ResultNotary.sol on-chain cryptographic anchor
- SHA-256 canonical hashing (RFC 8785)
- Automated verification APIs and reconciliation service

[ Phase 6.4: Verified Reputation Foundation ] - COMPLETED & VERIFIED
- ReputationRegistry.sol evidence registry (fundless, replay-protected)
- Objective outcome classification (VERIFIED_SUCCESS, VERIFIED_FAILURE, VERIFIED_TIMEOUT, VERIFIED_CANCELLATION)
- Cryptographic binding to ResultNotary.sol
- Reorg-safe off-chain profile derivation (zero mutable on-chain counters)
- Full Anvil E2E, Foundry property fuzzing (30,000 runs), Slither 0 findings, config drift verification
```

### Critical Path Analysis
- Phase 1 (Data & Storage) blocks Phase 2 (Orchestration).
- Phase 3 (Contracts) can proceed in parallel with Phase 2 (Orchestrator).
- Phase 4 (API & Relayer) requires both Phase 2 and Phase 3.
- Phase 5 (Frontend) depends on Phase 4 APIs.
- Phase 6 (Hardening) concludes the release.

---

## 5. Architectural Decision Records (ADRs) & Tradeoffs

### ADR-01: Dual Checkpointing (Redis + PostgreSQL)
- **Context:** LangGraph requires durable state checkpointing to enable step recovery, time-travel, and human-in-the-loop pauses.
- **Decision:** Use Redis for hot checkpoints (sub-ms graph transitions) and mirror committed node completions asynchronously to PostgreSQL.
- **Tradeoff:** Minimal added synchronization complexity vs. massive throughput gains over pure DB writes per graph step.

### ADR-02: EVM Layer-2 Settlement (Arbitrum / Base)
- **Context:** Ethereum Mainnet gas fees ($5–$30 per tx) make multi-step agent escrow and reputation updates economically unfeasible.
- **Decision:** Deploy on Base or Arbitrum One with native USDC settlement.
- **Tradeoff:** Requires bridging for Ethereum L1 users vs. enabling <$0.01 transaction costs.

### ADR-03: LangGraph as Core Workflow Engine
- **Context:** Evaluated LangChain vanilla chains, AutoGen, CrewAI, and LangGraph.
- **Decision:** Selected LangGraph due to first-class cyclic graph support, deterministic state serialization, built-in checkpointers, and production-ready human-in-the-loop interrupt mechanisms.

---

## 6. Assumptions & Critical Product Decisions

### 6.1 Explicit Assumptions
1. EVM L2 testnet (Base Sepolia / Arbitrum Sepolia) is suitable for Phase 3 and Phase 4 testing.
2. S3-compatible API (MinIO locally, AWS S3 / Cloudflare R2 in production) is standard for all binary artifact storage.
3. USDC is the sole currency for task funding, escrow, and revenue settlement.

### 6.2 Critical Decisions Requiring User / Product Confirmation
1. **Dispute Resolution Mechanism:** Should disputes be resolved via:
   - Option A: Platform Multi-Sig Committee (Fast, centralized MVP).
   - Option B: Decentralized Arbitration Court like Kleros (Fully decentralized, higher latency).
   - Option C: Validator Quorum Voting (Native multi-agent consensus).
2. **Worker Hosting Model:** Should the MVP focus on:
   - Option A: Platform-managed sandboxed workers (Faster time to market, lower friction for requesters).
   - Option B: Fully distributed external operator nodes connecting over ACAP protocol.
3. **Smart Contract Deployment Toolchain:** Confirm **Foundry** as the primary smart contract testing and deployment framework (recommended over Hardhat for speed, fuzzing, and Solidity scripting).
