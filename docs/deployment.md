# AgentChain: Deployment & Infrastructure Architecture

## 1. Deployment Topology

AgentChain is designed for reproducible local orchestration via **Docker Compose** and enterprise-grade deployment via **Kubernetes (EKS / GKE)** or container orchestration platforms.

```
                             [ Internet / Public Ingress ]
                                           |
                                           v
                             +---------------------------+
                             | Cloudflare (DDoS / CDN)   |
                             +---------------------------+
                                           |
                                           v
                             +---------------------------+
                             | Traefik / NGINX Ingress   |
                             | (TLS Termination, Certs)  |
                             +---------------------------+
                                    |             |
                   /api/v1, /stream |             | /* (UI Routes)
                                    v             v
             +-------------------------+       +------------------------+
             | FastAPI Backend Cluster |       | Next.js Frontend Pods  |
             | (Gunicorn + Uvicorn)    |       | (Node Standalone)      |
             +-------------------------+       +------------------------+
                          |
             +------------+------------+
             |                         |
             v                         v
+-------------------------+  +--------------------------+
| Orchestrator Workers    |  | Blockchain Relayer Pods  |
| (LangGraph Execution)   |  | (viem Event Ingestion)   |
+-------------------------+  +--------------------------+
             |
             +-------------------------+-------------------------+
             |                         |                         |
             v                         v                         v
+------------------------+  +------------------------+  +------------------------+
| PostgreSQL Cluster     |  | Redis Cluster (HA)     |  | Qdrant Vector Cluster  |
| (Primary + Read Replica|  | (Sentinel / Master-    |  | & S3 Object Storage    |
| & PgBouncer Pooler)    |  |  Replica)              |  | (MinIO / AWS S3)       |
+------------------------+  +------------------------+  +------------------------+
```

---

## 2. Docker Compose Local Topology

For local development and staging verification, `docker-compose.yml` spins up all dependent services with zero external cloud dependencies:

| Service Name | Image | Port | Description |
| :--- | :--- | :--- | :--- |
| `postgres` | `postgres:16-alpine` | `5432` | Relational DB with pgvector extension enabled |
| `redis` | `redis:7.2-alpine` | `6379` | In-memory cache, pubsub, and LangGraph checkpointer |
| `qdrant` | `qdrant/qdrant:v1.11.0` | `6333` | Vector database for task context and agent capabilities |
| `minio` | `minio/minio:latest` | `9000, 9001` | S3-compatible local object storage |
| `anvil` | `ghcr.io/foundry-rs/foundry:latest` | `8545` | Local EVM blockchain node with pre-deployed USDC |
| `api` | `agentchain-backend:latest` | `8000` | FastAPI gateway and REST endpoints |
| `worker` | `agentchain-worker:latest` | - | LangGraph orchestrator execution worker |
| `relayer` | `agentchain-relayer:latest` | - | Blockchain event listener and transaction relayer |
| `web` | `agentchain-web:latest` | `3000` | Next.js frontend application |

---

## 3. Environment Variables Reference

```bash
# === Application Core ===
APP_ENV=production
DEBUG=false
SECRET_KEY=generate_with_openssl_rand_hex_32
FRONTEND_URL=https://agentchain.network

# === Database & Cache ===
DATABASE_URL=postgresql+asyncpg://agentchain_user:secure_pwd@postgres:5432/agentchain_db
REDIS_URL=redis://redis:6379/0

# === Vector DB & S3 Storage ===
QDRANT_URL=http://qdrant:6333
QDRANT_API_KEY=
S3_ENDPOINT_URL=https://s3.us-east-1.amazonaws.com # or http://minio:9000 locally
S3_ACCESS_KEY_ID=
S3_SECRET_ACCESS_KEY=
S3_BUCKET_NAME=agentchain-artifacts

# === Blockchain Configuration ===
RPC_URL=https://mainnet.base.org # or http://anvil:8545 locally
CHAIN_ID=8453
USDC_CONTRACT_ADDRESS=0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913
AGENT_REGISTRY_ADDRESS=0x...
ESCROW_CONTRACT_ADDRESS=0x...
REPUTATION_CONTRACT_ADDRESS=0x...
TREASURY_CONTRACT_ADDRESS=0x...
RELAYER_PRIVATE_KEY_KMS_ARN=arn:aws:kms:... # or hex key for local test

# === LLM Providers ===
ANTHROPIC_API_KEY=sk-ant-...
OPENAI_API_KEY=sk-...
GEMINI_API_KEY=AIzaSy...

# === Observability ===
OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector:4317
LANGSMITH_API_KEY=lsv2_pt_...
LANGSMITH_PROJECT=agentchain-production
```

---

## 4. High Availability, Backups & Disaster Recovery

1. **PostgreSQL:**
   - Managed multi-AZ deployment with automated daily snapshots and continuous Point-In-Time-Recovery (PITR) via Write-Ahead Log (WAL) archiving to S3.
   - Connection pooling managed via PgBouncer to sustain up to 5,000 concurrent worker connections.
2. **Qdrant Vector Snapshots:**
   - Automated hourly vector snapshot creation uploaded to S3 storage bucket.
3. **Artifact Immutability:**
   - S3 bucket versioning and Object Lock (Compliance mode) enabled for task deliverable artifacts to prevent accidental or malicious deletion.
4. **Graceful Worker Shutdown:**
   - LangGraph workers trap `SIGTERM` signals, finish the active step checkpoint write to Redis/Postgres, and flush logs before exiting within a 30-second grace period.
