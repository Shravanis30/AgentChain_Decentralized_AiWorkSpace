#!/usr/bin/env python3
"""AgentChain Phase 6.14: Operator Activation Prerequisites Validator.

Purpose:
Performs strictly read-only inspection of operator environment, KMS readiness,
role separation, release fingerprint integrity, and Mainnet hard block status.

Invariants:
1. READ-ONLY: Never creates transactions, AWS resources, or modifies state.
2. FAIL-CLOSED: Exits with code 2 if operator prerequisites are unpopulated.
3. FAIL-CLOSED: Exits with code 1 on security regression or Mainnet violation.
4. Exits with code 0 ONLY if all operator prerequisites and governance inputs are met.
"""

import os
import sys
from pathlib import Path

# Add project root and backend to Python path
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "backend"))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from app.services.blockchain.config import (
    CHAIN_ID_ANVIL,
    CHAIN_ID_BASE_MAINNET,
    CHAIN_ID_BASE_SEPOLIA,
)

FROZEN_RELEASE_FINGERPRINT = "12d02ae0a6ffb65a84957af57574b35fdde78abf09b4abd69818ec937a9abce5"


def check_operator_prerequisites() -> int:
    print("=" * 70)
    print(" AgentChain Phase 6.14: Operator Activation Prerequisites Scanner")
    print("=" * 70)

    missing_prereqs = []
    status_notes = []

    # 1. Release Fingerprint Check
    try:
        from compute_release_fingerprint import compute_fingerprint
        fp_res = compute_fingerprint()
        computed_fp = fp_res["release_fingerprint"]
        if computed_fp == FROZEN_RELEASE_FINGERPRINT:
            print(f"[PASS] Release Fingerprint: {computed_fp[:16]}... (Zero Drift)")
        else:
            print(f"[FAIL] Release Fingerprint Drift: expected {FROZEN_RELEASE_FINGERPRINT}, got {computed_fp}")
            return 1
    except Exception as exc:
        print(f"[FAIL] Could not verify release fingerprint: {exc}")
        return 1

    # 2. Mainnet Hard Block Check
    chain_id_env = os.environ.get("CHAIN_ID", "").strip()
    if chain_id_env == str(CHAIN_ID_BASE_MAINNET) or chain_id_env == "8453":
        print("[CRITICAL BLOCK] CHAIN_ID is set to Base Mainnet (8453). Mainnet is strictly hard-blocked!")
        return 1
    else:
        print(f"[PASS] Mainnet Hard Block: Chain ID is '{chain_id_env or 'not set'}' (8453 prohibited)")

    # 3. AWS KMS Key Configuration Check
    kms_arn = (os.environ.get("AWS_KMS_KEY_ARN") or os.environ.get("SIGNER_KEY_REFERENCE") or "").strip()
    region = (os.environ.get("AWS_REGION") or "").strip()
    expected_signer = (os.environ.get("EXPECTED_SIGNER_ADDRESS") or "").strip()

    if not kms_arn:
        missing_prereqs.append("AWS_KMS_KEY_ARN (KMS key reference ARN)")
    else:
        status_notes.append(f"KMS Key ARN configured: {kms_arn.split(':')[-1] if ':' in kms_arn else kms_arn}")

    if not region:
        missing_prereqs.append("AWS_REGION (e.g. us-east-1)")
    else:
        status_notes.append(f"AWS Region: {region}")

    if not expected_signer:
        missing_prereqs.append("EXPECTED_SIGNER_ADDRESS (Derived Ethereum checksum address)")
    else:
        status_notes.append(f"Expected Signer: {expected_signer}")

    # Check ambient credentials without logging secrets
    try:
        import boto3
        session = boto3.Session(region_name=region or "us-east-1")
        creds = session.get_credentials()
        if creds is None or not getattr(creds, "access_key", None):
            missing_prereqs.append("Ambient AWS credentials (boto3 provider chain)")
        else:
            status_notes.append("AWS Credentials: Resolved via ambient provider chain")
    except Exception as exc:
        missing_prereqs.append(f"AWS Credential error: {exc}")

    # 4. Production Role Separation Check
    relayer_role = (os.environ.get("RELAYER_ROLE_ADDRESS") or "").strip()
    admin_role = (os.environ.get("ADMIN_ROLE_ADDRESS") or "").strip()
    arbitrator_role = (os.environ.get("ARBITRATOR_ROLE_ADDRESS") or "").strip()

    if not relayer_role:
        missing_prereqs.append("RELAYER_ROLE_ADDRESS")
    if not admin_role:
        missing_prereqs.append("ADMIN_ROLE_ADDRESS")
    if not arbitrator_role:
        missing_prereqs.append("ARBITRATOR_ROLE_ADDRESS")

    if relayer_role and admin_role and relayer_role.lower() == admin_role.lower():
        print(f"[FAIL] Role Separation Violation: RELAYER ({relayer_role}) equals ADMIN ({admin_role})")
        return 1

    if relayer_role and arbitrator_role and relayer_role.lower() == arbitrator_role.lower():
        print(f"[FAIL] Role Separation Violation: RELAYER ({relayer_role}) equals ARBITRATOR ({arbitrator_role})")
        return 1

    if admin_role and arbitrator_role and admin_role.lower() == arbitrator_role.lower():
        print(f"[FAIL] Role Separation Violation: ADMIN ({admin_role}) equals ARBITRATOR ({arbitrator_role})")
        return 1

    # 5. External Audit Intake Check
    audit_files = list(REPO_ROOT.glob("docs/audit/*report*")) + list(REPO_ROOT.glob("docs/audit/*finding*"))
    has_external_report = any(f.name != "PHASE_6_13_AUDIT_FINDINGS_RECONCILIATION.md" for f in audit_files)
    if not has_external_report:
        missing_prereqs.append("External Auditor Signed Report (Pending independent external review)")
    else:
        status_notes.append("External Audit: Report artifact present")

    print("\n--- Current Configuration Notes ---")
    for note in status_notes:
        print(f"  * {note}")

    print("\n--- Gate Status Evaluation ---")
    if missing_prereqs:
        print("\n[STATUS: BLOCKED — OPERATOR INPUT REQUIRED]")
        print("Missing or unprovisioned operator prerequisites:")
        for item in missing_prereqs:
            print(f"  [-] {item}")
        print("\nAll operator activation prerequisites remain fail-closed.")
        return 2

    print("\n[STATUS: PASS — ALL OPERATOR PREREQUISITES POPULATED]")
    return 0


if __name__ == "__main__":
    sys.exit(check_operator_prerequisites())
