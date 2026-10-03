# AWS KMS Production Emergency Response & Break-Glass Runbook

## 1. Trigger Conditions & Immediate Actions

This incident response runbook governs operational procedures when the production signer must be halted immediately due to:
- Suspected AWS IAM compromise or unauthorized KMS API calls.
- Abnormal transaction submission patterns or rogue transaction intents.
- Smart contract exploit requiring immediate operational pause.
- Critical configuration drift or identity mismatch alerts.

---

## 2. Emergency Key Halt Procedure (10 Steps)

```text
[TRIGGER: SUSPECTED ANOMALY / COMPROMISE]
                   │
                   ▼
Step 1: Immediate Relayer Service Pause (SIGTERM / Systemd stop)
                   │
                   ▼
Step 2: Operational Disablement in AWS (Provisioning Admin)
        - Disable KMS Key: aws kms disable-key --key-id <KMS_KEY_ID>
        - (Or detach IAM policy from runtime role)
                   │
                   ▼
Step 3: Verification of Fail-Closed State
        - Verify relayer.verify_startup() raises KMS_KEY_DISABLED
        - Confirm zero subsequent signatures can be generated
                   │
                   ▼
Step 4: Smart Contract Circuit Breaker (Pauser Role)
        - Invoke Escrow.pause() / ResultNotary.pause() if needed
                   │
                   ▼
Step 5: Evidence Preservation
        - Snapshot AWS CloudTrail logs for KMS key ARN
        - Snapshot Relayer audit logs, Redis queues, and DB intent records
                   │
                   ▼
Step 6: Security Investigation & Blast-Radius Assessment
        - Verify all on-chain transactions submitted by signer
        - Confirm whether unauthorized transactions were confirmed
                   │
                   ▼
Step 7: Provision Clean Replacement KMS Key (Out-of-Band)
        - Provision replacement KMS key in clean IAM environment
                   │
                   ▼
Step 8: Governance Sign-off & Address Derivation
        - Derive replacement Ethereum address
        - Multi-sig review and on-chain role grant
                   │
                   ▼
Step 9: Atomic Configuration Update
        - Update AWS_KMS_KEY_ARN and EXPECTED_SIGNER_ADDRESS in Vault
                   │
                   ▼
Step 10: Staged Resumption & Release Sign-Off
        - Execute verify_production_kms_configuration.py
        - Unpause contracts and restart relayer
```

---

## 3. Break-Glass Principles & Fallback Boundaries

### Non-Negotiable Invariants
1. **NO RAW PRIVATE KEY FALLBACK**: The production application **CANNOT and MUST NOT** fall back to in-memory private keys, local dev keys, or `.dev_wallet.txt`. If KMS is disabled, the relayer MUST fail closed.
2. **Break-Glass is Strictly Out-of-Band**: Break-glass operational power resides solely in AWS IAM administrative accounts and multi-sig contract governance. Application runtime code has zero awareness of break-glass credentials.
3. **Time-Bounded & Auditable**: Any temporary escalation must use AWS STS temporary credentials with a maximum session duration of 1 hour, emitting CloudTrail audit events.
4. **Mandatory Post-Incident Review**: Following any break-glass activation, a root-cause analysis (RCA) must be published before the key deletion grace period expires.
