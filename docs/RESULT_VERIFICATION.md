# Result Verification Guide (Phase 6.3)

## 1. Overview

AgentChain provides deterministic, tamper-proof verification of agent execution results. Third parties, clients, and auditors can verify that an agent's output has not been tampered with and was anchored to an authoritative blockchain proof.

---

## 2. Verification API Endpoints

### 1. Lookup Notarization by Primary ID
```http
GET /api/v1/notarizations/{notarization_id}
```
**Response**: Returns complete `ResultNotarizationResponse` including on-chain transaction metadata, block numbers, confirmation depth, and canonical status.

---

### 2. Lookup Notarization by Execution ID
```http
GET /api/v1/notarizations/execution/{execution_id}?chain_id=31337
```
**Response**: Returns the `ResultNotarizationResponse` for the specified execution on the requested chain.

---

### 3. Cryptographic Proof Verification
```http
POST /api/v1/notarizations/execution/{execution_id}/verify?chain_id=31337
Content-Type: application/json

{
  "result_payload": {
    "computed_data": "value",
    "status": "success"
  },
  "result_hash": "optional_precomputed_sha256_hash"
}
```

#### Verification Logic:
1. If `result_payload` is supplied, the server canonicalizes the dictionary via **RFC 8785** and computes its **SHA-256** hash.
2. If `result_hash` is supplied, it is compared against the on-chain notarized hash.
3. The server queries the canonical blockchain proof in `ResultNotary`.

---

## 3. Verification Verdicts

| Status Code | Meaning | Action / Interpretation |
| :--- | :--- | :--- |
| `VERIFIED` | Canonical hash matches on-chain proof with 32+ confirmations. | Execution result is authentic and tamper-proof. |
| `HASH_MISMATCH` | Computed or supplied hash differs from the on-chain notarization. | Output has been tampered with or corrupted. |
| `NOT_CONFIRMED` | Proof exists on-chain but has not yet reached required 32 confirmations. | Wait for additional block confirmations. |
| `NOT_NOTARIZED` | No notarization intent or proof exists for this execution identity. | Request notarization or check execution status. |
| `REORGED` | The proof was submitted in a block that was subsequently orphaned by a chain reorganization. | Wait for re-submission on canonical chain. |

---

## 4. Frontend Observability Interface

A read-only verification dashboard is available at `/notarizations`:
- Search by Execution UUID.
- Inspect canonical result hash, hash algorithm, canonicalization version, artifact URI.
- Inspect transaction hash, block number, confirmation depth, and canonical flag.
- Click "Verify Proof" to run real-time server-side recomputation and smart contract proof validation.
- Strictly read-only: **no wallet signing**, **no editable fields**, **no Mainnet submission**.
