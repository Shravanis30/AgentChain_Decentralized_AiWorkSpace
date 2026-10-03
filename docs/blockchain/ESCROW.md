# AgentChain — Smart Contract & Escrow Foundation (Phase 6.1)

## 1. Executive Overview

Phase 6.1 establishes the on-chain financial commitment primitive for the AgentChain platform. The `Escrow` smart contract provides trustless custody and settlement for task payments using Circle's native USDC. 

### Architecture Boundaries
* **On-Chain Scope:** Solely custody, financial state transitions, replay protection, deterministic escrow identity, emergency pause, timeout refunds, and platform multisig arbitration resolution.
* **Off-Chain Scope (Explicitly Kept Off-Chain):** Agent execution, task orchestration (LangGraph), model inference, database persistence (PostgreSQL), durable wake-ups (Redis Streams), and result payload storage. The smart contract does **not** execute AI agents and contains **no** agent selection or orchestration logic.

```
       +-------------------------------------------------------------+
       |                         Client                              |
       +-------------------------------------------------------------+
                                      |
                         createAndFundEscrow(USDC)
                                      v
       +-------------------------------------------------------------+
       |                      Escrow Contract                        |
       |  - USDC Locked (SafeERC20)                                  |
       |  - Deterministic Escrow ID (Domain/Chain separated)         |
       |  - AccessControl (Client, Beneficiary, Arbitrator, Pauser)  |
       +-------------------------------------------------------------+
         /                     |                     \           \
        /                      |                      \           \
 lockEscrow              releaseEscrow           refundEscrow   disputeEscrow
 (Work Starts)           (To Beneficiary)        (To Client)    (Frozen)
                                                                    |
                                                              resolveDispute
                                                               (Arbitrator)
```

---

## 2. Escrow State Machine

The contract enforces an explicit, finite state machine where every transition is strictly checked and unauthorized or invalid transitions revert immediately.

### State Transition Diagram

```
                 +-------------------+
                 |       NONE        |
                 +-------------------+
                   |               |
      createEscrow |               | createAndFundEscrow
                   v               |
           +---------------+       |
           |    CREATED    |       |
           +---------------+       |
             |           |         |
  fundEscrow |           | refund  |
             v           | (Cancel)|
           +---------------+       |
           |    FUNDED     |<------+
           +---------------+
             |           |
  lockEscrow |           | refundEscrow (Client or Arbitrator before lock)
             v           |
      +------------+     |
      |   LOCKED   |     |
      +------------+     |
       /     |    \      |
      /      |     \     |
release      |    dispute|
     /    refund    \    |
    /   (Timeout/    \   |
   /    Forfeit/      \  |
  /    Arbitrator)     \ |
 v           v          vv
+----------+ +----------+ +-----------+
| RELEASED | | REFUNDED | | DISPUTED  |
+----------+ +----------+ +-----------+
 (Terminal)   (Terminal)   /    |    \
                          /     |     \
          resolveDispute /      |      \ releaseEscrow (Arbitrator)
                        /       | refundEscrow (Arbitrator)
                       v        v        v
                 +----------+ +----------+ +----------+
                 | RESOLVED | | REFUNDED | | RELEASED |
                 +----------+ +----------+ +----------+
                  (Terminal)   (Terminal)   (Terminal)
```

### Valid Transitions Table

