# AgentChain Security Findings Register
## Phase 6.11 & 6.11.1 — Security Audit Baseline & Remediation

**Document ID**: SEC-REG-6.11-001  
**Status**: ACTIVE / REMEDIATED AUDIT BASELINE / FROZEN RELEASE CANDIDATE  
**Date**: October 5, 2026  
**Auditor Classification**: Application Security, Blockchain Security, & Release Engineering  

---

### Executive Summary

As part of the Phase 6.11 Independent Security Audit Readiness and Phase 6.11.1 Security Findings Remediation, an exhaustive audit and remediation cycle was conducted across all 20 protocol domains.

All findings identified during static analysis, automated regression matrices, live chain inspections, and adversarial review are cataloged below with factual reproduction steps, impact assessments, explicit remediation evidence, and residual risks.

Concrete engineering findings (`SEC-6.11-002`, `SEC-6.11-003`, `SEC-6.11-007`) have been remediated locally and verified through automated test suites. Deprecation warnings (`SEC-6.11-010`) have been formally analyzed and safe upstream boundaries documented. External blockers (`SEC-6.11-001`) and testnet-scoped privileges (`SEC-6.11-004`) remain truthfully classified.

---

### Findings Summary Table

| Finding ID | Severity | Component | Title | Phase 6.11 Status | Phase 6.11.1 Status | Remediation Summary |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **SEC-6.11-001** | **HIGH** | Signer / KMS | Live AWS KMS Provider Verification Not Executed | ACCEPTED RISK / KNOWN BLOCKER | **OPEN / KNOWN BLOCKER** | Unaltered; ambient AWS credentials unavailable; production blocked |
| **SEC-6.11-002** | **HIGH** | Local Testing / Anvil | Local Anvil Ephemeral State Absence on Container Restart | RUNBOOK DOCUMENTED | **MITIGATED** | Automated deterministic Anvil bootstrap (`anvil_bootstrap.py`, 11/11 tests pass) |
| **SEC-6.11-003** | **MEDIUM** | Worker / Redis Streams | Redis Stream Consumer Group Cross-Talk in Async Test Runner | TEST ISOLATION | **MITIGATED** | Terminated rogue worker PID, raised socket timeouts to 5s, pre/post fixture cleanup |
| **SEC-6.11-004** | **MEDIUM** | Access Control / Governance | Testnet Operator Multi-Role Overlap (Deployer / Admin / Oracle) | ACCEPTED TESTNET RISK | **ACCEPTED TESTNET RISK / PROD BLOCKED** | Scoped strictly to Base Sepolia testnet; production role overlap blocked by gate 7 |
| **SEC-6.11-005** | **LOW** | Smart Contracts (Escrow) | Strict Equality on Enum Sentinel (`record.state == NONE`) | ACCEPTED RISK | **ACCEPTED RISK** | Formal false positive; zero-enum existence check protected by `nonReentrant` |
| **SEC-6.11-006** | **LOW** | Smart Contracts (Escrow) | Block Timestamp Dependency in Execution Deadline Checks | ACCEPTED RISK | **ACCEPTED RISK** | Intended L2 coarse-grained time-lock design (hours/days vs ~2s drift) |
| **SEC-6.11-007** | **LOW** | Backend API / Validation | Starlette Deprecation Warning on HTTP 422 Constant | RUNTIME INFORMATIONAL | **MITIGATED** | Replaced `HTTP_422_UNPROCESSABLE_ENTITY` with `HTTP_422_UNPROCESSABLE_CONTENT` across backend |
| **SEC-6.11-008** | **INFORMATIONAL** | Secrets / Config | Local Development `.env` Contains Testnet Private Key | MITIGATED | **MITIGATED** | Verified `.gitignore`, `.dockerignore`, and Git-tracked file scanner (Gate 1 & 2 PASS) |
| **SEC-6.11-009** | **INFORMATIONAL** | Smart Contracts (Build) | Pragma Directive Discrepancy with OpenZeppelin v5.x | ACCEPTED RISK | **ACCEPTED RISK** | Deterministic compiler pinning: `solc_version = "0.8.24"` satisfies `^0.8.20` |
| **SEC-6.11-010** | **INFORMATIONAL** | Orchestration (LangGraph) | LangChain Checkpoint Deprecation Warning | VENDOR UPSTREAM | **JUSTIFIED / ACCEPTED RISK** | Vendor API limitation (`allowed_objects` unsupported in v0.2.76); warning filtered; canonical JSON hashing preserved |

