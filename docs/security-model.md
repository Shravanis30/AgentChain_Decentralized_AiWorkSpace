# AgentChain: Security Threat Model & Defense Architecture

## 1. STRIDE Threat Analysis Matrix

| Threat Category | Target Component | Threat Vector | Impact | Mitigation Strategy |
| :--- | :--- | :--- | :--- | :--- |
| **Spoofing** | API & Auth | Stolen wallet session or fake agent signature | Unauthorized task dispatch or fund drainage | EIP-4361 SIWE signature verification; HTTP-only Strict SameSite JWT; ECDSA payload attestation signed by operator key. |
| **Tampering** | Deliverables & DAG | Attacker modifies subtask code deliverable or LLM output in transit | Poisoned workflow or malicious code execution | Cryptographic SHA-256 deliverable hashing; on-chain notarization; TLS 1.3 encryption across all internal services. |
| **Repudiation** | Escrow & Settlement | Operator denies completing task or Requester denies approving valid work | Dispute gridlock or unpaid agents | On-chain immutable hash commitment; signed worker execution receipts; immutable PostgreSQL audit logs. |
| **Information Disclosure** | Data Storage & LLM | Leaked proprietary task briefs or LLM API keys | Breach of confidentiality or API quota exhaustion | S3 encrypted at rest (AES-256); ephemeral presigned URLs (15m TTL); AWS KMS / HashiCorp Vault for API secrets; prompt PII sanitization. |
| **Denial of Service** | Orchestrator & LLM | Task flooding; recursive prompt loop draining budget | Platform outage or massive LLM billing surge | Per-node token & timeout bounds; upfront USDC escrow lock; Cloudflare DDoS protection; Redis token-bucket rate limiting. |
| **Elevation of Privilege** | Agent Tool Sandbox | Agent escapes tool execution sandbox to host OS | Host compromise or network pivot | Docker/gVisor container isolation; unprivileged user execution; read-only root filesystem; network egress allowlisting. |

---

## 2. Smart Contract Defense-in-Depth

1. **Reentrancy Protection:** All fund transfers in `Escrow.sol` and `Treasury.sol` strictly enforce OpenZeppelin's `ReentrancyGuard` and adhere to the **Checks-Effects-Interactions (CEI)** pattern.
2. **Safe ERC-20 Handling:** Integration with USDC uses `SafeERC20.safeTransfer` and `SafeERC20.safeTransferFrom` to prevent revert anomalies from non-standard ERC-20 return values.
3. **Pull over Push Payments:** For dispute refunds and operator payouts, funds can be credited to an internal balance pattern if direct transfer fails, preventing DoS by malicious contract recipients.
4. **Emergency Pausing & Timelocks:** Critical contract functions can be paused by multi-sig governance (`Pausable`). Fee parameter changes and treasury withdrawals enforce a 48-hour timelock.
5. **Static Analysis & Formal Verification:**
   - Pre-commit Slither static analysis and Aderyn vulnerability scanning.
   - Property-based fuzzing using Echidna and Foundry invariant tests (`testFuzz_`).

---

## 3. AI Safety & Tool Execution Guardrails

```
[ Inbound Prompt / Tool Input ]
             |
             v
+---------------------------------------+
| Layer 1: Prompt Injection Guardrail   |
| - Strips known jailbreak templates    |
| - Enforces structured JSON schemas    |
+---------------------------------------+
             |
             v
+---------------------------------------+
| Layer 2: Tool Execution Firewall      |
| - Validates argument bounds & paths   |
| - Blocks shell injection primitives   |
+---------------------------------------+
             |
             v
+---------------------------------------+
| Layer 3: Container Sandbox (gVisor)   |
| - Read-only root filesystem           |
| - Memory cap: 2GB, CPU quota: 1 core  |
| - Network egress: strictly allowlist  |
+---------------------------------------+
```

---

## 4. Agent Collusion & Sybil Defense

1. **Collateral Staking Hurdle:** Operators must stake significant USDC (e.g. 500+ USDC per registered agent) to qualify for task bidding. This imposes a high capital cost on Sybil attacks.
2. **Independent Validator Quorum:** Tasks are verified by randomly selected validator agents assigned using verifiable pseudo-randomness (commit-reveal or blockhash salt). An agent cannot validate its own operator's work.
3. **Slashing Mechanism:** Upon confirmed submission of malicious artifacts (exploits, fake completions, data poisoning), the operator's stake is slashed:
   - 50% burned.
   - 30% refunded to the victimized requester.
   - 20% rewarded to the detecting validator.

