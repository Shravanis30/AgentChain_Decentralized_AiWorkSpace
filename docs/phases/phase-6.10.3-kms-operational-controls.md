# Phase 6.10.3 — Production KMS Operational Controls, Key Lifecycle & Release Runbook

## 1. Executive Summary

Phase 6.10.3 establishes the operational, lifecycle, emergency response, and security governance boundaries surrounding the production AWS KMS signer for AgentChain. 

Building upon the locally verified `AwsKmsSigner` implementation from Phase 6.10.1 and 6.10.2, this phase hardens the system against operational hazards without provisioning, mutating, or interacting with live AWS infrastructure or broadcasting state-changing blockchain transactions.

All 232 tests across unit, integration, failure injection, and smart contract suites passed 100%. Base Mainnet remains strictly hard blocked.

---

## 2. Operational Architecture

The production transaction signing pipeline enforces an uncompromising single-path boundary:

```text
Production Application / Relayer
            ↓
      AwsKmsSigner
            ↓
  AWS KMS (secp256k1)
```

No alternate signing provider is permitted in production:
* `LocalDevelopmentSigner` is hard-rejected at factory construction.
* `TestnetAccountSigner` is hard-rejected at factory construction.
* Raw `DEPLOYER_PRIVATE_KEY` / ambient private keys are rejected with `SecurityConfigurationError`.
* Anvil accounts (`0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266`) and Hardhat accounts are unconditionally blocked.
* Historical retired developer addresses (`0x000000000000000000000000000000000000dEaD`, `0x2222222222222222222222222222222222222222`) are blocked from role assignment.

---

## 3. Least-Privilege IAM & KMS Policy Model

The application runtime identity is strictly partitioned from administrative infrastructure management:

