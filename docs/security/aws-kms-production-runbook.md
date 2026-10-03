# AWS KMS Production Signer Operational Runbook

## 1. Overview & Operational Model

This document establishes the authoritative operational runbook for configuring, deploying, monitoring, and operating the AgentChain AWS KMS Production Signer (`AwsKmsSigner`).

The architecture strictly decouples **Key Provisioning Authority** from **Application Runtime Signing Authority**.

---

## 2. Three-Role Governance & Segregation of Duties

To enforce defense-in-depth and prevent privilege escalation, three distinct operational roles are defined:

```text
┌─────────────────────────────────┐
│   Provisioning Administrator    │ ──→ Creates KMS key, assigns IAM policy,
│   (Infrastructure / Cloud Ops)  │     manages cloud lifecycle out-of-band.
└─────────────────────────────────┘
                 │ (Hands off Key ARN)
                 ▼
┌─────────────────────────────────┐
│  Release / Governance Authority │ ──→ Reviews derived public key and address,
│  (Security Officer / Multi-Sig) │     formally approves EXPECTED_SIGNER_ADDRESS.
└─────────────────────────────────┘
                 │ (Approves configuration)
                 ▼
┌─────────────────────────────────┐
│     Runtime Signer Identity     │ ──→ Least-privilege IAM role used by backend relayer.
│      (AgentChain Backend)       │     Can ONLY execute DescribeKey, GetPublicKey, Sign, Verify.
└─────────────────────────────────┘
```

1. **Provisioning Administrator**:
   - Provisions asymmetric KMS key with KeySpec `ECC_SECG_P256K1` and KeyUsage `SIGN_VERIFY`.
   - Never has access to private key material (private key remains protected inside KMS HSM).
   - Grants least-privilege runtime policy to the application's IAM role.
2. **Release / Governance Authority**:
   - Inspects the KMS public key and independently derives the checksummed Ethereum address.
   - Signs off on production deployment configuration (`EXPECTED_SIGNER_ADDRESS`, `CHAIN_ID=84532`, canonical contracts).
   - Approves on-chain role grants (e.g. `RELAYER_ROLE` on `Escrow`).
3. **Runtime Signer Identity (AgentChain Backend)**:
   - Authenticates strictly through the ambient AWS SDK credential provider chain.
   - Holds zero permissions to create, modify, enable, disable, or delete KMS keys or IAM roles.
   - Operates strictly fail-closed: any deviation aborts relayer startup.

---

## 3. Production Startup Sequence & Safety Contract

Before the blockchain relayer accepts any transaction intents, the entire startup sequence must succeed:

```text
Load Configuration (.env / Vault)
            ↓
Validate Production Environment (app_env=production, debug=false, no raw keys)
            ↓
Validate Chain ID (84532 only; 8453 hard blocked; 31337 rejected)
            ↓
Validate Contract Addresses (all 6 canonical Base Sepolia contracts match)
            ↓
Load KMS Configuration (AWS_KMS_KEY_ARN, AWS_REGION, EXPECTED_SIGNER_ADDRESS)
            ↓
AWS KMS DescribeKey API
            ↓
Validate KeyMetadata:
  - KeySpec == ECC_SECG_P256K1
  - KeyUsage == SIGN_VERIFY
  - KeyState == Enabled
            ↓
AWS KMS GetPublicKey API (DER X.509 SPKI)
            ↓
Derive Ethereum Address (Keccak-256 of uncompressed 64-byte point)
            ↓
Check Identity Binding (derived_address == EXPECTED_SIGNER_ADDRESS)
            ↓
Signer Health Check (check_health_status() == HEALTHY)
            ↓
Relayer Preflight (verify_startup(): RPC chain verification, address allowlists)
            ↓
          READY
```

If any step fails, startup is immediately aborted, and no transactions can be prepared or submitted.

---

## 4. Monitoring & Alerting Requirements

Production monitoring must configure alerts for the following operational failure modes:

| Alert Name | Condition | Severity | Action |
|---|---|---|---|
| `KmsUnavailableAlert` | KMS API call timeouts, 5xx errors, network connection drops | Critical (P1) | Check AWS service health, network egress, IAM credentials. |
| `KmsKeyDisabledAlert` | KMS DescribeKey reports `KeyState != Enabled` | Critical (P1) | Relayer stops immediately. Contact Provisioning Admin. |
| `KmsPermissionDeniedAlert` | KMS API returns `AccessDeniedException` | Critical (P1) | Audit IAM role session and key policy. |
| `SignerIdentityMismatchAlert` | Derived address != `EXPECTED_SIGNER_ADDRESS` | Critical (P1) | Halt deployment; investigate potential key substitution or config drift. |
| `NetworkMismatchAlert` | Relayer chainId != configured chainId | Critical (P1) | Halt deployment; reject intent submission. |
| `SignerHealthCheckFailure` | `signer.health_check()` returns False | High (P2) | Inspect relayer logs for structured `KmsHealthStatus`. |
| `RepeatedSigningFailure` | 3+ consecutive KMS sign errors | High (P2) | Check transaction payload formatting and KMS quota limits. |

---

## 5. Audit Logging Architecture

Signer operational events must be recorded with structured metadata:

### Safe Event Types
- `sign_attempt`: `intent_id`, `tx_hash`, `chain_id`
- `sign_success`: `intent_id`, `tx_hash`, `recovery_id`, `duration_ms`
- `sign_failure`: `intent_id`, `error_type`, `duration_ms`
- `startup_rejected`: `status_code`, `reason`, `chain_id`
- `identity_mismatch`: `derived_address`, `expected_address`
- `network_mismatch`: `configured_chain`, `requested_chain`
- `kms_unavailable`: `aws_region`, `error_code`

### Non-Negotiable Logging Invariants
- **NEVER** log AWS access keys, secret keys, or session tokens.
- **NEVER** log raw private keys or seed phrases (private key is inaccessible inside KMS).
- **NEVER** log raw `Authorization` request headers.
- **NEVER** log unredacted sensitive customer payloads.