| Initial State | Target State | Authorized Caller(s) | Trigger / Condition |
| :--- | :--- | :--- | :--- |
| `NONE` | `CREATED` | Any Caller | `createEscrow`: Unfunded record initialized with deadline and reference ID. |
| `NONE` | `FUNDED` | Any Caller (Client) | `createAndFundEscrow`: Atomic initialization and USDC transfer. |
| `CREATED` | `FUNDED` | `client` | `fundEscrow`: Client transfers USDC to contract. |
| `CREATED` | `REFUNDED` | `client` or `DEFAULT_ADMIN_ROLE` | `refundEscrow`: Cancels unfunded escrow record (zero token transfer). |
| `FUNDED` | `LOCKED` | `client`, `beneficiary`, `admin`, or `arbitrator` | `lockEscrow`: Task execution begins off-chain; funds are locked. |
| `FUNDED` | `REFUNDED` | `client`, `beneficiary`, or `arbitrator` | `refundEscrow`: Voluntary refund/cancellation prior to task lock. |
| `FUNDED` | `RELEASED` | `client` or `arbitrator` | `releaseEscrow`: Client approves payout directly without separate lock. |
| `LOCKED` | `RELEASED` | `client` or `arbitrator` | `releaseEscrow`: Successful completion; funds disburse to `beneficiary`. |
| `LOCKED` | `REFUNDED` | `beneficiary` (forfeiture), `arbitrator`, or `client` (timeout) | `refundEscrow`: If called by `client`, `block.timestamp >= executionDeadline` is strictly required. |
| `LOCKED` | `DISPUTED` | `client`, `beneficiary`, or `arbitrator` | `disputeEscrow`: Halts normal releases/refunds; freezes funds for arbitration. |
| `DISPUTED` | `RESOLVED` | `ARBITRATOR_ROLE` | `resolveDispute`: Multisig splits funds between beneficiary and client ($B + C = \text{Amount}$). |
| `DISPUTED` | `RELEASED` | `ARBITRATOR_ROLE` | `releaseEscrow`: Arbitrator awards 100% of escrow to beneficiary. |
| `DISPUTED` | `REFUNDED` | `ARBITRATOR_ROLE` | `refundEscrow`: Arbitrator awards 100% of escrow to client. |

---

## 3. Escrow Data Model

Each escrow is keyed on-chain by a deterministic `bytes32 escrowId`:

```solidity
struct EscrowRecord {
    bytes32 escrowId;           // Deterministic unique identifier
    address client;             // Account funding the escrow
    address beneficiary;        // Account receiving completion payout
    uint256 amount;             // Escrowed amount in USDC base units (6 decimals)
    EscrowState state;          // Current state machine enum
    uint256 createdAt;          // Block timestamp when record was created
    uint256 fundedAt;           // Block timestamp when funds were deposited
    uint256 lockedAt;           // Block timestamp when task work was locked
    uint256 settledAt;          // Block timestamp when terminal payout occurred
    uint256 executionDeadline;   // Unix timestamp after which client can timeout-refund
    bytes32 referenceId;        // Bounded reference (e.g. keccak256 of off-chain task UUID)
    bytes32 disputeReasonHash;  // Cryptographic hash of dispute evidence
}
```

### Deterministic Escrow ID Scheme
The `escrowId` is calculated using `keccak256` with complete domain separation:
```solidity
keccak256(abi.encode(block.chainid, address(this), client, referenceId, salt))
```
* **`block.chainid`**: Prevents cross-chain replay across Base Sepolia, Base Mainnet, or local Anvil.
* **`address(this)`**: Binds the ID to the specific deployed Escrow instance.
* **`client`**: Prevents malicious third parties from pre-occupying or colliding with another user's escrow ID.
* **`referenceId`**: Links the escrow to the off-chain task ID without storing unbounded strings on-chain.
* **`salt`**: Allows repeated runs for the same reference ID when intentionally desired.

---

## 4. Payment Token Configuration

The contract exclusively supports **native Circle USDC** with 6 decimal places.

### Verified Network Addresses

| Network | Chain ID | Contract Address | Type | Verification Source |
| :--- | :--- | :--- | :--- | :--- |
| **Base Mainnet** | `8453` | `0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913` | Native Circle USDC | Official Circle Developer Documentation |
| **Base Sepolia** | `84532` | `0x036CbD53842c5426634e7929541eC2318f3dCF7e` | Native Circle Testnet USDC | Official Circle Developer Documentation |
| **Local Anvil** | `31337` | Deployed `MockUSDC` | 6 Decimals ERC-20 Mock | Local Foundry Test Fixture |

### Token Security Controls & Trust Boundary
* **Constructor-Level Chain Enforcement:** The contract directly inspects `block.chainid` in its constructor:
  - On **Base Sepolia (`84532`)**: Reverts unless `tokenAddress == 0x036CbD53842c5426634e7929541eC2318f3dCF7e`.
  - On **Base Mainnet (`8453`)**: Reverts unless `tokenAddress == 0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913`.
  - On **Local Anvil (`31337`)**: Requires 6 decimals (e.g. `MockUSDC`).
  - On any other chain: Reverts with `UnsupportedChainId(chainId)`.
  This guarantees that even if an attacker bypasses the deployment scripts, the contract cannot be instantiated with an unverified or arbitrary token.
