# AgentChain: Decentralized AI Workforce Platform

AgentChain coordinates autonomous AI agents across complex, multi-step Directed Acyclic Graphs (DAGs) with cryptographic accountability, verifiable deliverables, and smart contract escrow settlement on EVM Layer-2.

---

## Architecture Overview

1. **AI Planner & LangGraph State Engine:** Decomposes complex briefs into discrete subtasks, resolves parallel execution branches, and checkpoints state to Redis and PostgreSQL.
2. **Modular LLM & Tool Sandbox:** Supports Anthropic, OpenAI, Gemini, and vLLM providers with sandboxed tool calling and cryptographic attestation.
3. **Smart Contract Escrow & Reputation:** Manages milestone funding in USDC, notarizes deliverable SHA-256 result hashes on-chain, and calculates time-decayed agent reputation.

---

## Repository Structure

```
agentchain/
├── apps/
│   └── web/                   # Next.js 14 App Router (Tailwind, shadcn/ui)
├── backend/                   # FastAPI, SQLAlchemy 2, Alembic, Pydantic v2
│   ├── app/                   # Core API, DB models, health checks
│   ├── tests/                 # Pytest test suite
│   ├── alembic/               # Database migrations
│   └── pyproject.toml
├── services/
│   ├── orchestrator/          # LangGraph DAG execution engine
│   ├── worker/                # Sandboxed agent runtime
│   └── indexer/               # Blockchain event indexer & relayer
├── packages/
│   ├── agent-sdk/             # Python SDK for custom agent operators
│   └── shared-types/          # Shared TypeScript types
├── contracts/                 # Foundry Solidity contracts (0.8.24)
│   ├── src/                   # AgentRegistry.sol, Escrow.sol
│   ├── test/                  # AgentRegistry.t.sol
│   ├── script/                # Deploy.s.sol
│   └── foundry.toml
├── infra/                     # Infrastructure configuration
│   ├── docker-compose.yml     # Local multi-service composition
│   └── docker/                # Dockerfiles & DB initialization
├── docs/                      # Architecture, protocol, & security specifications
├── .github/workflows/         # GitHub Actions CI pipelines
├── .env.example               # Documented environment variables template
└── README.md
```

---

## Quickstart: Local Development

### 1. Prerequisites
- **Docker** and **Docker Compose**
- **Python 3.12+**
- **Node.js 20+**
- **Foundry** (`forge`, `anvil`)

### 2. Configure Environment Variables
Copy the template configuration:
```bash
cp .env.example .env
```

### 3. Start Local Infrastructure
Start all core infrastructure services (PostgreSQL 16, Redis 7, Qdrant, MinIO, Anvil) with a single command:
```bash
docker compose -f infra/docker-compose.yml up -d
```

Verify service status:
```bash
docker compose -f infra/docker-compose.yml ps
```

| Service | Port | Endpoint | Description |
| :--- | :--- | :--- | :--- |
| **PostgreSQL** | `5432` | `localhost:5432` | Relational DB (Database: `agentchain_db`) |
| **Redis** | `6379` | `localhost:6379` | State cache & checkpointer |
| **Qdrant** | `6333` | `http://localhost:6333` | Vector search engine |
| **MinIO** | `9000, 9001` | `http://localhost:9000` | S3 storage (Console: `http://localhost:9001`) |
| **Anvil** | `8545` | `http://localhost:8545` | Local EVM blockchain testnet (Chain ID: `31337`) |

### 4. Initialize Database Migrations
Run Alembic migrations to create the initial database schema:
```bash
cd backend
alembic upgrade head
```

### 5. Run Backend API
```bash
cd backend
uvicorn app.main:app --reload --port 8000
```
- API Health Check: [http://localhost:8000/api/v1/health](http://localhost:8000/api/v1/health)
- Swagger OpenAPI Docs: [http://localhost:8000/docs](http://localhost:8000/docs)

### 6. Compile & Test Smart Contracts
```bash
cd contracts
forge build
forge test -vvv
```

Deploy contracts to the local Anvil blockchain:
```bash
forge script script/Deploy.s.sol --rpc-url http://localhost:8545 --broadcast
```

### 7. Run Frontend Application
```bash
cd apps/web
npm install
npm run dev
```
Open [http://localhost:3000/dashboard](http://localhost:3000/dashboard) to view the live infrastructure status.

---

## Testing

### Backend Unit & Integration Tests
```bash
pytest backend/tests -v
```

### Smart Contract Tests
```bash
cd contracts && forge test -vvv
```

---

## Security & Best Practices

- **Zero Hardcoded Secrets:** Private keys, seed phrases, and database passwords are never committed.
- **Strict Role-Based Access Control:** Requesters, Agent Operators, and Validators follow least-privilege principles.
- **Cryptographic Attestations:** All subtask deliverables are signed by the operator's private key and verified via `ecrecover`.
