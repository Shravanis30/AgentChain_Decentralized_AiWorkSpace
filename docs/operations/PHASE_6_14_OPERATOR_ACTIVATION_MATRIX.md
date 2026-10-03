# Phase 6.14: Production Operator Activation Matrix

**Document ID**: AGY-OPS-6.14-MATRIX  
**Phase**: 6.14 — Production Operator Activation Readiness & Governance Execution Package  
**Release Candidate Version**: `1.1.0-rc1`  
**Release Fingerprint**: `12d02ae0a6ffb65a84957af57574b35fdde78abf09b4abd69818ec937a9abce5`  
**Classification**: OPERATOR RUNBOOK & GOVERNANCE PREREQUISITE MATRIX  
**Release Freeze Status**: **STRICTLY FROZEN**  
**Date**: October 5, 2026  

---

## 1. Executive Purpose

This activation matrix defines the exact operator-owned prerequisites, required authorities, execution commands, and evidence production rules for all remaining release gates identified in Phase 6.13.

No gate in this matrix can be cleared by automated CI/CD pipelines or AI agents without explicit human operator credentials and governance authority.

---

## 2. Master Operator Activation Matrix

| Gate ID & Name | Required Input | Required Authority | Current State | Execution Command / Procedure | Evidence Produced | Release Effect |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **GATE-12: Live AWS KMS Attestation** | 1. Active AWS KMS Key ARN (`ECC_SECG_P256K1`, `SIGN_VERIFY`, `Enabled`)<br>2. AWS Region (e.g. `us-east-1`)<br>3. Ambient AWS credentials (IAM role/SSO session)<br>4. Governance-approved `EXPECTED_SIGNER_ADDRESS` | Infrastructure Operator | **NOT EXECUTED** (Exit code 2 fail-closed) | `python3 scripts/verify_aws_kms_live.py` | `LIVE — EXTERNAL AWS KMS ATTESTATION`<br>(Stdout log, exit code 0, signature verification record) | Unlocks hardware custody verification; required for Production KMS Custody gate. |
| **GATE-13: Production KMS Custody** | 1. Validated KMS Key ARN<br>2. Derived Ethereum address = `EXPECTED_SIGNER_ADDRESS`<br>3. Production configuration populated in secure secret store<br>4. Hardware Key Policy enforcing least privilege | Security Architect / Cloud Custody Lead | **BLOCKED** (Pending infra provisioning) | `python3 scripts/verify_production_kms_configuration.py` | `STATIC — INSPECTED`<br>(Exit code 0, non-secret configuration audit) | Enables production relayer startup; prerequisites for live deployment authorization. |
| **GATE-13B: Production Role Separation** | 1. Distinct `RELAYER_ROLE_ADDRESS`<br>2. Distinct `ADMIN_ROLE_ADDRESS`<br>3. Distinct `ARBITRATOR_ROLE_ADDRESS`<br>4. Invariant: `RELAYER != ADMIN != ARBITRATOR`<br>5. No Anvil default accounts | Multi-Sig Governance Council | **BLOCKED** (Addresses marked `NOT ASSIGNED`) | `python3 scripts/verify_phase_6_14_operator_prerequisites.py` | `STATIC — INSPECTED`<br>(Role separation verified by Gate 7) | Eliminates single-key failure mode; enforces separation between operations and administration. |
| **GATE-14: Independent External Audit** | 1. Engagement of independent external security audit firm<br>2. Auditor review of `AGENTCHAIN_EXTERNAL_AUDIT_PACKAGE.md`<br>3. Formal audit report delivery<br>4. Reconciliation of all findings<br>5. Formal auditor sign-off | External Security Audit Firm | **OPEN** (Audit dossier ready; review pending) | External audit execution per `PHASE_6_14_EXTERNAL_AUDIT_EXECUTION_PROCEDURE.md` | `STATIC — INSPECTED`<br>(Signed third-party audit report, formal sign-off letter) | Confirms mathematical invariants, smart contract security, and operational failure modes. |
| **GATE-15: Mainnet Governance Approval** | 1. Completed audit sign-off (GATE-14)<br>2. Successful live KMS attestation (GATE-12)<br>3. Verified role separation (GATE-13B)<br>4. Formal Multi-Sig Governance proposal & vote | Protocol DAO / Governance Multi-Sig | **BLOCKED** (Awaiting audit & KMS prerequisites) | On-chain multi-sig proposal submission and quorum execution | `STATIC — INSPECTED`<br>(Signed governance transaction hash, voting quorum record) | Authorizes protocol activation; mandatory prerequisite for Mainnet deployment. |
| **GATE-16: Base Mainnet Deployment** | 1. Governance authorization (GATE-15)<br>2. Production relayer running with KMS signer<br>3. Gas funding on Base Mainnet<br>4. Execution of deployment script via authorized multi-sig | Protocol Deployment Officer | **STRICTLY BLOCKED** (Hard-coded safety blocks active) | Controlled multi-sig execution of deployment scripts | `LIVE — EXTERNAL CHAIN TRANSACTION`<br>(Canonical contract deployment tx hashes on Chain ID 8453) | Deploys protocol contracts to Base Mainnet; establishes production settlement network. |

---

## 3. Operational Invariants for All Gates

1. **Anti-Promotion Rule**: Local test evidence must NEVER be promoted to external evidence. A passing mock test is classified as `LOCAL — EXECUTED`, never `LIVE — EXTERNAL AWS KMS ATTESTATION`.
2. **Anti-Fabrication Rule**: No auditor finding, approval, signature, or address may be synthesized. Absent items must remain `NOT RECEIVED` or `NOT ASSIGNED`.
3. **Fail-Closed Verification**: All validator scripts (`verify_aws_kms_live.py`, `verify_production_kms_configuration.py`, `verify_phase_6_14_operator_prerequisites.py`) must exit with code 2 (prerequisites unavailable) or code 1 (failure) when configuration is incomplete.
4. **Credential Isolation**: No private keys or AWS secret access keys may ever be committed to git, logged to stdout/stderr, or stored in application source trees.