---

### Detailed Findings & Remediation Evidence

#### Finding SEC-6.11-001
- **ID**: `SEC-6.11-001`
- **SEVERITY**: **HIGH**
- **COMPONENT**: Signer Architecture / AWS KMS Adapter (`backend/app/services/blockchain/signer.py`, `scripts/verify_aws_kms_live.py`)
- **OLD STATUS**: `ACCEPTED RISK / KNOWN BLOCKER`
- **DESCRIPTION**: The production signer abstraction requires hardware security module custody via AWS KMS (`ECC_SECG_P256K1`). While mock KMS adapters, DER decoding, low-S normalization, recovery ID derivation, address binding, and operational pre-flight check scripts are fully implemented and verified via unit tests, live attestation against an active AWS KMS key has not been executed due to lack of ambient AWS infrastructure credentials.
- **IMPACT**: Production transactions cannot be safely broadcast without live hardware attestation. Fail-closed controls prevent relayer startup if KMS is unreachable or key metadata is invalid.
- **REMEDIATION**: Retain fail-closed gate. Require operator activation with an approved AWS KMS key ARN prior to any mainnet authorization.
- **TEST EVIDENCE**:
  - `backend/tests/test_aws_kms_signer.py` (43 passed)
  - `backend/tests/test_kms_operational_controls.py` (22 passed)
  - `backend/tests/test_phase_6_10_production_signer_mainnet_safety.py` (21 passed)
  - `.venv/bin/python3 scripts/verify_aws_kms_live.py` returns exit code 2: `[FAIL-CLOSED] Live AWS KMS prerequisites unavailable: Missing AWS_KMS_KEY_ARN. Result: LIVE AWS KMS VERIFICATION: NOT EXECUTED`.
- **NEW STATUS**: **OPEN / KNOWN BLOCKER**
- **RESIDUAL RISK**: System safely halts at startup without credentials; zero Mainnet transactions can occur.

---

#### Finding SEC-6.11-002
- **ID**: `SEC-6.11-002`
- **SEVERITY**: **HIGH**
- **COMPONENT**: Local Anvil / Integration Testing (`backend/tests/conftest.py`, `backend/tests/anvil_bootstrap.py`, `backend/tests/test_anvil_bootstrap.py`)
- **OLD STATUS**: `RUNBOOK DOCUMENTED`
- **DESCRIPTION**: Anvil runs inside a Docker container without persistent state storage. When the container restarts, deployed contract bytecodes revert to zero length (`0x`), requiring manual re-seeding via Foundry scripts.
- **IMPACT**: Local Anvil end-to-end integration tests fail when executed against a freshly restarted Anvil instance without manual intervention.
- **REMEDIATION**:
  1. Implemented `backend/tests/anvil_bootstrap.py` which:
     - Verifies Anvil connectivity and inspects bytecode.
     - Strictly enforces `chain_id == 31337`; throws `RuntimeError: SECURITY VIOLATION: Anvil bootstrap must never run on non-local chain` if invoked on Base Sepolia (84532), Base Mainnet (8453), or any foreign chain.
     - Deterministically seeds the chain using `Deploy.s.sol` with deterministic local Anvil account `0xac09...`.
     - Validates contract deployment addresses and bytecode presence.
  2. Integrated auto-seeding into pytest fixture (`ensure_anvil_seeded_fixture` in `backend/tests/conftest.py`).
  3. Created restart and safety regression suite `backend/tests/test_anvil_bootstrap.py`.
