# PHASE 6.10.1 — REAL AWS KMS PRODUCTION SIGNER INTEGRATION & CRYPTOGRAPHIC VERIFICATION

## 1. Overview & Operational Scope

Phase 6.10.1 introduces the concrete production signing provider implementation for AgentChain: **`AwsKmsSigner`**.

This phase implements end-to-end cryptographic integration with AWS Key Management Service (KMS) using asymmetric secp256k1 key custody (`ECC_SECG_P256K1`). The implementation ensures that:
- The private key **never leaves the KMS Hardware Security Module (HSM)**.
- Zero raw private keys are stored in memory, parameters, environment variables, logs, or databases.
- The Ethereum transaction digest is signed directly using KMS's `MessageType="DIGEST"` without accidental double-hashing.
- Signatures undergo strict DER decoding, canonical low-S normalization ($s \le N/2$), and deterministic recovery ID ($v \in \{0, 1\}$) determination.
- Signer identity is strictly bound to `EXPECTED_SIGNER_ADDRESS` at startup, failing closed on any discrepancy with `SignerIdentityMismatchError`.
- Base Mainnet (Chain ID `8453`) remains **unconditionally hard blocked**.

---

## 2. Cryptographic Architecture & Flow

```text
Ethereum Transaction (EIP-1559 or Legacy)
                 ↓
Keccak-256 Hash of Unsigned RLP Serialized Tx
                 ↓  (32 bytes digest)
AWS KMS Sign API:
  KeyId: AWS_KMS_KEY_ARN
  MessageType: "DIGEST"
  SigningAlgorithm: "ECDSA_SHA_256"
                 ↓
DER-Encoded ECDSA Signature (ASN.1 SEQUENCE of 2 INTEGERs)
                 ↓
Strict DER Decoder:
  - ASN.1 tag 0x30 validation & exact sequence length
  - Two positive INTEGERs (r, s)
  - Rejection of negative integers, excessive 0x00 padding, zero values, >= N, trailing data
                 ↓
Canonical Low-S Normalization:
  - Let N = secp256k1 curve order
  - If s > N // 2: s = N - s
                 ↓
Deterministic Recovery-ID Determination:
  - Test candidate recovery IDs: cand_v ∈ {0, 1}
  - Recover candidate public key: Signature(cand_v, r, s).recover(digest)
  - Match recovered public key == KMS public key
  - Selected rec_id ∈ {0, 1}
                 ↓
Transaction Signature Construction:
  - For EIP-1559 (TypedTransaction): v = rec_id
  - For Legacy (EIP-155): v = chain_id * 2 + 35 + rec_id
  - RLP encode signed transaction
                 ↓
On-Chain Address Verification:
  - Account.recover_transaction(raw_tx) == KMS derived Ethereum address
```

---

## 3. AWS KMS Specifications & Metadata Validation

Before any signing operation is permitted, the `AwsKmsSigner` validates the KMS key metadata via `DescribeKey`:
- `KeySpec`: Must be strictly `ECC_SECG_P256K1`. Rejects RSA, NIST P-256, NIST P-384, NIST P-521, ED25519, and symmetric keys.
- `KeyUsage`: Must be strictly `SIGN_VERIFY`. Rejects `ENCRYPT_DECRYPT` and `GENERATE_VERIFY_MAC`.
- `KeyState`: Must be strictly `Enabled`. Rejects `Disabled`, `PendingDeletion`, and `PendingImport`.

---

## 4. SubjectPublicKeyInfo (SPKI) Parsing & Address Derivation

AWS KMS exposes the public key through `GetPublicKey` in DER-encoded SubjectPublicKeyInfo (SPKI) format (88 bytes):
1. ASN.1 SEQUENCE (`0x30 0x56`)
2. AlgorithmIdentifier (`id-ecPublicKey` OID `1.2.840.10045.2.1` and `secp256k1` OID `1.3.132.0.10`)
3. BIT STRING (`0x03 0x42 0x00`)
4. 65-byte uncompressed elliptic curve point: `0x04 || X (32 bytes) || Y (32 bytes)`

The public key is verified to lie on the secp256k1 curve. The Ethereum address is derived via canonical Keccak-256 hashing:
$$\text{address} = \text{Checksum}(\text{Keccak-256}(X \parallel Y)[12:])$$

