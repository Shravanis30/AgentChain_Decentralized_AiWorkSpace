# AWS KMS Production Key Rotation Runbook

## 1. Objective & Invariants

This runbook specifies the mandatory multi-step operational procedure for rotating an active AWS KMS production signing key in AgentChain.

### Core Invariants
1. **No Silent Identity Substitution**: Every AWS KMS key produces a unique secp256k1 public key and derived Ethereum address. Rotating the KMS key changes the signer's on-chain address.
2. **Dual-Control Governance Approval**: No new key can be adopted without formal governance review and sign-off on the derived Ethereum address.
3. **Fail-Closed Continuity**: If `AWS_KMS_KEY_ARN` is updated without updating `EXPECTED_SIGNER_ADDRESS`, the system immediately fails closed with `SignerIdentityMismatchError`.
4. **No Automatic In-Flight Rotation**: Key rotation is an operator-controlled, phased event with explicit on-chain role migration.

---

## 2. End-to-End Key Rotation Lifecycle

```text
Step 1: Out-of-Band Key Provisioning (Provisioning Admin)
        - Provision NEW KMS key: ECC_SECG_P256K1, SIGN_VERIFY
        ↓
Step 2: Address Derivation & Verification
        - python3 scripts/verify_aws_kms_live.py (dry run)
        - Extract derived Ethereum address (NEW_SIGNER_ADDRESS)
        ↓
Step 3: Governance Review & Multi-Sig Role Grants (Governance Authority)
        - Grant RELAYER_ROLE to NEW_SIGNER_ADDRESS on contracts
        - Verify coexistence window (both OLD and NEW addresses authorized)
        ↓
Step 4: Production Configuration Update
        - Update AWS_KMS_KEY_ARN = <NEW_KMS_KEY_ARN>
        - Update EXPECTED_SIGNER_ADDRESS = <NEW_SIGNER_ADDRESS>
        ↓
Step 5: Preflight Startup Validation
        - Relayer verifies startup with new key: health_check() == True
        - Address identity binding verified
        ↓
Step 6: Traffic Switch & Operational Verification
        - Route transaction intents through new signer
        - Monitor first 10 transactions for confirmation
        ↓
Step 7: Old Signer Decommissioning & Role Revocation
        - Revoke RELAYER_ROLE from OLD_SIGNER_ADDRESS on-chain
        - Schedule deletion of OLD KMS key with 30-day safety grace window
```

---

## 3. Detailed Step-by-Step Execution

### Step 1: Out-of-Band Key Provisioning
The Provisioning Administrator provisions a replacement asymmetric key in AWS KMS:
- Key Spec: `ECC_SECG_P256K1`
- Key Usage: `SIGN_VERIFY`
- Key State: `Enabled`
- Description: `AgentChain Relayer Signer (Rotated 202X-XX-XX)`

### Step 2: Address Derivation
The operator inspects the new key and derives the checksummed Ethereum address:
```bash
export AWS_KMS_KEY_ARN=<NEW_KMS_KEY_ARN>
export AWS_REGION=us-east-1
.venv/bin/python3 scripts/verify_aws_kms_live.py
```
Record the derived Ethereum address: `NEW_SIGNER_ADDRESS`.

### Step 3: Governance Approval & Dual-Authorization
1. Present the derived `NEW_SIGNER_ADDRESS` to the multi-sig administrative council.
2. Verify that `NEW_SIGNER_ADDRESS` is not in `BLOCKED_PRODUCTION_ADDRESSES`.
3. Submit on-chain transaction from `ADMIN_ROLE` granting necessary operational permissions to `NEW_SIGNER_ADDRESS`.
4. Do NOT revoke old permissions yet.

### Step 4: Atomic Configuration Update
Update production environment configuration (via AWS Secrets Manager / HashiCorp Vault):
```env
AWS_KMS_KEY_ARN=<NEW_KMS_KEY_ARN>
EXPECTED_SIGNER_ADDRESS=<NEW_SIGNER_ADDRESS>
```
*Note*: Updating `AWS_KMS_KEY_ARN` without updating `EXPECTED_SIGNER_ADDRESS` will intentionally cause relayer startup to fail with `SignerIdentityMismatchError`. Both must be updated in lockstep.

### Step 5: Startup Verification
Restart the relayer process and verify clean preflight startup:
```bash
.venv/bin/python3 scripts/verify_production_kms_configuration.py
```
Ensure `relayer.verify_startup()` reports healthy binding to `NEW_SIGNER_ADDRESS`.

### Step 6: On-Chain Decommissioning of Old Key
1. Confirm that all pending transactions signed by `OLD_SIGNER_ADDRESS` have reached finality (32 confirmation blocks on Base Sepolia).
2. Execute governance transaction revoking roles from `OLD_SIGNER_ADDRESS`.
3. Schedule deletion of the old KMS key in AWS with a 30-day waiting period:
   - Status transitions to `PendingDeletion`.
   - Never use immediate deletion.

---

## 4. Rollback Procedure

If issues arise before Step 6:
1. Revert `AWS_KMS_KEY_ARN` to `<OLD_KMS_KEY_ARN>`.
2. Revert `EXPECTED_SIGNER_ADDRESS` to `<OLD_SIGNER_ADDRESS>`.
3. Restart relayer service; old signer resumes immediately since old on-chain roles were not yet revoked.
4. Revoke pending roles from `NEW_SIGNER_ADDRESS`.
5. Post-mortem investigation.
