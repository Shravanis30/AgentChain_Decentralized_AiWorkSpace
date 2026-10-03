# AgentChain — Escrow Smart Contract Threat Model

## 1. Overview

This document presents a structured security analysis and threat model for the AgentChain `Escrow` smart contract implemented in Phase 6.1. The threat model follows STRIDE and defense-in-depth principles, addressing on-chain vulnerabilities, economic attacks, access control failures, token risks, and network-level anomalies.

---

## 2. Threat Analysis Matrix

### 2.1 Unauthorized Release
* **Threat:** A malicious actor (e.g. stranger, untrusted caller, or worker) attempts to release escrow funds to themselves or an arbitrary address.
* **Impact:** Direct theft of client funds.
* **Mitigation:** `releaseEscrow` enforces strict authorization checks. Only the client (`escrow.client`) or the platform arbitrator (`ARBITRATOR_ROLE`) may release funds. The payout recipient is immutably set to `escrow.beneficiary`. Beneficiaries explicitly cannot call `releaseEscrow` to release funds to themselves.
* **Residual Risk:** Compromise of client private key or arbitrator multisig keys (analyzed below).

### 2.2 Unauthorized Refund
* **Threat:** A client attempts to refund escrowed funds after work has begun, or a stranger attempts to trigger an unauthorized refund.
* **Impact:** Loss of compensation for agents/developers who performed work; griefing attacks.
* **Mitigation:** When an escrow is in `LOCKED` status, the client cannot refund until `block.timestamp >= executionDeadline`. Prior to the deadline, only the beneficiary (voluntary forfeiture) or the arbitrator can authorize refunds. Strangers are rejected with `UnauthorizedCaller`.
* **Residual Risk:** Client setting an unrealistically short execution deadline before task assignment; mitigated by backend validation of deadline thresholds during task creation.

### 2.3 Double Payment / Replay
* **Threat:** An attacker re-executes `releaseEscrow`, `refundEscrow`, or `resolveDispute` to withdraw funds repeatedly.
* **Impact:** Drain of other users' escrow balances from the contract.
* **Mitigation:** State machine transitions to terminal states (`RELEASED`, `REFUNDED`, `RESOLVED`) before token transfers. Any subsequent call evaluates `escrow.state` and immediately reverts with `InvalidEscrowState`.
* **Residual Risk:** None. The state transitions are atomic and terminal.

### 2.4 Replay Across Chains / Identifiers
* **Threat:** An escrow creation transaction or authorization hash is replayed on another chain (e.g., Base Sepolia to Base Mainnet) or across contract deployments.
* **Impact:** Unexpected escrow creation or collision.
* **Mitigation:** `computeEscrowId` incorporates `block.chainid` and `address(this)`, creating strict cryptographic domain separation across EVM chains and contract instances.
* **Residual Risk:** None on standard EVM chains adhering to EIP-1344.

### 2.5 Reentrancy Attack
* **Threat:** A malicious recipient or ERC-20 token executes a fallback/transfer callback to re-enter `Escrow` before balance accounting updates.
* **Impact:** Contract draining via reentrancy.
* **Mitigation:** 
  1. Adherence to Checks-Effects-Interactions: state changes and `totalEscrowBalance` decrements occur strictly before external token calls.
  2. OpenZeppelin `ReentrancyGuard` (`nonReentrant` modifier) applied to all external fund-moving functions (`createAndFundEscrow`, `fundEscrow`, `releaseEscrow`, `refundEscrow`, `resolveDispute`).
* **Residual Risk:** Verified via automated mock reentrancy exploit tests in Foundry (`test_Reentrancy_GuardBlocksReentry`).

### 2.6 Malicious ERC-20 Behavior (Fee-on-Transfer, Rebasing, Non-Standard Return)
* **Threat:** Integration of tokens with unorthodox mechanics that silently deduct fees, dynamically rebase balances, or fail to return a boolean.
* **Impact:** Contract accounting mismatch, insolvency, or locked funds.
* **Mitigation:**
  1. Contract restricts payment token exclusively to native Circle USDC via immutable `paymentToken`.
  2. SafeERC20 wraps all token interactions.
  3. No untrusted user-supplied token addresses are accepted.