- **TEST EVIDENCE**:
  - `.venv/bin/pytest backend/tests/test_anvil_bootstrap.py -v` (11 passed):
    - `test_anvil_bootstrap_safety_guard_rejects_mainnet` (PASS)
    - `test_anvil_bootstrap_safety_guard_rejects_sepolia` (PASS)
    - `test_anvil_bootstrap_safety_guard_rejects_arbitrary_chain` (PASS)
    - `test_anvil_bootstrap_allows_chain_31337` (PASS)
    - `test_anvil_contracts_seeded_bytecode_present` (PASS)
    - `test_anvil_restart_simulation_and_deterministic_reseeding` (PASS)
    - `test_e2e_after_bootstrap_reseed` (PASS)
  - All local Anvil E2E suites passing: `test_distribution_e2e_anvil.py` (PASS), `test_notarization_e2e_anvil.py` (PASS), `test_reputation_e2e_anvil.py` (PASS).
- **NEW STATUS**: **MITIGATED**
- **RESIDUAL RISK**: None. Bootstrap is programmatically hard-blocked from ever executing outside chain ID 31337.

---

#### Finding SEC-6.11-003
- **ID**: `SEC-6.11-003`
- **SEVERITY**: **MEDIUM**
- **COMPONENT**: Worker Execution & Queue Recovery (`backend/tests/conftest.py`, `backend/app/services/rate_limiter.py`)
- **OLD STATUS**: `TEST ISOLATION`
- **DESCRIPTION**: In asynchronous testing environments executing 570+ test cases sequentially, Redis Stream consumer groups could experience message contention, stream offset exhaustion, or read timeouts.
- **IMPACT**: Tests executed in bulk could intermittently fail due to stolen messages or blocking timeouts.
- **REMEDIATION**:
  1. Identified and terminated an orphaned background worker process (PID 4829) running `agentchain_worker/main.py` that was stealing stream messages during test executions.
  2. Increased `socket_timeout` and `socket_connect_timeout` from 2.0s to 5.0s in `backend/app/services/rate_limiter.py` to prevent timeout collisions during 2000ms blocking reads.
  3. Hardened `backend/tests/conftest.py` `cleanup_db_connections` fixture to flush all Redis test keyspaces (`agentchain:*`, `rl:*`, `worker:*`, `test:*`) synchronously before and after every test function.
- **TEST EVIDENCE**:
  - High-contention suites (`test_queue_recovery.py`, `test_orchestration_engine.py`, `test_cancellation.py`) executed 3 consecutive times with zero failures:
    - Run 1: 13 passed in 13.44s
    - Run 2: 13 passed in 13.39s
    - Run 3: 13 passed in 13.26s
  - `backend/tests/test_agents.py` (5 passed in 1.15s)
  - `backend/tests/test_orchestration_recovery.py` (12 passed in 10.37s)
  - Full backend test suite: 587/587 passed in 158.46s.
- **NEW STATUS**: **MITIGATED**
- **RESIDUAL RISK**: None in production; production workers operate in separate pods with unique consumer IDs and stream partitions.

---

#### Finding SEC-6.11-004
- **ID**: `SEC-6.11-004`
- **SEVERITY**: **MEDIUM**
- **COMPONENT**: Access Control & Role Separation (`.env`, `contracts/script/Deploy.s.sol`)
- **OLD STATUS**: `ACCEPTED TESTNET RISK / PROD BLOCKED`
- **DESCRIPTION**: On the live Base Sepolia testnet (Phase 6.9 deployment), a single operator address (`0x473888C859F88D3De7b5A20986805b4dC3178189`) holds multiple privileged roles simultaneously: contract owner, deployer, admin, notarizer, and reputation oracle. While acceptable for automated testnet operations, this violates production least-privilege principles.
- **IMPACT**: Compromise of the testnet operator key permits arbitrary notarization, dispute resolution, and contract pausing on Base Sepolia.
- **REMEDIATION**: Strict isolation maintained. Production safety gate `scripts/verify_production_safety_gates.py` strictly blocks role overlap in production configuration: `relayer_role != admin_role != arbitrator_role`. The production KMS verification script requires distinct role assignments.
- **TEST EVIDENCE**:
  - `scripts/verify_production_safety_gates.py` Gate 7 PASS (`test_anvil_default_address_blocked_as_production_role`, `test_relayer_and_admin_cannot_be_same_address_in_production`).
  - Read-only RPC calls in `scripts/verify_base_sepolia_read_only.py` verify no unauthorized transactions occurred.
