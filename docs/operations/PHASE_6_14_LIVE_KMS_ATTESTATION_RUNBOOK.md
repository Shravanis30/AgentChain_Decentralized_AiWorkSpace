# Phase 6.14: Live AWS KMS Attestation Runbook

**Document ID**: AGY-OPS-6.14-KMS-RUNBOOK  
**Phase**: 6.14 — Production Operator Activation Readiness & Governance Execution Package  
**Release Candidate Version**: `1.1.0-rc1`  
**Classification**: OPERATIONAL PROCEDURAL RUNBOOK  
**Release Freeze Status**: **STRICTLY FROZEN**  
**Date**: October 5, 2026  

---

## 1. Important Safety Directives

> [!CAUTION]
> **ABSOLUTE CREDENTIAL AND KEY ISOLATION RULES**:
> 1. Private key material must NEVER be exported from AWS KMS hardware.
> 2. AWS credentials (access keys, secret keys, session tokens) must NEVER be committed to git or logged in repository files.
> 3. Application code NEVER touches raw private keys in production; all signing operations occur inside the HSM.
> 4. Zero blockchain transactions are broadcast during this attestation runbook.

---

## 2. Operator Execution Sequence

This sequence must be performed by an authorized infrastructure operator in a secure terminal session:

### Step 1: Provision AWS KMS Asymmetric Key (Out-of-Band)
Using the AWS Management Console or Terraform:
- Key Type: **Asymmetric**
- Key Spec: **`ECC_SECG_P256K1`**
- Key Usage: **`SIGN_VERIFY`**
- Key Origin: **AWS_KMS (HSM)**

### Step 2: Validate Key Specification & State
Run AWS CLI to confirm parameters:
```bash
aws kms describe-key --key-id <YOUR_KEY_ARN> --region <YOUR_REGION>
```
Verify the output contains:
```json
{
  "KeyMetadata": {
    "KeySpec": "ECC_SECG_P256K1",
    "KeyUsage": "SIGN_VERIFY",
    "KeyState": "Enabled"
  }
}
```

### Step 3: Extract Public Key and Determine Ethereum Signer Address
Inspect the public key using AWS CLI and derive the checksum address:
```bash
aws kms get-public-key --key-id <YOUR_KEY_ARN> --region <YOUR_REGION>
```
Compute the uncompressed 64-byte public key ($X, Y$) from SPKI ASN.1, hash with Keccak-256, and take the last 20 bytes.

### Step 4: Governance Approval of Expected Signer Identity
Present the derived Ethereum checksum address to the Multi-Sig Governance Council. Record formal approval in governance meeting minutes.

### Step 5: Configure Operator Environment Variables
In your local, non-persistent shell session (DO NOT commit to `.env`):
```bash
export AWS_REGION="us-east-1"
export AWS_KMS_KEY_ARN="arn:aws:kms:us-east-1:123456789012:key/your-key-uuid"
export EXPECTED_SIGNER_ADDRESS="0xYourApprovedDerivedAddress"
# Authenticate using AWS SSO or temporary IAM role session
aws sso login --profile agentchain-relayer
export AWS_PROFILE="agentchain-relayer"
```

### Step 6: Execute Live KMS Verification Script
Run the authoritative attestation script:
```bash
.venv/bin/python3 scripts/verify_aws_kms_live.py
```

### Step 7: Verify Output & Capture Evidence
Expected successful execution:
- DescribeKey: PASS (`ECC_SECG_P256K1`, `SIGN_VERIFY`, `Enabled`)
- GetPublicKey: PASS (SPKI extracted, public key parsed)
- Address Derivation: PASS (Matches `EXPECTED_SIGNER_ADDRESS`)
- Sign: PASS (DER ASN.1 decoded, low-S normalized, recovery ID $v \in \{27, 28\}$ calculated)
- Verify: PASS (Local public key recovery recovers identical address)
- Exit code: **0**

### Step 8: Classify and Record Evidence
- Evidence Classification: **`LIVE — EXTERNAL AWS KMS ATTESTATION`**
- Save redacted log (with key UUID and region, but NO credentials or raw signatures) to `docs/phases/phase-6.14-live-evidence.json`.
- Update `PHASE_6_14_OPERATOR_ACTIVATION_MATRIX.md` to reflect GATE-12 status as `PASS`.
