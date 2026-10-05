# Result Notarization Security Specification (Phase 6.3)

## 1. Threat Model & Security Boundaries

Phase 6.3 establishes strict security guarantees to protect cryptographic result proofs against malicious manipulation, replay, unauthorized writing, and blockchain state ambiguity.

### Invariant Table

| Threat / Attack Vector | Mitigation Architecture | Verification Method | Status |
| :--- | :--- | :--- | :--- |
| **Tampered Result Payload** | RFC 8785 canonicalization + SHA-256 server recomputation | Adversarial unit tests comparing whitespace, key order, unicode | TESTED |
| **Unauthorized Notarization** | OpenZeppelin `AccessControl` with `NOTARIZER_ROLE` | Foundry tests asserting non-notarizers revert | TESTED |
| **Result Replay (Hash Override)** | Mapping existence check reverting with `AlreadyNotarized` | Smart contract test & backend idempotency test | TESTED |
| **Cross-Execution Hash Binding** | Strict execution ID $\leftrightarrow$ result hash binding | Cross-execution matrix adversarial tests | TESTED |
| **Cross-Chain Replay** | EIP-155 domain separation (`block.chainid`) & DB chain_id index | Multi-chain configuration drift tests | TESTED |
| **Arbitrary Calldata Injection** | Pinned ABI encoding, strict `ALLOWED_OPERATIONS` allowlist | Relayer calldata enforcement tests | TESTED |
| **Mainnet Accidental Submission** | Dual hard gates: `allow_transactions=False`, `BLOCK_MAINNET_SETTLEMENT=True` | Adversarial test attempting chain 8453 | TESTED |
| **Chain Reorganization Ambiguity** | 32-block confirmation threshold before `CONFIRMED` | Deep reorg simulation & invalidation tests | TESTED |

---

## 2. Cryptographic Proof Invariants

1. **Deterministic Hashing**:
   - `CanonicalNotarizationPayload` strictly defines hashed fields.
   - Database row IDs, non-deterministic timestamps, worker IDs, and mutable metadata are excluded.
   - RFC 8785 guarantees canonical JSON serialization across diverse runtimes.

2. **Result Hash Immutability**:
   - The SHA-256 digest calculated at `ExecutionStatus.SUCCEEDED` cannot be updated or overwritten.
   - Any API or worker call attempting to update an existing notarization with a distinct hash fails immediately.

3. **No Financial Custody**:
   - `ResultNotary.sol` contains no payable functions, ERC-20 transfers, or financial balances.
   - Settlement and revenue distribution are handled independently by `Escrow.sol` and `RevenueDistributor.sol`.

---

## 3. Access Control & Authorization

- **Contract Roles**:
  - `DEFAULT_ADMIN_ROLE`: Configured at deployment, transfers administrative rights.
  - `NOTARIZER_ROLE`: Grants permission to call `notarizeResult()`.
- **Signer Isolation**:
  - Execution workers hold zero private keys.
  - Blockchain transactions are prepared via `BlockchainTransactionIntent` and signed only by the backend `BlockchainRelayer` using isolated credentials.

---

## 4. Mainnet Hard Safeguards

Transactions on Base Mainnet (chain ID 8453) are strictly prohibited:
```python
if target_chain_id == CHAIN_ID_BASE_MAINNET:
    raise MainnetSubmissionBlockedError(
        "Result notarization on Base Mainnet (8453) is strictly blocked."
    )
```
Config flags remain pinned:
- `BLOCK_MAINNET_SETTLEMENT = True`
- `allow_transactions = False` for chain 8453.