* **Residual Risk:** Low. Circle USDC does not implement fee-on-transfer or rebasing. Circle retains contract blacklist capability under legal compliance.

### 2.7 Incorrect Token Address / Wrong Network
* **Threat:** Contract is deployed on Base pointing to bridged USDbC (`0xd9aAEc86B65D86f6A7B5B1b0c42FFA531710b6CA`) instead of native USDC (`0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913`), or testnet address used in production, or deployment script bypassed.
* **Impact:** Liquidity fragmentation or total loss of funds.
* **Mitigation:** Two independent layers of defense:
  1. `Escrow.sol` constructor directly checks `block.chainid` and strictly requires the official Circle USDC address: `0x036CbD53842c5426634e7929541eC2318f3dCF7e` on Base Sepolia (`84532`), `0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913` on Base Mainnet (`8453`), reverting with `InvalidPaymentToken` or `UnsupportedChainId` if bypassed.
  2. Deployment script (`Deploy.s.sol`) additionally enforces fail-closed pre-flight validation.
* **Residual Risk:** None. The security boundary does not depend solely on deployment scripts.

### 2.8 Compromised Administrator
* **Threat:** An attacker obtains access to the `DEFAULT_ADMIN_ROLE` key.
* **Impact:** Attacker could grant arbitrator/pauser roles or pause the contract.
* **Mitigation:** The admin **cannot** drain funds directly. `paymentToken` is immutable. Escrow funds can only be released to the designated beneficiary or refunded to the client. Admin role must be assigned to a timelocked Gnosis Safe multisig with hardware signers.
* **Residual Risk:** Admin can grant `ARBITRATOR_ROLE` to an attacker key. Mitigated by operational multisig security and timelocks in future phases.

### 2.9 Compromised Arbitrator
* **Threat:** Attacker gains control of `ARBITRATOR_ROLE`.
* **Impact:** Malicious resolution of disputed escrows.
* **Mitigation:** In `resolveDispute`, the arbitrator can only distribute between `escrow.beneficiary` and `escrow.client` ($B + C = \text{Amount}$). The arbitrator **cannot redirect funds to an arbitrary third party**.
* **Residual Risk:** Arbitrator could unfairly favor one party in a dispute. Mitigated by platform multisig threshold requirements (e.g. 3-of-5).

### 2.10 Compromised Client Wallet
* **Threat:** Client's private key is compromised.
* **Impact:** Attacker could release funds to the legitimate beneficiary prematurely or dispute an escrow.
* **Mitigation:** Attacker cannot redirect escrow funds to an arbitrary address; funds can only go to the assigned `beneficiary`.
* **Residual Risk:** Loss of the client's locked deposit to the assigned beneficiary.

### 2.11 Compromised Beneficiary Wallet
* **Threat:** Beneficiary's private key is compromised.
* **Impact:** Attacker could forfeit/refund the escrow to the client.
* **Mitigation:** Attacker cannot release funds to an attacker address.
* **Residual Risk:** Premature return of funds to client.

### 2.12 Contract Upgrade Risk
* **Threat:** Flaws or malicious backdoors introduced via upgradeable proxies.
* **Impact:** Complete contract compromise.
* **Mitigation:** The `Escrow` contract is intentionally **non-upgradeable and immutable**. There are no proxy contracts, diamond patterns, or delegatecalls.
* **Residual Risk:** If a bug is discovered, a new contract version must be deployed and new escrows routed to it. Active escrows must drain through the existing contract.