---

## 5. Secret Management & Key Architecture

- **No Hardcoded Credentials:** Zero secrets in Git or container images.
- **Relayer Private Keys:** Stored in AWS KMS or HashiCorp Vault. Transactions are signed via KMS HSM API calls without exposing raw private keys in memory.
- **LLM Provider API Keys:** Loaded exclusively through environment variables injected at container runtime by Kubernetes Secrets / Docker Compose secret files.

---

## 6. Identity, Wallet Authentication & Session Security (Phase 2)

### 6.1 Cryptographic Identity Verification (SIWE / EIP-4361)
- **Zero Frontend Trust:** The backend never trusts a wallet address passed in requests. All identity assertions require an active cryptographic session or valid EIP-4361 signature.
- **Single-Use Nonce Lifecycle:** Nonces are cryptographically generated (128-bit entropy), stored in PostgreSQL (`auth_nonces`), bound to the target wallet address with a 5-minute TTL, and atomically marked consumed upon verification. Replayed nonces are rejected.
- **Domain & Chain Binding:** Signatures are strictly bound to authorized domains (`SIWE_ALLOWED_DOMAINS`) and allowed chains (`[31337, 84532, 8453]`), mitigating phishing and cross-chain replay attacks.
- **EIP-55 Address Normalization:** All addresses are normalized to checksum format via `Web3.to_checksum_address` to eliminate casing vulnerabilities.

### 6.2 Session Security & Token Storage
- **Cryptographic Token Hashing:** Raw session tokens (`secrets.token_urlsafe(32)`) are hashed via SHA-256 before storage in PostgreSQL (`sessions.token_hash`). Database compromises do not expose usable credentials.
- **Cookie Security:** Session tokens are delivered via `HttpOnly`, `SameSite=Lax`, and `Secure` (in HTTPS) cookies. JavaScript cannot access tokens, eliminating XSS token exfiltration.
- **Session Revocation & Sliding Expiration:** Logout revokes the session in the database immediately (`revoked_at`). Sessions have a strict 7-day expiration with throttled sliding activity updates (`last_seen_at`).

### 6.3 Abuse & Brute-Force Rate Limiting
- Redis-backed sliding-window rate limiters protect sensitive authentication endpoints:
  - `/api/v1/auth/nonce`: 30 requests / minute per IP.
  - `/api/v1/auth/verify`: 15 requests / minute per IP.
- Excess requests receive HTTP 429 Too Many Requests with standard `Retry-After` headers.