---

## 5. Strict DER ECDSA Parser Specification

The DER parser rejects permissive or malformed inputs:
- Valid ASN.1 SEQUENCE tag (`0x30`) required; total length must equal remaining buffer.
- Exactly two INTEGER elements (`0x02 <len> <val>`) for $r$ and $s$.
- Rejects negative integers (MSB bit set without `0x00` prefix).
- Rejects superfluous zero padding (e.g. `0x00 0x01` instead of `0x01`).
- Rejects zero ($r = 0$ or $s = 0$).
- Rejects values $\ge N$ where $N = \text{0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BB5CA5B9659CA6D3D}$.
- Rejects trailing garbage after the second INTEGER.

---

## 6. Signer Identity & Network Binding

### Identity Binding
At initialization or preflight startup:
1. `AwsKmsSigner` derives the Ethereum address from the KMS public key.
2. If `EXPECTED_SIGNER_ADDRESS` is provided, it is compared (checksummed).
3. If there is any mismatch, initialization immediately fails closed with `SignerIdentityMismatchError`.
4. If the derived address matches known default Anvil/Hardhat accounts (`0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266`), initialization fails with `SecurityConfigurationError`.

### Network Binding
- Base Sepolia (`84532`): Permitted when fully configured.
- Anvil (`31337`): Strictly rejected (`SignerNetworkMismatchError`).
- Base Mainnet (`8453`): Strictly hard blocked (`MainnetSubmissionBlockedError`).
- Non-matching chain IDs: Strictly rejected (`SignerNetworkMismatchError`).

---

## 7. Dependency Injection & Client Isolation

To ensure that the application is testable without ambient AWS credentials, `AwsKmsSigner` accepts an optional `client: AwsKmsClientProtocol`.

```python
@runtime_checkable
class AwsKmsClientProtocol(Protocol):
    def describe_key(self, **kwargs: Any) -> dict[str, Any]: ...
    def get_public_key(self, **kwargs: Any) -> dict[str, Any]: ...
    def sign(self, **kwargs: Any) -> dict[str, Any]: ...
    def verify(self, **kwargs: Any) -> dict[str, Any]: ...
```

In production, if `client` is omitted, `AwsKmsSigner` instantiates `boto3.client("kms", region_name=...)` utilizing the ambient AWS SDK credential provider chain.

---

## 8. Relayer Integration

The `BlockchainRelayer` integrates with `AwsKmsSigner` via `SignerFactory`:
- Relayer preflight startup (`verify_startup`) executes:
  1. `rpc_client.verify_chain_id()`
  2. `signer.verify_network_compatibility(chain_id)`
  3. `signer.health_check()`
  4. Blocked account check on `signer.get_address()`
- During transaction execution:
  - Relayer prepares standard unsigned transaction dictionary.
  - Passes transaction to `signer.sign_transaction(raw_tx)`.
  - KMS signs the 32-byte transaction hash without exposing key material.
  - Relayer broadcasts the RLP-encoded raw transaction to the RPC node.

---

## 9. Verification & Safety Summary

| Test Suite | Tests Passed | Status | Classification |
|---|:---:|:---:|:---:|
| `test_aws_kms_signer.py` | 43 | PASS | LOCAL — EXECUTED |
| `test_phase_6_10_production_signer_mainnet_safety.py` | 21 | PASS | LOCAL — EXECUTED |
| `test_production_readiness.py` | 24 | PASS | LOCAL — EXECUTED |
| `forge test --root contracts` | 122 | PASS | LOCAL — EXECUTED |
| `verify_production_safety_gates.py` | 8 | PASS | STATIC — INSPECTED |
| `check_config_drift.py` | Zero Drift | PASS | STATIC — INSPECTED |
| `verify_base_sepolia_read_only.py` | 21 state reads | PASS | LIVE — EXTERNAL CHAIN STATE READ |
| Live AWS KMS API Verification | 0 | NOT EXECUTED | NOT EXECUTED |

Zero state-changing transactions broadcast on Base Sepolia (`84532`) or Base Mainnet (`8453`).
Base Mainnet remains **HARD BLOCKED**.