* **Policy Template:** [`docs/security/aws-kms-runtime-policy-template.json`](file:///Users/shravani/Desktop/AgentChain_AIWorkSpace/docs/security/aws-kms-runtime-policy-template.json)
* **Permitted Cryptographic Actions (Runtime Only):**
  - `kms:DescribeKey`
  - `kms:GetPublicKey`
  - `kms:Sign`
  - `kms:Verify`
* **Cryptographic Constraints:**
  - Algorithm strictly constrained to `ECDSA_SHA_256` via IAM condition `kms:SigningAlgorithm`.
  - Message type strictly constrained to precomputed 32-byte `DIGEST` via IAM condition `kms:MessageType`.
* **Explicit Runtime Deny:**
  - All provisioning and destructive operations (`kms:CreateKey`, `kms:UpdateKeyDescription`, `kms:EnableKey`, `kms:DisableKey`, `kms:ScheduleKeyDeletion`, `kms:PutKeyPolicy`, `kms:CreateGrant`) are explicitly denied.

---

## 4. Runtime vs. Provisioning Authority Separation

Three mutually exclusive roles govern the KMS signer lifecycle:

1. **Key Provisioning Administrator (Cloud Operations):**
   - Provisions AWS KMS asymmetric key pair (`ECC_SECG_P256K1`, `SIGN_VERIFY`).
   - Attaches key tags, manages CloudTrail audit configurations, and monitors key state.
   - Has zero access to AgentChain relayer runtime execution or smart contract administration.
2. **Runtime Signer Identity (AgentChain Backend Relayer):**
   - Operates with least-privilege runtime IAM role (only `DescribeKey`, `GetPublicKey`, `Sign`, `Verify`).
   - Derives Ethereum address on boot, compares against expected address, signs EIP-1559 payload digests.
   - Has zero permission to create, mutate, disable, or delete KMS keys or change policies.
3. **Release & Governance Authority (Multisig / Release Council):**
   - Reviews derived Ethereum address prior to deployment.
   - Approves `RELAYER_ROLE` on-chain transactions via multisig.
   - Signs release manifests and oversees planned key rotations.

---

## 5. Signer Identity Continuity Invariant

Signer identity continuity is enforced through strict cryptographic address binding:

```text
AWS KMS secp256k1 Public Key (SPKI)
                ↓
    Uncompressed 64-byte (X, Y)
                ↓
         Keccak-256 Hash
                ↓
    Last 20 bytes (Ethereum Address)
                ↓
    Compare with EXPECTED_SIGNER_ADDRESS
```

* **Invariant:** A rotated or altered KMS key **cannot** silently take over transactions.
* If `derived_address != EXPECTED_SIGNER_ADDRESS`, the signer health check transitions to `SIGNER_IDENTITY_MISMATCH` and `BlockchainRelayer.verify_startup()` fails closed, aborting initialization immediately.

---

## 6. Key Rotation Procedure

The formal key rotation procedure is documented in [`docs/security/aws-kms-key-rotation-runbook.md`](file:///Users/shravani/Desktop/AgentChain_AIWorkSpace/docs/security/aws-kms-key-rotation-runbook.md):

1. **Provision Phase:** Provision Key `B` in AWS KMS; derive candidate address $A_B$.
2. **Review & Approval Phase:** Governance Authority inspects key metadata and approves address $A_B$.
3. **On-Chain Dual-Signer Phase:** Grant contract roles (`RELAYER_ROLE`, `NOTARIZER_ROLE`, etc.) to $A_B$ alongside current signer $A_A$.
4. **Configuration Transition Phase:** Update `AWS_KMS_KEY_ARN` and `EXPECTED_SIGNER_ADDRESS` in secrets manager.
5. **Staged Deployment Phase:** Canary relayer instance boots with Key `B`, performs 13-step startup check.
6. **Decommissioning Phase:** Revoke contract roles from $A_A$; schedule Key `A` for deletion (minimum 30-day window).

---

## 7. Emergency Response & Key Disable Procedure

The emergency response runbook is documented in [`docs/security/aws-kms-emergency-response.md`](file:///Users/shravani/Desktop/AgentChain_AIWorkSpace/docs/security/aws-kms-emergency-response.md):

* **Objective:** Instantly halt transaction signing within $< 60$ seconds upon suspecting key compromise, unauthorized signing attempts, or smart contract exploits.
* **Execution (Out-of-Band):**
  1. Trigger backend circuit breaker (`KILL_SWITCH_ACTIVE=true`).
  2. Provisioning Administrator executes `aws kms disable-key --key-id <KEY_ARN>`.
  3. Relayer health check transitions to `KMS_KEY_DISABLED` and immediately blocks further execution.
  4. Contract emergency pause triggered via multisig (`Escrow.pause()`).
  5. Evidence preserved from CloudTrail and relayer audit logs.
  6. Replacement key provisioned and authorized following the rotation runbook.

---

## 8. Break-Glass Principles

* Emergency operations must **never** circumvent the KMS/HSM boundary by introducing local private keys.
* Application code contains **zero** fallback paths to raw private keys under any emergency condition.
* Break-glass procedures are conducted out-of-band using separate, time-bounded, dual-authorized IAM administrator roles.
* Every emergency action is logged to immutable CloudTrail and subject to post-incident security audit.

---

## 9. 13-Step Startup Safety Contract

The relayer implements a fail-closed 13-step startup verification contract before processing any transaction intent:

```text
 1. Load runtime configuration
 2. Validate production environment flags
 3. Validate Chain ID (Base Sepolia 84532 vs RPC; reject Mainnet 8453)
 4. Validate contract allowlist addresses
 5. Load KMS ARN and region configuration
 6. Execute KMS DescribeKey (validate key exists)
 7. Validate KeySpec == ECC_SECG_P256K1
 8. Validate KeyUsage == SIGN_VERIFY
 9. Validate KeyState == Enabled
10. Execute KMS GetPublicKey & derive Ethereum address
11. Compare derived address == EXPECTED_SIGNER_ADDRESS
12. Execute Signer health check (check_health_status() == HEALTHY)
13. Execute Relayer startup validation (verify_startup()) -> READY
```

Any exception or invariant failure during steps 1–13 prevents the relayer from accepting transactions.

---

## 10. Monitoring & Prometheus Alert Rules

Documented in [`docs/security/aws-kms-production-runbook.md`](file:///Users/shravani/Desktop/AgentChain_AIWorkSpace/docs/security/aws-kms-production-runbook.md):

| Alert | Condition | Severity | Action |
|---|---|---|---|
| `KmsSignerUnavailable` | Health check reports `KMS_UNAVAILABLE` | CRITICAL | Page on-call; check AWS connectivity & IAM |
| `KmsKeyDisabled` | KeyState is `Disabled` or `PendingDeletion` | CRITICAL | Page security team; halt relayer |
| `SignerIdentityMismatch` | Derived address != `EXPECTED_SIGNER_ADDRESS` | EMERGENCY | Halt relayer immediately; investigate key rotation |
| `NetworkMismatch` | Chain ID != 84532 or Mainnet detected | EMERGENCY | Hard halt; alert engineering leads |
| `SigningErrorRateHigh` | Signing failure rate > 1% in 5 min | HIGH | Investigate KMS throttling or malformed payloads |
| `RelayerStartupFailed` | Startup validation check failed | HIGH | Inspect startup logs for exact error code |

---

## 11. Structured Audit & Operational Logging

Operational events emit structured JSON log entries with distinct event categories:
* `kms_sign_attempt`
* `kms_sign_success`
* `kms_sign_failure`
* `relayer_startup_success`
* `relayer_startup_rejected`
* `signer_identity_mismatch`
* `signer_network_mismatch`

**Redaction Invariant:** Zero sensitive material (raw credentials, secret keys, private keys, Authorization headers) is ever logged. Only nonces, transaction hashes, key ARNs, and checksummed addresses are captured.

---

## 12. Production Configuration Validator

* **Tool:** [`scripts/verify_production_kms_configuration.py`](file:///Users/shravani/Desktop/AgentChain_AIWorkSpace/scripts/verify_production_kms_configuration.py)
* **Design:** Offline, deterministic validator (zero external AWS calls).
* **Checks:**
  1. Validates all 14 mandatory production environment variables.
  2. Validates KMS ARN format (`arn:aws:kms:<region>:<account>:key/<uuid>`).
  3. Validates AWS region syntax.
  4. Validates checksummed Ethereum addresses for all contracts and roles.
  5. Enforces separation between `RELAYER_ROLE_ADDRESS` and `ADMIN_ROLE_ADDRESS`.
  6. Rejects Anvil default accounts and historical retired developer accounts.
  7. Hard-rejects Base Mainnet (`8453`) and Anvil (`31337`).
  8. Hard-rejects raw private keys or mnemonic phrases.
* **Exit Codes:**
  - `0`: Valid production configuration.
  - `1`: Invalid configuration / security violation.
  - `2`: Prerequisites unavailable / template placeholders detected.

---

## 13. Failure-Injection & Operational Controls Test Suite

* **Test Module:** [`backend/tests/test_kms_operational_controls.py`](file:///Users/shravani/Desktop/AgentChain_AIWorkSpace/backend/tests/test_kms_operational_controls.py)
* **Test Count:** 22 tests (100% passed).
* **Scenarios Covered:**
  - `KMS_UNAVAILABLE` (network error / timeout) -> status reported explicitly.
  - `KMS_KEY_DISABLED` -> status reported explicitly, startup aborted.
  - `KMS_KEY_INVALID` (`RSA_2048`, `ECC_NIST_P256`, `ENCRYPT_DECRYPT`) -> status reported explicitly.
  - `SIGNER_IDENTITY_MISMATCH` (mismatched key / Anvil address) -> status reported explicitly.
  - `CONFIG_INVALID` (missing ARN / empty key reference) -> status reported explicitly.
  - `BlockchainRelayer.verify_startup()` fail-closed reporting on all unhealthy statuses.
  - Factory rejection of `LocalDevelopmentSigner` and `TestnetAccountSigner` in production.
  - Factory rejection of raw private keys even when passed alongside KMS configuration.
  - Mainnet hard block across all production factory paths.
  - Exit code contracts (0, 1, 2) of `verify_production_kms_configuration.py`.

---

## 14. Remaining Blockers

```text
LIVE AWS KMS PROVIDER VERIFICATION:
PENDING OPERATOR-SUPPLIED KMS KEY + AWS CREDENTIAL CHAIN
```

The repository code, security configuration, failure-injection tests, and operational runbooks are completely verified locally. Final production activation remains gated on the cloud operator supplying a live KMS Key ARN and ambient AWS credentials in a controlled environment.
