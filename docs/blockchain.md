# AgentChain: Blockchain & Smart Contract Architecture

## 1. Overview & Network Topology

AgentChain utilizes EVM-compatible smart contracts to establish trustless escrow, verifiable deliverables, transparent revenue distribution, and tamper-resistant reputation.

- **Target Network:** EVM Layer-2 (Base / Arbitrum One) for sub-cent gas fees, 2-second block finality, and robust native USDC liquidity.
- **Settlement Token:** Native USDC (ERC-20, 6 decimals).
- **Security Standard:** Solidity `^0.8.20`, OpenZeppelin v5.0 (`ReentrancyGuard`, `Ownable2Step`, `Pausable`, `SafeERC20`).

---

## 2. Smart Contract Suite Design

```
                     +---------------------------------------+
                     |             Treasury.sol              |
                     |  - Receives Protocol Fees (e.g. 2.5%) |
                     |  - Timelocked Multi-Sig Governance    |
                     +---------------------------------------+
                                         ^
                                         | Fees
+------------------------+   Funds       |        +-------------------------+
|    AgentRegistry.sol   | <-------------+----->  |       Escrow.sol        |
| - Operator Staking     | (Collateral / Slashes) | - Locks USDC from User  |
| - Capabilities URI     |                        | - Notarizes Result Hash |
| - Active Status / Bond |                        | - Distributes Payouts   |
+------------------------+                        +-------------------------+
            |                                                  |
            | Agent Status & Score                             | Task Success/Fail
            v                                                  v
+---------------------------------------------------------------------------+
|                              Reputation.sol                               |
| - On-chain dynamic reputation with exponential time-decay                 |
| - Subtask completion feedback attestations                                |
+---------------------------------------------------------------------------+
```

---

## 3. Contract Specifications

### 3.1 `AgentRegistry.sol`
Manages agent identity, capability discovery metadata, and staking collateral.
- **Key Storage:**
  - `mapping(uint256 => Agent) public agents;`
  - `mapping(address => uint256[]) public operatorAgents;`
- **Functions:**
  - `registerAgent(string calldata metadataURI, string[] calldata capabilities, uint256 stakeAmount)`: Requires transferring `stakeAmount` USDC to contract.
  - `increaseStake(uint256 agentId, uint256 amount)`: Adds collateral.
  - `initiateUnstake(uint256 agentId, uint256 amount)`: Starts 7-day timelock cooldown to prevent exit scams before pending tasks settle.
  - `slashStake(uint256 agentId, uint256 amount, address recipient)`: Called exclusively by `Escrow.sol` or Arbitration Court upon confirmed malicious breach.

### 3.2 `Escrow.sol`
Manages the end-to-end financial lifecycle of tasks.
- **States:** `Created`, `Funded`, `Completed`, `Disputed`, `Refunded`.
- **Functions:**
  - `createAndDeposit(uint256 taskId, uint256 totalBudget, uint256 platformFee, uint256 deadline)`:
    - Transfers $(totalBudget + platformFee)$ USDC from requester via `SafeERC20.safeTransferFrom`.
    - Emits `EscrowDeposited(taskId, requester, totalBudget)`.
  - `completeTaskAndDistribute(uint256 taskId, bytes32 resultHash, PayoutSplit[] calldata splits)`:
    - Verifies caller is authorized Relayer or Requester.
    - Commits `tasks[taskId].resultHash = resultHash`.
    - Transfers fees: `protocolFee` to `Treasury.sol`.
    - Transfers subtask allocations to respective Agent Operators.
    - Notifies `Reputation.sol` to record positive performance.
    - Emits `TaskCompleted(taskId, resultHash, splits)`.
  - `disputeTask(uint256 taskId, string calldata reasonURI)`:
    - Freezes funds, emits `TaskDisputed(taskId, msg.sender)`.
  - `resolveDispute(uint256 taskId, uint256 requesterRefund, PayoutSplit[] calldata splits)`:
    - Executable only by the designated Arbitrator (Multi-Sig or Kleros module).

### 3.3 `Reputation.sol`
Maintains an immutable track record of agent reliability.
- **Formula:**
  $$R(t) = R_0 \cdot \exp(-\lambda \cdot \Delta t) + \sum \Delta R_{\text{task}}$$
  Implemented in fixed-point integer math (PRBMath / Solady) on-chain.
- **Events:** `ReputationUpdated(uint256 indexed agentId, uint256 newScore, int256 delta)`.

### 3.4 `Treasury.sol`
Vault holding accrued protocol fees.
- Enforces `Ownable2Step` requiring a Gnosis Safe multi-sig owner.
- Implements emergency pause and circuit-breakers on maximum withdrawal velocity.

### 3.5 `ResultNotary.sol` (Implemented in Phase 6.3)
Dedicated, fundless cryptographic anchor for agent execution outputs.
- Cryptographically binds `executionId` (bytes32) to `resultHash` (bytes32 SHA-256) and optional `artifactCommitment` (bytes32).
- Zero user funds, zero transfers, zero subjective parameters.
- Restricts notarization to authorized `NOTARIZER_ROLE` accounts.
- Enforces on-chain replay protection via `AlreadyNotarized(executionId)`.
- Exposes `verifyProof(bytes32 executionId, bytes32 expectedHash)` returning a deterministic boolean.

### 3.6 `ReputationRegistry.sol` (Implemented in Phase 6.4)
Auditable reputation evidence registry based strictly on verified execution outcomes.
- Binds to `ResultNotary.sol` to verify that `VERIFIED_SUCCESS` records have confirmed cryptographic proofs.
- Restricts registration to authorized `REPUTATION_ORACLE_ROLE` accounts.
- Rejects subjective star ratings, self-reported scores, and mutable on-chain counters.
- Enforces on-chain replay protection (`AlreadyRegistered(executionId)`).
- Emits immutable `ReputationEventRegistered` events consumed by off-chain indexers to project reorg-safe reputation profiles.

---

## 4. Off-Chain Indexing & Synchronization

To guarantee seamless integration between off-chain LangGraph orchestrator and on-chain state:
1. **Event Watcher Service (`viem` / `web3.py`):**
   - Continuously subscribes to WebSocket block headers and contract event logs.
   - Requires $N = 12$ block confirmations on Arbitrum/Base to defend against micro-reorgs.
2. **Transaction Relayer Worker:**
   - Holds an operational relayer private key (secured via AWS KMS).
   - Batches `completeTaskAndDistribute` calls using multicall or ERC-4337 bundlers to minimize transaction costs.
   - Implements EIP-1559 dynamic fee calculation with automatic gas bumping on unconfirmed transactions.
3. **Audit Hash Verification:**
   - Prior to triggering fund release, the backend orchestrator verifies:
     $$\text{keccak256}(\text{PostgreSQL Artifact Deliverable Hash}) == \text{resultHash submitted to Escrow}$$
   - Any mismatch immediately halts the transaction and alerts security monitors.
