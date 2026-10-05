# Relayer Security & Key Management Boundaries (Phase 6.2A)

> **CRITICAL SECURITY REQUIREMENT:**
> Private keys must NEVER be stored in PostgreSQL, Redis, application logs, metrics, or API responses.
> Base Mainnet transaction submission MUST remain hard-disabled.

---

## 1. Signer Boundary & Key Architecture

The relayer decouples business transaction logic from signing credentials through an explicit abstract boundary:

```text
Application Services
        ↓  (Idempotent Intent)
BlockchainTxOutbox (PostgreSQL)
        ↓  (Durable Queue)
Redis Stream (`agentchain:blockchain:tx_stream`)
        ↓  (Worker Ingestion)
BlockchainRelayer
        ↓  (Unsigned EIP-1559 Transaction Dict)
====================== SECURE SIGNER BOUNDARY ======================
  TransactionSigner Interface
    ├── LocalAccountSigner (In-memory only for Anvil / Sepolia test execution)
    └── Future Production Signers:
          ├── AWS KMS / GCP Cloud KMS (Hardware Security Module)
          ├── HashiCorp Vault Transit Engine
          └── MPC / Multi-Party Custody (e.g. Fireblocks, Turnkey)
====================================================================
        ↓  (Raw Signed Byte String: 0x02...)
RPC Client Broadcast (eth_sendRawTransaction)
```

---

## 2. Hard Security Controls & Enforcement

### 1. Base Mainnet Hard-Stop Barrier
In `app/services/blockchain/config.py` and `rpc_client.py`:
```python
if self.chain_id == CHAIN_ID_BASE_MAINNET:
    raise MainnetSubmissionBlockedError(
        "HARD SECURITY GATING: Base Mainnet transaction submission is disabled in Phase 6.2A"
    )
```
This is a compile-time and run-time invariant that fails closed unconditionally.

### 2. Contract Address Allowlist
Every transaction intent must target an allowlisted contract explicitly registered in the network's configuration:
- For Anvil: Verified local test deployment address.
- For Base Sepolia: Verified testnet deployment address.
- Arbitrary contract addresses are rejected with `ContractMismatchError`.

### 3. Operation & Method Allowlist
The relayer only accepts 10 pre-approved operations from `Escrow.sol`:
- `createEscrow`, `createAndFundEscrow`
- `fundEscrow`, `lockEscrow`, `releaseEscrow`, `refundEscrow`
- `disputeEscrow`, `resolveDispute`
- `pause`, `unpause`

Any unknown method name is rejected with `UnauthorizedOperationError`.

### 4. Zero Arbitrary Calldata
Calldata is strictly encoded using typed arguments from `ESCROW_ABI`. Raw, pre-encoded, or unchecked byte buffers are rejected with `ArbitraryCalldataBlockedError`.

### 5. Gas Limits & Ceiling
- Default gas limit: `300,000` gas.
- Maximum fee ceiling: `200 gwei` (`200,000,000,000 wei`).
- Prevents uncontrolled fee escalation during network congestion or mempool replacement loops.

### 6. Transaction Replacement Safety & Concurrency Boundaries (Phase 6.2A.1 Hardening)
- **RPC Outage Immunity:** The relayer and reconciler strictly differentiate `NOT_FOUND` from `RPC_ERROR` / `NETWORK_UNAVAILABLE`. A replacement is never triggered due to RPC errors.
- **Mined Detection:** Inspects `blockNumber` from `eth_getTransactionByHash` before replacement to ensure lagging receipts do not cause duplicate spends.
- **On-Chain Nonce Verification:** Asserts `latest_nonce == tx.nonce` before submitting replacement; if on-chain nonce advanced, reconciles safely as `DROPPED` rather than creating conflicting transactions.
- **Concurrent Worker Defense:** Prevents race conditions using row-level locks (`with_for_update(skip_locked=True)`) and optimistic attempt verification, ensuring only one worker bumps a stuck transaction.

---

## 3. Threat Model & Mitigation Matrix

| Threat | Attack Vector | Mitigation in Phase 6.2A / 6.2A.1 |
| :--- | :--- | :--- |
| **Accidental Mainnet Fund Movement** | Misconfiguration or environment spill | Hardcoded `allow_transactions = False` on Chain ID 8453; `MainnetSubmissionBlockedError` raised on every broadcast attempt. |
| **Key Leakage via Logs or DB** | Printing exceptions or persisting records | Signer holds key in-memory private attribute only; excluded from `__repr__` and `__str__`; no key columns exist in DB schemas. |
| **Calldata Injection / Drain** | Malicious payload passed to relayer | Relayer constructs calldata exclusively via ABI function templates; arbitrary calldata rejected. |
| **Double Spend / Duplicate Tx** | Replay of transaction requests | Strict database idempotency on `(chain_id, idempotency_key)` and row-level locked nonces. |
| **Spurious Replacement on RPC Outage** | Node timeout / 500 error causes false stuck detection | Explicit `UNKNOWN` classification; reconciliation defers immediately without replacement. |
| **Concurrent Replacement Race** | Multiple workers attempt to bump same stuck transaction | PostgreSQL row-level locks + fresh attempt verification ensure exactly one replacement is created. |
| **Frontend Privilege Escalation** | Direct RPC submission from client | Relayer is an internal worker consuming from Redis Streams; no public HTTP endpoints accept raw transaction commands. |
