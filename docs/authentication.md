# AgentChain — Phase 2 Authentication & Identity Architecture

This document specifies the production identity foundation, Sign-In with Ethereum (EIP-4361) flow, cryptographic session management, role-based authorization (RBAC), and security controls implemented in AgentChain Phase 2.

---

## 1. Architectural Principles

1. **Zero-Trust for Frontend Wallet Claims:** The backend never trusts a wallet address supplied directly by the frontend. Every identity assertion requires cryptographic proof of private key ownership via EIP-4361.
2. **Server-Side Controlled Nonces:** Nonces are cryptographically generated (128-bit entropy), stored in PostgreSQL with a 5-minute TTL, and strictly marked consumed in the verification transaction to prevent replay attacks.
3. **Defense-in-Depth Cryptographic Verification:** Dual-layer signature validation combining the official `siwe` library with explicit EC public key recovery via `eth_account` / `web3.py`.
4. **Hashed Session Tokens:** Raw session secrets are never stored in the database. Only SHA-256 hashes of high-entropy tokens are persisted.
5. **Secure Browser Storage:** Authentication sessions rely exclusively on `HttpOnly`, `SameSite=Lax`, and `Secure` (in HTTPS) cookies. No authentication tokens are ever stored in `localStorage` or `sessionStorage`.
6. **Immutable Audit Trail:** All security-sensitive authentication and wallet lifecycle events are recorded in an append-only audit log table.

---

## 2. Authentication Flow

```
[ Frontend / User (MetaMask) ]                     [ Backend (FastAPI + DB) ]
             |                                                    |
             | ---- 1. POST /api/v1/auth/nonce (wallet, chain) -> |
             |                                                    | (Generate 16-byte random hex)
             |                                                    | (Store in auth_nonces, TTL=5m)
             |                                                    | (Audit: AUTH_NONCE_CREATED)
             | <-- 2. NonceResponse { nonce, expires_at } -------- |
             |                                                    |
   (Construct EIP-4361 message)                                   |
   (Sign via personal_sign / wagmi)                               |
             |                                                    |
             | ---- 3. POST /api/v1/auth/verify { msg, sig } ---> |
             |                                                    | [1. Parse SIWE message]
             |                                                    | [2. Validate domain against whitelist]
             |                                                    | [3. Validate chain ID (31337, 84532, 8453)]
             |                                                    | [4. Validate timestamps & expiration]
             |                                                    | [5. Verify signature & recover signer]
             |                                                    | [6. Check nonce unconsumed & unexpired]
             |                                                    | [7. Atomically mark nonce consumed]
             |                                                    | [8. Upsert User & Wallet records]
             |                                                    | [9. Create Session (SHA-256 token hash)]
             |                                                    | [10. Audit: AUTH_SUCCESS]
             | <-- 4. Set-Cookie: agentchain_session (HttpOnly) - |
             |        VerifyResponse { authenticated, user, ... } |
             |                                                    |
             | ---- 5. Subsequent Requests (Cookie / Bearer) ---> |
             |                                                    | (Hash token -> Lookup active session)
             |                                                    | (Enforce role & wallet constraints)
```

---

## 3. Threat Model & Implemented Mitigations