- **NEW STATUS**: **ACCEPTED TESTNET RISK / PROD BLOCKED**
- **RESIDUAL RISK**: Base Sepolia remains an active testing network; production deployment remains blocked until multi-sig and separate KMS keys are provisioned.

---

#### Finding SEC-6.11-005
- **ID**: `SEC-6.11-005`
- **SEVERITY**: **LOW**
- **COMPONENT**: Smart Contracts (`contracts/src/Escrow.sol#389`)
- **OLD STATUS**: `ACCEPTED RISK (FALSE POSITIVE)`
- **DESCRIPTION**: Slither static analyzer flags `dangerous-strict-equalities` on `record.state == EscrowState.NONE`.
- **IMPACT**: None. `EscrowState.NONE` is a zero-initialized enum value representing non-existence of an escrow.
- **REMEDIATION**: Formal classification as false positive. State machine transitions are strictly guarded by `nonReentrant` and modifier functions.
- **TEST EVIDENCE**: Documented in `docs/blockchain/SLITHER_ACCEPTED.md` Finding S1; 52 Escrow Foundry unit tests and 256 invariant runs passing.
- **NEW STATUS**: **ACCEPTED RISK**
- **RESIDUAL RISK**: None.

---

#### Finding SEC-6.11-006
- **ID**: `SEC-6.11-006`
- **SEVERITY**: **LOW**
- **COMPONENT**: Smart Contracts (`contracts/src/Escrow.sol#285`, `#407`)
- **OLD STATUS**: `ACCEPTED RISK (INTENDED L2 DESIGN)`
- **DESCRIPTION**: Slither flags `timestamp` usage in comparisons `block.timestamp < escrow.executionDeadline` and `executionDeadline <= block.timestamp`.
- **IMPACT**: Minor timestamp drift (~1-2 seconds on L2 Base sequencer) cannot be leveraged to prematurely trigger refund deadlines designed for multi-hour or multi-day agent task executions.
- **REMEDIATION**: Standard pattern in Ethereum/L2 time-locks (OpenZeppelin standard). Execution deadlines are coarse-grained (hours/days).
- **TEST EVIDENCE**: Documented in `docs/blockchain/SLITHER_ACCEPTED.md` Finding S3 and S4; 52 Escrow Foundry tests passing.
- **NEW STATUS**: **ACCEPTED RISK**
- **RESIDUAL RISK**: Sequencer drift is bounded to Ethereum timestamp consensus rules.

---

#### Finding SEC-6.11-007
- **ID**: `SEC-6.11-007`
- **SEVERITY**: **LOW**
- **COMPONENT**: Backend API / Exception Handling (`backend/app/api/v1/agents.py`, `backend/app/api/v1/orchestrations.py`, `backend/app/services/agent_execution.py`, `backend/app/services/agent_registry.py`, `backend/app/api/v1/settlements.py`)
- **OLD STATUS**: `RUNTIME INFORMATIONAL`
- **DESCRIPTION**: Upstream Starlette/FastAPI emitted deprecation warnings: `StarletteDeprecationWarning: 'HTTP_422_UNPROCESSABLE_ENTITY' is deprecated. Use 'HTTP_422_UNPROCESSABLE_CONTENT' instead`.
- **IMPACT**: Deprecation warning spam during test execution and potential future incompatibility upon Starlette minor version bump.
- **REMEDIATION**: Replaced all occurrences of `status.HTTP_422_UNPROCESSABLE_ENTITY` with `status.HTTP_422_UNPROCESSABLE_CONTENT` across all backend API and service modules. Verified zero instances remain in `backend/app`.
- **TEST EVIDENCE**:
  - `backend/tests/test_agents.py` (5 passed)
  - `backend/tests/test_orchestration_recovery.py` (12 passed)
  - Zero `StarletteDeprecationWarning` emitted during test execution.
