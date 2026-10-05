# AgentChain Settlement Security Specification (Phase 6.2B)

> **CRITICAL PRODUCTION MANDATE:**
> **Phase 6.2B does not enable Base Mainnet settlement.**
> Base Mainnet (Chain ID 8453) transactions are hard-blocked across all layers.

---

## 1. Security Architecture & Threat Model

The settlement boundary is the critical bridge connecting off-chain agent execution with on-chain financial movement. The core philosophy is **zero-trust, fail-closed, and authorization-driven custody isolation**.

```
[ Off-Chain Agent Execution / LangGraph ]
                   │
                   ▼ (No blockchain credentials or direct access)
[ Settlement Authorization Boundary ]
       │       │       │
       │       │       └───► Verified against Canonical Chain State
       │       │
       │       ▼ (PostgreSQL FOR UPDATE + Unique Idempotency Key)
       │  [ Durable Transaction Intent & Outbox ]
       │               │
       ▼               ▼ (Decoupled Redis Stream)
[ Relayer & Signer Boundary ] (Isolated Signing Credentials)
       │
       ▼ (EIP-1559 Structured Calldata Only)
[ On-Chain Escrow Smart Contract ]
```

---

## 2. Hardened Security Controls

### 2.1 No Arbitrary Calldata or Target Contracts
- The settlement engine maps actions strictly to internal function builders (`releaseEscrow`, `refundEscrow`, `resolveDispute`).
- Target contracts must match the configured trusted address for the chain.
- No user-supplied ABI, calldata, or target addresses are accepted by the API or Relayer.

### 2.2 Token & Chain Allowlisting
- Only configured chain IDs are supported (Anvil: `31337`, Base Sepolia: `84532`).
- Base Mainnet (`8453`) is explicitly rejected with `SettlementMainnetBlockedError`.
- Only the official Circle USDC token addresses on supported chains are valid.

### 2.3 Idempotency & Concurrency Protection
- Deterministic idempotency keys formatted as:
  `settlement:{chain_id}:{escrow_id}:{action}:{authorization_version}`
- Enforced via a database `UniqueConstraint("chain_id", "idempotency_key")`.
- Concurrency handled using PostgreSQL `SELECT ... FOR UPDATE` row-level locks, preventing double authorizations when concurrent requests arrive.

### 2.4 Escrow State & Confirmation Verification
Before authorization:
- Escrow must exist in local indexer state.
- Escrow must be backed by a **canonical** event (`is_canonical = True`).
- Escrow confirmation status must be `CONFIRMED` meeting depth requirements.
- Escrows in terminal states (`RELEASED`, `REFUNDED`, `RESOLVED`) are rejected immediately.
- Client address, developer address, token, and amount must match exact database records.

### 2.5 Role-Based Access Control (RBAC)
- `RELEASE`: Only authorized by the registered `client_address` or an arbitrator.
- `REFUND`: Only authorized by the `client_address` (if pre-lock or post-deadline) or the `developer` (forfeiture).
- `DISPUTE_RESOLVE_*`: Strictly authorized by a user with `ADMIN` or `ARBITRATOR` roles.

### 2.6 Step 10: Nonce Confusion & Mempool Safety
- Relayer distinguishes between `latest` (mined) nonce and `pending` (mempool) nonce.
- A higher `pending` nonce **never** causes an intent or settlement to be marked failed or dropped.
- Intents are only marked dropped if an on-chain receipt confirms nonce consumption by another transaction or definitive proof of cancellation exists.

### 2.7 Reorg Safety & Finality Protection
- Settlements require block confirmation depth (Anvil: 1, Sepolia: 3, Mainnet: 12) before reaching `CONFIRMED`.
- The maximum tracking window is strictly 32 blocks across all components (`max_reorg_depth = 32`).
- Shallow reorgs (<= 32 blocks): If an indexed event is reorged out (`is_canonical = False`), the settlement is demoted back to `SUBMITTED`/`BLOCKED` and logged with `SETTLEMENT_REORG_INVALIDATED`.
- Deep reorgs (> 32 blocks): System immediately halts automatic state mutation, marks the reorg `FAILED`, blocks affected settlements (`BLOCKED`), halts new authorizations on that chain, and requires manual operator recovery.

---

## 3. Key Isolation & Signer Boundary

- **No Private Keys in Storage**: Private keys are strictly forbidden from PostgreSQL, Redis, session state, audit logs, or API responses.
- **Relayer Isolation**: The API creates inert `BlockchainTransactionIntent` records. Only the background Relayer worker interacts with signing keys.
- **Local / Testnet Environment**: `LocalAccountSigner` is used for Anvil and Base Sepolia testnet only.
- **Production Boundary**: In production, signing will be delegated to:
  - AWS KMS / GCP Cloud KMS with EIP-1559 signing policy
  - HashiCorp Vault Transit engine
  - Dedicated MPC signer cluster (e.g., Fireblocks / Turnkey)
  - Hardware Security Modules (HSM)

---

## 4. Audit Logging & Non-Repudiation

All critical settlement lifecycle events write immutable records to `audit_logs`:
- `SETTLEMENT_CREATED`
- `SETTLEMENT_AUTHORIZED`
- `SETTLEMENT_BLOCKED`
- `SETTLEMENT_SUBMITTED`
- `SETTLEMENT_CONFIRMED`
- `SETTLEMENT_FAILED`
- `SETTLEMENT_CANCELLED`
- `SETTLEMENT_REORG_INVALIDATED`
- `SETTLEMENT_RECONCILED`

Each audit entry captures actor ID, IP address, user agent, timestamps, and contextual metadata without leaking credentials.