### 6.4 Immutable Audit Logging Foundation
- Security-critical events (`AUTH_NONCE_CREATED`, `AUTH_SUCCESS`, `AUTH_FAILURE`, `LOGOUT`, `WALLET_ADDED`, `WALLET_REMOVED`, `ROLE_CHANGED`, `SESSION_REVOKED`) are recorded in an append-only `audit_logs` table with request metadata, sanitized payloads, and timestamps.
- See full threat model and implementation details in [docs/authentication.md](file:///Users/shravani/Desktop/AgentChain_AIWorkSpace/docs/authentication.md).

---

## 7. Agent Security, Manifest Validation & Execution Controls (Phase 3)

### 7.1 Strict Ownership & RBAC Enforcement
- Agent creation requires authorized developer roles (`DEVELOPER`, `AGENT_OPERATOR`, `ADMIN`).
- Agent mutation (`PATCH`, `DELETE`, `validate`, `publish`, `suspend`) strictly enforces server-side ownership. Developers cannot modify another developer's agent under any circumstances.
- Execution records (`GET /api/v1/executions/{id}`) are restricted to the requester, agent owner, platform verifier, or admin. Third parties receive HTTP 403 Forbidden.

### 7.2 Manifest & Schema Ingestion Guardrails
- Inbound manifests are validated via Pydantic and JSON Schema Draft 2020-12 meta-schemas.
- Root input and output schemas must be objects (`type: "object"`).
- Strict SemVer 2.0.0 regex validation enforces semantic release formatting.
- Tool configurations cannot inject shell primitives or arbitrary code execution paths.
- Input and output payloads are bounded by size limits (max 100,000 characters for text) to prevent Denial-of-Service attacks.

### 7.3 Cryptographic Execution Verification & Non-Repudiation
- Canonical serialization (RFC 8785) ensures deterministic key ordering and token whitespace elimination.
- Both input (`input_hash`) and output (`output_hash`) SHA-256 digests are computed and persisted in `agent_executions`.
- Neither party can repudiate the exact input requested or output returned.

### 7.4 Phase 3 Audit Event Types
- `AGENT_CREATED`, `AGENT_UPDATED`, `AGENT_VALIDATED`, `AGENT_PUBLISHED`, `AGENT_SUSPENDED`, `AGENT_EXECUTION_STARTED`, `AGENT_EXECUTION_SUCCEEDED`, `AGENT_EXECUTION_FAILED`.

---

## 8. Worker Runtime & Hardened SSE Authentication Security (Phase 4 & 4.1)

### 8.1 No Session Credentials in URLs
- Standard API endpoints and SSE streams reject long-lived session tokens in URL query strings (`?token=...` is disabled across all dependencies).
- Normal SSE authentication relies on `HttpOnly; Secure` cookies (`agentchain_session`) or `Authorization: Bearer <token>` headers.
- Browser EventSource limitations are supported strictly through **short-lived execution-scoped tickets**:
  - Authenticated clients request a ticket via `POST /api/v1/executions/{execution_id}/sse-token`.
  - The ticket is generated with 256-bit cryptographic entropy (`sse_<token>`), scoped exclusively to `(user_id, execution_id)`, has a 60-second TTL in Redis, and enforces single-use consumption.
  - SSE tickets cannot authenticate normal API endpoints (e.g. `/api/v1/auth/me`, `/api/v1/executions/{id}`) and cannot be used to stream any other execution.
  - Session tokens and SSE tickets are strictly excluded from server logs.

### 8.2 Execution Lease & Split-Brain Prevention
- Workers acquire server-side leases on `agent_executions`: `lease_owner`, `lease_acquired_at`, `lease_expires_at`.
- Stale workers that wake up after another worker has reclaimed execution cannot mutate the database or overwrite results.
- Terminal executions are immutable: duplicate deliveries cannot overwrite `SUCCEEDED`, `FAILED`, `TIMED_OUT`, or `CANCELLED` results.

### 8.3 Sandbox Isolation Boundaries
- `LocalProcessSandboxRunner`: Development and test fallback only.
- `DockerSandboxRunner`: Local integration and controlled container isolation with dropped capabilities (`cap_drop=["ALL"]`), read-only root filesystems, tmpfs workspace, disabled privileged mode, no host mounts, isolated networks (`network_mode="none"`), non-root execution (`1000:1000`), and strict environment allowlists.
- `gVisor/Kata + Kubernetes`: Planned production hostile-code isolation boundary (standard Docker alone does not provide hypervisor-grade hostile code multi-tenancy).

---

## 9. Verified Reputation Evidence Security Model (Phase 6.4)

### 9.1 Fail-Closed Cryptographic Evidence Boundary
1. **Rejection of Subjective Inputs**: Frontend users, developers, and autonomous agents are strictly prohibited from submitting arbitrary reputation ratings, star scores, or mutable points.
2. **ResultNotary Dependency**: `VERIFIED_SUCCESS` reputation events strictly require that `ResultNotary.sol` on-chain has an already confirmed proof where `verifyProof(executionId, resultHash) == true`.
3. **Immutability of Terminal Executions**: Once an execution reaches a terminal state, its inputs, outputs, and cryptographic hashes are strictly immutable. Even during blockchain deep reorgs, execution data is never mutated.

### 9.2 Smart Contract Authorization & Fundless Invariant
1. **Zero Fund Exposure**: `ReputationRegistry.sol` holds zero funds, has no transfer functions, and accepts no deposits.
2. **Oracle Role Restriction**: Calling `registerReputationEvent` requires `REPUTATION_ORACLE_ROLE`. Unauthorized EOAs or contracts revert.
3. **Replay & Conflicting Event Protection**: Each execution ID can only be registered once on-chain. Conflicting outcomes or parameter tampering revert with `AlreadyRegistered(executionId)`.

### 9.3 Reorg Safety Invariants
1. **No Mutable On-Chain Counters**: Authoritative reputation profiles are derived strictly off-chain by indexing canonical `ReputationEventRegistered` events with depth >= 32 blocks.
2. **Immediate Invalidation on Reorg**: If an event or its underlying ResultNotary proof is orphaned during a reorg, it is marked `REORGED` and excluded from profile calculations.

### 9.4 Base Mainnet Transaction Hard-Block
- Base Mainnet (Chain ID 8453) transaction submission remains strictly hard-blocked across the API, `ReputationService`, `BlockchainTransactionIntent`, and `BlockchainRelayer` (`allow_transactions = False`).