- **NEW STATUS**: **MITIGATED**
- **RESIDUAL RISK**: None.

---

#### Finding SEC-6.11-008
- **ID**: `SEC-6.11-008`
- **SEVERITY**: **INFORMATIONAL**
- **COMPONENT**: Secrets Management (`.env`, `.gitignore`, `.dockerignore`)
- **OLD STATUS**: `MITIGATED`
- **DESCRIPTION**: Local `.env` file contains the Base Sepolia testnet operator private key (`DEPLOYER_PRIVATE_KEY=703f...`). If committed to git or copied into a container image, this key would be exposed.
- **IMPACT**: Potential compromise of testnet funds (~0.001 Sepolia ETH) and testnet contracts if leaked.
- **REMEDIATION**: Multi-layer defense:
  1. `.gitignore` strictly excludes `.env`, `.env.*`, `*.key`, `*.pem`, `*.keystore`, `.dev_wallet.txt`.
  2. `.dockerignore` strictly excludes `.env*`, `*.key`, `*.pem`.
  3. CI pipeline executes `scripts/verify_production_safety_gates.py` fail-fast on every PR.
- **TEST EVIDENCE**: `scripts/verify_production_safety_gates.py` Gate 1 & 2 PASS. Git tracked files check returns 0 secret files.
- **NEW STATUS**: **MITIGATED**
- **RESIDUAL RISK**: Developer hygiene required to avoid pasting `.env` contents into issue tickets.

---

#### Finding SEC-6.11-009
- **ID**: `SEC-6.11-009`
- **SEVERITY**: **INFORMATIONAL**
- **COMPONENT**: Smart Contract Build System (`contracts/foundry.toml`, `contracts/src/*.sol`)
- **OLD STATUS**: `ACCEPTED RISK (SOLC 0.8.24 PINNED)`
- **DESCRIPTION**: Slither flags `different-pragma-directives` because AgentChain production contracts pin `pragma solidity 0.8.24;` while OpenZeppelin library contracts specify `pragma solidity ^0.8.20;`.
- **IMPACT**: None. Foundry compiles all code under Solidity 0.8.24, fulfilling the open upper bound of OpenZeppelin.
- **REMEDIATION**: Consistent compilation enforced in `foundry.toml` (`solc_version = "0.8.24"`).
- **TEST EVIDENCE**: `forge test` (122 tests passed).
- **NEW STATUS**: **ACCEPTED RISK**
- **RESIDUAL RISK**: None.

---

#### Finding SEC-6.11-010
- **ID**: `SEC-6.11-010`
- **SEVERITY**: **INFORMATIONAL**
- **COMPONENT**: Orchestration Engine (`backend/app/services/orchestration/checkpoint.py`, `backend/pyproject.toml`)
- **OLD STATUS**: `VENDOR UPSTREAM`
- **DESCRIPTION**: LangChain / LangGraph library logs pending deprecation warning: `LangChainPendingDeprecationWarning: The default value of 'allowed_objects' will change in a future version. Pass an explicit value (e.g., allowed_objects='messages' or allowed_objects='core') to suppress this warning.`
- **IMPACT**: Informational warning during checkpoint serialization. Does not compromise checkpoint data integrity or state hashes.
- **REMEDIATION**: Evaluated passing `allowed_objects` parameter to `JsonPlusSerializer`. In the pinned `langgraph==0.2.76` and `langchain_core` version, passing `allowed_objects` raises `TypeError: got an unexpected keyword argument 'allowed_objects'`. Added clean warning filter in `backend/pyproject.toml` to suppress benign upstream pending deprecation warnings while retaining RFC 8785 canonical JSON serialization with PostgreSQL checkpoint verification.
- **TEST EVIDENCE**:
  - `backend/tests/test_orchestration_engine.py` (5 passed)
  - `backend/tests/test_durable_wakeups.py` (6 passed)
  - Checkpoint integrity and PostgreSQL serialization tests all green.
- **NEW STATUS**: **JUSTIFIED / ACCEPTED RISK**
- **RESIDUAL RISK**: Upstream vendor dependency will require parameter update upon major LangGraph release.

---
