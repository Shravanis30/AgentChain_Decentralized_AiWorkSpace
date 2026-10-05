# Cryptographic Result Notarization Architecture (Phase 6.3)

## 1. Overview & Purpose

Phase 6.3 establishes the tamper-proof cryptographic proof layer connecting:
```
Agent Execution 
  → Canonical Result
  → RFC 8785 Canonicalization
  → SHA-256 Result Hash
  → Optional Artifact Reference / IPFS CID
  → On-Chain Notarization (ResultNotary.sol)
  → Blockchain Event (ResultNotarized)
  → Canonical Indexer & 32-Block Confirmation Depth
  → Read-Only Cryptographic Verification API
```

This layer anchors the computational output of autonomous agent executions to immutable blockchain proof without storing bulky payloads on-chain or coupling cryptographic proofs to settlement or escrow economics.

---

## 2. Canonical Notarization Envelope & Determinism

### Deterministic Payload Envelope
The canonical envelope standardizes execution output before computing cryptographic commitments:

```json
{
  "protocol_version": "1.0",
  "execution_id": "018f3a2c-7b19-7561-9c90-0c4a962a939f",
  "agent_id": "018f3a2c-7b19-7561-9c90-0c4a962a9301",
  "agent_version": "1.0.0",
  "result_hash": "b2910fa2bb76efeb60fa84bd3a954fb77df34701a2b46b1b932ca85eeca6ac6b",
  "hash_algorithm": "SHA-256",
  "canonicalization": "RFC-8785",
  "artifact_reference": "ipfs://bafybeic7n6y2b66d4m73x5g5yq7fey7r3v5sxgvx",
  "orchestration_id": null,
  "task_id": null,
  "completed_at": "2026-09-28T06:53:30.063991+00:00"
}
```

### Canonicalization Algorithm (RFC 8785)
- Standard: JSON Canonicalization Scheme (RFC 8785 / JCS).
- Deterministic key ordering: UTF-16 code point order.
- Numeric normalization: IEEE 754 float representation, no redundant trailing zeroes, strict exponents.
- Whitespace stripping: Compact representation with zero whitespace between tokens.
- Unicode encoding: UTF-8 normalized characters.

### Result Hash Immutability
Once an `AgentExecution` transitions to terminal `SUCCEEDED` status:
- Its `output_hash` is permanently fixed in PostgreSQL.
- Database constraints and service-level guards strictly prevent modification or replacement.
- Any attempt to submit a conflicting hash raises `ResultHashImmutabilityViolationError`.

---

## 3. ResultNotary Smart Contract (`ResultNotary.sol`)

### Contract Responsibilities
The `ResultNotary` smart contract acts as an immutable, append-only cryptographic proof registry. It custody **zero tokens** and has no financial balance or payout functions.

### Interface
```solidity
interface IResultNotary {
    struct ProofRecord {
        bytes32 executionId;
        bytes32 resultHash;
        bytes32 artifactCommitment;
        uint64 timestamp;
        uint64 blockNumber;
        address notarizer;
    }

    event ResultNotarized(
        bytes32 indexed executionId,
        bytes32 indexed resultHash,
        bytes32 artifactCommitment,
        uint64 timestamp
    );

    function notarizeResult(
        bytes32 executionId,
        bytes32 resultHash,
        bytes32 artifactCommitment
    ) external;

    function getProof(bytes32 executionId) external view returns (ProofRecord memory);
    function hasProof(bytes32 executionId) external view returns (bool);
    function verifyProof(bytes32 executionId, bytes32 expectedResultHash) external view returns (bool);
}
```

### Access Control
- Function `notarizeResult` is restricted to accounts with `NOTARIZER_ROLE`.
- The backend relayer's signing address holds `NOTARIZER_ROLE`.
- Arbitrary EOA or unauthenticated callers cannot write or mutate proofs.

### On-Chain Replay Protection
- `mapping(bytes32 => ProofRecord) private _proofs;`
- If `_proofs[executionId].executionId != bytes32(0)`, any subsequent attempt to notarize reverts with `AlreadyNotarized(executionId)`.
- Proofs are permanently immutable once written.

---

## 4. Pipeline Integration

1. **Execution**: Worker completes execution and calls `ExecutionStateMachine.transition_to_succeeded()`, persisting `output_data` and canonical `output_hash`.
2. **Intent Creation**: `NotarizationService.create_notarization()` validates eligibility (status SUCCEEDED, output_hash valid, not cancelled) and atomically generates a `BlockchainTransactionIntent` for `notarizeResult`.
3. **Outbox & Redis**: Atomic `BlockchainTxOutbox` row is created in the same database transaction and dispatched via Redis Stream to the relayer.
4. **Relayer & Anvil**: Existing `BlockchainRelayer` extracts intent parameters, estimates EIP-1559 fees, checks nonce locks, and broadcasts the signed transaction.
5. **Indexer**: `EventIndexer` ingests `ResultNotarized` events into `blockchain_events` table and invokes `NotarizationProjector`.
6. **Reconciliation**: `NotarizationReconciliationService` advances confirmation depth towards the required 32 blocks and marks proof `CONFIRMED`.

---

## 5. Separation of Concerns
- **Execution Proof vs. Settlement**: Result notarization does not require escrow funds or payment. A failed settlement never invalidates a cryptographic execution proof, and payment release cannot rewrite execution hashes.
- **Off-Chain Artifacts**: Large computation payloads are referenced by URI/CID (e.g. `ipfs://...`). The blockchain only anchors 32-byte cryptographic hashes.