| Threat | Vulnerability Vector | Implemented Mitigation |
| :--- | :--- | :--- |
| **Signature Replay** | Attacker intercepts a valid signature and replays it to authenticate or impersonate the user. | Nonces are stored server-side with `consumed_at` timestamp. During verification, the nonce is checked for single use and atomically marked consumed in the DB transaction. Replaying identical messages is rejected with HTTP 400. |
| **Nonce Replay** | Attacker attempts to reuse an issued nonce across different messages or sessions. | Nonces have unique DB constraints (`uq_nonce`) and are invalidated immediately upon first verification or expiration (5 min). |
| **Session Fixation** | Attacker establishes a session and tricks victim into authenticating with it. | Sessions are minted strictly server-side *after* signature verification completes. A fresh high-entropy random token (`secrets.token_urlsafe(32)`) is generated on each login. |
| **Session Theft / XSS Token Exfiltration** | Malicious scripts in the client read authentication tokens from `localStorage`. | Session tokens are delivered via `HttpOnly` cookies that JavaScript cannot access. Zero authentication tokens are stored in `localStorage`. |
| **Session Hijacking in Transit** | Unencrypted HTTP connection allows sniffing session tokens. | In production, `SESSION_COOKIE_SECURE=True` forces cookie transmission only over TLS/HTTPS. |
| **Wallet Spoofing** | Attacker sends a wallet address belonging to a high-privileged user in payload body. | The backend does not trust any user-supplied address. The wallet address is recovered cryptographically from the SIWE message signature and validated against the message address. |
| **Address Normalization Flaws** | Case discrepancies (`0xabc...` vs `0xABC...`) causing duplicate accounts or auth bypass. | All Ethereum addresses are normalized to EIP-55 checksum format via `Web3.to_checksum_address` before database lookup or persistence. |
| **Wrong-Chain Signatures** | Attacker signs message on an unapproved or compromised chain. | SIWE `chain_id` is strictly checked against `settings.ALLOWED_CHAIN_IDS` (`[31337, 84532, 8453]`). Unauthorized chains are rejected with HTTP 400. |
| **Phishing Domains** | Attacker hosts phishing portal and prompts signature with evil domain. | The backend validates `siwe_msg.domain` against configured whitelist (`settings.SIWE_ALLOWED_DOMAINS`). Phishing domains fail validation. |
| **CSRF (Cross-Site Request Forgery)** | Malicious third-party site initiates requests using the ambient session cookie. | Session cookies enforce `SameSite=Lax` (or `SameSite=Strict`), preventing cross-origin requests from transmitting the session cookie. Non-GET mutations require explicit JSON headers. |
| **Brute-Force & Credential Stuffing** | Flooding `/auth/nonce` and `/auth/verify` to exhaust entropy or guess signatures. | Redis-backed sliding window rate limiter protects `/auth/nonce` (30 req/min) and `/auth/verify` (15 req/min). Returns HTTP 429 with `Retry-After`. |
| **Authorization Bypass** | Authenticated client attempts to call privileged operator or admin endpoints. | Declarative FastAPI dependencies (`require_authenticated_user`, `require_role`, `require_wallet_verified`) validate user permissions server-side on every request. |

---

## 4. Role-Based Access Control (RBAC)

AgentChain implements a hierarchical role-based authorization model:

1. **`CLIENT`**: Default role for new users. May submit tasks, fund escrow, and view own tasks.
2. **`DEVELOPER`**: May register and manage AI agents, tools, and developer configurations.
3. **`AGENT_OPERATOR`**: May operate active worker containers, stake collateral, and receive task payouts.
4. **`VERIFIER`**: May participate in decentralized validation quorums and submit verification results.
5. **`ADMIN`**: Superuser possessing platform administration, emergency dispute resolution, and audit access. Bypasses individual role checks.

### Reusable Authorization Dependencies

```python
# Route requiring any authenticated user
@router.get("/me")
async def get_me(user: User = Depends(require_authenticated_user)):
    ...

# Route requiring specific role (or ADMIN)
@router.get("/operator/status")
async def get_operator_status(user: User = Depends(require_role(UserRole.AGENT_OPERATOR))):
    ...

# Route requiring verified wallet
@router.post("/tasks")
async def create_task(wallet: Wallet = Depends(require_wallet_verified)):
    ...
```

---

## 5. Audit Logging Architecture

Security-sensitive operations record immutable audit events in the `audit_logs` table:

- `AUTH_NONCE_CREATED`: Nonce requested for a specific wallet address.
- `AUTH_SUCCESS`: Successful SIWE verification and session issuance.
- `AUTH_FAILURE`: Failed verification attempt with structured failure reasons (e.g. `domain_mismatch`, `invalid_signature`, `nonce_expired`, `nonce_already_consumed`).
- `LOGOUT`: Active session revocation.
- `WALLET_ADDED`: Secondary wallet linked via cryptographic proof.
- `WALLET_REMOVED`: Wallet unlinked from account.
- `ROLE_CHANGED`: User role promotion or demotion.
- `SESSION_REVOKED`: Forced session termination.

Audit logs never store sensitive authentication materials, passwords, private keys, or raw signatures.
