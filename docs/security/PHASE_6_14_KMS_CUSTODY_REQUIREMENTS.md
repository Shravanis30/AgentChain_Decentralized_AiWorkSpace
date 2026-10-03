# Phase 6.14: Production AWS KMS Key Custody & Role Separation Requirements

**Document ID**: AGY-SEC-6.14-KMS-CUSTODY  
**Phase**: 6.14 — Production Operator Activation Readiness & Governance Execution Package  
**Release Candidate Version**: `1.1.0-rc1`  
**Classification**: SECURITY SPECIFICATION & HARDWARE KEY CUSTODY POLICY  
**Release Freeze Status**: **STRICTLY FROZEN**  
**Date**: October 5, 2026  

---

## 1. Scope & Objective

This document defines the non-negotiable security requirements for hardware key custody using AWS Key Management Service (KMS) and role separation across AgentChain production infrastructure.

In production (`APP_ENV=production`), AgentChain strictly prohibits in-memory raw private keys, local keystores, and shared administrative roles. All cryptographic transaction signing for settlement release, result notarization, and dispute processing must originate from an authenticated FIPS 140-2 Level 3 Hardware Security Module (HSM).

---

## 2. Hardware KMS Key Specification

Any AWS KMS key provisioned for AgentChain production operations MUST meet the following immutable technical parameters:

```text
Key Specification:      ECC_SECG_P256K1 (Elliptic Curve Cryptography over secp256k1)
Key Usage:              SIGN_VERIFY (Signing and verification only; encryption strictly forbidden)
Key State:              Enabled
Hardware Classification: FIPS 140-2 Level 3 HSM Custody
Key Policy:             Least-privilege policy restricting kms:Sign and kms:GetPublicKey
                        strictly to the relayer execution IAM role
Signature Algorithm:    ECDSA_SHA_256 (Invoked via MessageType='DIGEST' with 32-byte Keccak-256)
```

### Invariants:
1. **Zero Key Export**: The private key material must be generated inside the HSM and configured as non-exportable.
2. **Digest Signing Only**: The relayer must pass pre-computed 32-byte Keccak-256 hashes (`MessageType='DIGEST'`) to avoid double-hashing.
3. **Deterministic Verification**: Application code normalizes raw DER ASN.1 signatures to BIP-62 low-S format ($s \le N/2$) and computes recovery ID ($v \in \{27, 28\}$) locally.

---

## 3. Signer Identity Binding & Address Verification

The production signer abstraction (`AwsKmsSigner` in `backend/app/services/blockchain/signer.py`) enforces strict cryptographic address binding before allowing any transaction broadcast:

```text
DERIVED SIGNER ADDRESS:
Derived from AWS KMS SPKI public key (Uncompressed 64-byte point -> Keccak-256 -> Last 20 bytes)
MUST EQUAL
EXPECTED_SIGNER_ADDRESS:
Governance-approved Ethereum checksum address
```

If the derived address does not exactly match `EXPECTED_SIGNER_ADDRESS`:
- `AwsKmsSigner` immediately raises `SignerIdentityMismatchError`.
- `BlockchainRelayer.verify_startup()` fails closed.
- The relayer process terminates with zero transactions submitted.

---

## 4. Production Role Separation Invariant

To prevent single-key compromise from catastrophic failure, AgentChain mandates strict multi-role isolation:

```text
RELAYER_ROLE_ADDRESS    != ADMIN_ROLE_ADDRESS
RELAYER_ROLE_ADDRESS    != ARBITRATOR_ROLE_ADDRESS
ADMIN_ROLE_ADDRESS      != ARBITRATOR_ROLE_ADDRESS
```

| Role Name | Authority Scope | Target Custody Mechanism | Current Value |
| :--- | :--- | :--- | :--- |
| **Relayer Role** | Nonce management, gas estimation, and transaction submission for notarization and settlement release. | AWS KMS HSM Key (`ECC_SECG_P256K1`) | `NOT ASSIGNED` |
| **Admin Role** | Contract pausing/unpausing, role granting/revocation, fee recipient configuration. | Multi-Signature Cold Storage (e.g. Safe 3/5) | `NOT ASSIGNED` |
| **Arbitrator Role** | Escrow dispute resolution and split adjudications under contested execution. | Independent Governance / Arbitration Multi-Sig | `NOT ASSIGNED` |

### Blocked Production Addresses:
The following addresses are permanently hard-blocked across all production environments:
- `0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266` (Anvil Account #0)
- `0x70997970C51812dc3A010C7d01b50e0d17dc79C8` (Retired Development Account)
- `0x3C44CdDdB6a900fa2b585dd299e03d12FA4293BC` (Anvil Account #2)
- Any development account or single private-key EOA shared across administrative functions.

Placeholder addresses must NEVER be deployed to production configurations. Until formal assignment by the governance authority, all role fields remain `NOT ASSIGNED`.

---

## 5. Fail-Closed Health Monitoring & Error Modes

The production signer implementation categorizes operational failure modes into structured health outcomes:

```text
KmsHealthStatus.HEALTHY                    -> Relayer startup permitted
KmsHealthStatus.KMS_UNAVAILABLE            -> Fail closed (KMS unreachable / credentials expired)
KmsHealthStatus.KMS_KEY_DISABLED           -> Fail closed (KMS key state is Disabled / PendingDeletion)
KmsHealthStatus.KMS_KEY_INVALID            -> Fail closed (KeySpec != ECC_SECG_P256K1 or KeyUsage != SIGN_VERIFY)
KmsHealthStatus.SIGNER_IDENTITY_MISMATCH   -> Fail closed (Derived address != EXPECTED_SIGNER_ADDRESS)
KmsHealthStatus.NETWORK_MISMATCH           -> Fail closed (Configured chain ID != Supported signer chain)
KmsHealthStatus.CONFIG_INVALID             -> Fail closed (ARN or region missing)
```

In any non-healthy condition, the relayer halts, logs an error alert, and refuses transaction submission.
