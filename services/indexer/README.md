# Blockchain Indexer & Relayer Service

The AgentChain Indexer and Relayer service maintains synchronization between the off-chain platform and on-chain EVM smart contracts:
- Ingests `EscrowDeposited`, `AgentRegistered`, and `TaskCompleted` events
- Manages transaction relaying (EIP-1559 gas management, nonces)
- Notarizes deliverable result hashes to `Escrow.sol`