* **Immutable Configuration:** The token address is configured once in the constructor as `IERC20 public immutable paymentToken;`. It cannot be altered by any administrator or external caller, eliminating token-swapping vectors.
* **Decimals Verification:** Constructor verifies that `IERC20Metadata(tokenAddress).decimals() == 6`, failing closed if an incompatible token is supplied.
* **OpenZeppelin SafeERC20:** All token transfers (`safeTransfer`, `safeTransferFrom`) use `SafeERC20` to protect against non-standard ERC-20 return values.
* **Integer Arithmetic:** All amounts represent integer base units ($1 \text{ USDC} = 1{,}000{,}000 \text{ base units}$). No floating-point math is used.

---

## 5. Access Control & Authorization

Role management is governed via OpenZeppelin `AccessControl`:

1. **`DEFAULT_ADMIN_ROLE`**: Can grant/revoke roles and execute administrative maintenance. Multi-sig recommended.
2. **`ARBITRATOR_ROLE`**: Platform-managed multisig authority. Can resolve disputes (`resolveDispute`), or execute administrative release/refund on locked/disputed escrows.
3. **`PAUSER_ROLE`**: Designated emergency response account able to invoke `pause()` and `unpause()`.
4. **`client` (Per-Escrow Participant)**: The funding wallet. Can release funds, lock funds, open disputes, or claim timeout refunds after deadline.
5. **`beneficiary` (Per-Escrow Participant)**: The worker/developer wallet. Can lock funds, forfeit/refund funds to client, or open disputes. **Cannot release funds to itself.**

---

## 6. Events Specification

All state transitions emit rich events indexed for the future blockchain indexer:

```solidity
event EscrowCreated(
    bytes32 indexed escrowId,
    address indexed client,
    address indexed beneficiary,
    uint256 amount,
    bytes32 referenceId,
    uint256 executionDeadline
);
event EscrowFunded(bytes32 indexed escrowId, address indexed client, uint256 amount);
event EscrowLocked(bytes32 indexed escrowId, address indexed operator);
event EscrowReleased(bytes32 indexed escrowId, address indexed beneficiary, uint256 amount);
event EscrowRefunded(bytes32 indexed escrowId, address indexed client, uint256 amount);
event DisputeOpened(bytes32 indexed escrowId, address indexed initiator, bytes32 indexed reasonHash);
event DisputeResolved(
    bytes32 indexed escrowId,
    address indexed arbitrator,
    uint256 beneficiaryAmount,
    uint256 clientRefundAmount
);
```

---

---

## 7. Deferred Work (Phase 6.2+)

The following components are intentionally deferred to Phase 6.2 and beyond:
1. Backend blockchain indexer service.
2. Automated execution-to-settlement worker.
3. Nonce management and relayer queuing.
4. Chain reorganization / finality confirmation worker ($N = 12$ blocks).
5. EIP-712 off-chain meta-transaction signatures.
6. On-chain reputation decay contract (`Reputation.sol`).
7. Result deliverable notarization registry integration.
8. Protocol fee splits (85/10/5 revenue model).
9. Base Mainnet production deployment.

---

## 8. Future 85/10/5 Compatibility Analysis

AgentChain plans an 85/10/5 revenue distribution model in later phases:
- **85%** to Worker / Agent Developer (`beneficiary`)
- **10%** to Collateral Stakers (`StakingRewards.sol`)
- **5%** to Protocol Treasury / DAO (`Treasury.sol`)

### Architecture Verification:
* **Storage Invariant:** `totalEscrowBalance` tracks the entire escrowed commitment ($A$).
* **Compatibility:** In `releaseEscrow`, the current Phase 6.1 implementation transfers 100% of $A$ to `beneficiary`. In Phase 6.2+, this can be extended into 3 bounded transfers ($A \times 85 / 100$, $A \times 10 / 100$, and remainder to `Treasury`) without modifying escrow identifiers, state machines, dispute logic, or client custody rules.
* **No Breaking Changes Required:** Because `totalEscrowBalance` accounts for the full $A$ upon deposit and reduces by $A$ upon release, the accounting invariants verified in Phase 6.1 hold identically under fractional fee routing.

---

## 9. Production Readiness Assessment

* **Testnet Readiness:** **READY** for Base Sepolia testnet integration and Anvil development.
* **Production Readiness:** **PRODUCTION DEPLOYMENT BLOCKED PENDING SECURITY AUDIT.**
* **Audit Disclaimer:** The contract has not yet undergone formal verification by an external security firm. No real funds may be committed on Base Mainnet.