### 2.13 Pause Abuse / Permanent Fund Lock
* **Threat:** An attacker or malfunctioning script repeatedly triggers `pause()`, permanently freezing user funds.
* **Impact:** Denial of service / frozen capital.
* **Mitigation:** `pause()` and `unpause()` are restricted to `PAUSER_ROLE` and `DEFAULT_ADMIN_ROLE`. The pause mechanism halts new state changes but does not alter balances or forfeit funds.
* **Residual Risk:** Operational dependency on admin/pauser responsiveness to unpause.

### 2.14 Timestamp Manipulation
* **Threat:** Block validators manipulate `block.timestamp` to prematurely trigger timeout refunds.
* **Impact:** Unfair early refund before work deadline expires.
* **Mitigation:** On Base L2 (OP Stack), timestamps are assigned by the centralized sequencer and subject to strict L1 consensus bounds (within seconds). Escrow deadlines are measured in hours/days (e.g. $\ge 1 \text{ hour}$), rendering second-level variance negligible.
* **Residual Risk:** Negligible for macro-deadlines.

### 2.15 Integer Overflow / Underflow & Truncation
* **Threat:** Arithmetic manipulation to bypass balance checks or produce phantom tokens.
* **Impact:** Infinite minting or balance underflow.
* **Mitigation:** Solidity `0.8.24` enforces built-in checked arithmetic for all operations. All timestamps use `uint256`, preventing typecast truncation.
* **Residual Risk:** None.

### 2.16 Denial of Service (DoS) / Gas Griefing
* **Threat:** An attacker creates unbounded loops or forces high gas consumption on state operations.
* **Impact:** Settlement transactions run out of gas.
* **Mitigation:** The `Escrow` contract contains zero unbounded loops and zero dynamic array iterations. Every operation executes in $O(1)$ constant time and bounded gas.
* **Residual Risk:** Low. Gas consumption is predictable and minimal on Base L2.

### 2.17 Stuck Funds
* **Threat:** A worker vanishes or crashes; funds remain locked in `LOCKED` status forever.
* **Impact:** Client funds become unrecoverable.
* **Mitigation:** Every escrow includes an `executionDeadline`. Once the deadline elapses without settlement, the client can unilaterally invoke `refundEscrow()` to retrieve 100% of their deposit.
* **Residual Risk:** If client also abandons the wallet, funds remain in escrow custody. Can be resolved by arbitrator refund if necessary.

### 2.18 Accidental Zero Address
* **Threat:** Creation of escrow with `address(0)` as beneficiary or admin.
* **Impact:** Tokens permanently burned or unmanageable contract.
* **Mitigation:** Constructor and creation functions strictly validate all address parameters against `address(0)` via `revert ZeroAddress()`.
* **Residual Risk:** None.

### 2.19 Event / Indexer Inconsistency
* **Threat:** Off-chain indexing service misses an event log or interprets state incorrectly.
* **Impact:** UI/database out of sync with blockchain truth.
* **Mitigation:** Every state transition emits an indexed event containing `escrowId`, participants, and amounts. The on-chain state machine remains the authoritative source of truth.
* **Residual Risk:** Off-chain indexer latency (to be addressed in Phase 6.2 indexer architecture).

### 2.20 Chain Reorganization (Reorg) Implications
* **Threat:** A 1-2 block micro-reorg on Base L2 causes a transaction to be reverted or reordered.
* **Impact:** Premature off-chain state advancement before on-chain finality.
* **Mitigation:** Contract-level operations are idempotent with respect to deterministic `escrowId`. Off-chain integration (Phase 6.2) enforces an $N=12$ block confirmation requirement before treating events as final.
* **Residual Risk:** Addressed in off-chain worker design.

### 2.21 Deployment Misconfiguration
* **Threat:** Deploying to testnet with mainnet parameters or missing role configurations.
* **Impact:** Broken access control or failed interactions.
* **Mitigation:** `Deploy.s.sol` enforces programmatic pre-flight checks: validates chain ID, forces mainnet deployment block, verifies official Circle USDC addresses, and asserts all role addresses $\ne \text{address}(0)$.
* **Residual Risk:** Minimal.
