# AgentChain — Smart Contract Deployment Guide

## 1. Overview

This document specifies the deployment architecture, configuration parameters, network targets, verification procedures, and security safeguards for AgentChain's smart contracts (`Escrow.sol`, `AgentRegistry.sol`).

---

## 2. Supported Networks & Configuration

### 2.1 Network Parameters

| Parameter | Local Anvil | Base Sepolia (Testnet) | Base Mainnet (Production) |
| :--- | :--- | :--- | :--- |
| **Chain ID** | `31337` | `84532` | `8453` |
| **RPC Endpoint** | `http://localhost:8545` | `https://sepolia.base.org` | `https://mainnet.base.org` |
| **Block Explorer** | N/A | `https://sepolia.basescan.org` | `https://basescan.org` |
| **Native USDC Address** | Deployed `MockUSDC` | `0x036CbD53842c5426634e7929541eC2318f3dCF7e` | `0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913` |
| **USDC Decimals** | 6 | 6 | 6 |
| **Phase 6.1 Status** | **Supported** | **Supported** | **BLOCKED (Audit Gate)** |

> [!CAUTION]
> **Base Mainnet Deployment Block:** In accordance with Phase 6.1 requirements, `Deploy.s.sol` enforces an explicit programmatic block on Chain ID `8453`. Any deployment attempt against Base Mainnet reverts immediately with `MainnetDeploymentBlocked()`.

---

## 3. Deployment Safety Controls

The deployment script (`contracts/script/Deploy.s.sol`) operates on a **fail-closed** paradigm:

1. **Chain ID Verification:** Verifies `block.chainid` matches supported networks.
2. **Mainnet Lock:** Reverts if `block.chainid == 8453`.
3. **Official USDC Validation:** On Base Sepolia, requires `USDC_ADDRESS` to match Circle's official contract (`0x036CbD53842c5426634e7929541eC2318f3dCF7e`). Any other address reverts with `InvalidUsdcAddressForNetwork`.
4. **Non-Zero Address Assertions:** Verifies `deployer`, `admin`, `arbitrator`, and `pauser` are non-zero addresses.
5. **Decimals Verification:** `Escrow` constructor queries `IERC20Metadata(token).decimals()` and reverts if not equal to 6.
6. **No Committed Secrets:** Private keys and RPC secrets are strictly injected via environment variables and never checked into source control.

---

## 4. Local Anvil Deployment

### Step 1: Start Anvil
In a dedicated terminal:
```bash
anvil --chain-id 31337 --port 8545
```

### Step 2: Run Deployment Script
From `contracts/`:
```bash
forge script script/Deploy.s.sol \
  --rpc-url http://localhost:8545 \
  --broadcast
```

### Expected Output
```
== Logs ==
  Executing deployment on Chain ID: 31337
  Deployer: 0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266
  Admin: 0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266
  Arbitrator: 0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266
  Pauser: 0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266
  Deployed MockUSDC at: 0x5FbDB2315678afecb367f032d93F642f64180aa3
  AgentRegistry deployed at: 0xe7f1725E7734CE288F8367e1Bb143E90bb3F0512
  Escrow deployed at: 0x9fE46736679d2D9a65F0992F2272dE9f3c7fa6e0
```

---

## 5. Base Sepolia Testnet Deployment

### Prerequisites
1. Base Sepolia ETH in the deployer wallet (from official Base Sepolia faucet).
2. Environment variables set:
```bash
export RPC_URL="https://sepolia.base.org"
export DEPLOYER_PRIVATE_KEY="<YOUR_TESTNET_PRIVATE_KEY>"
export ADMIN_ADDRESS="<YOUR_ADMIN_ADDRESS>"
export ARBITRATOR_ADDRESS="<YOUR_ARBITRATOR_MULTISIG_ADDRESS>"
export PAUSER_ADDRESS="<YOUR_PAUSER_ADDRESS>"
export USDC_ADDRESS="0x036CbD53842c5426634e7929541eC2318f3dCF7e"
export ETHERSCAN_API_KEY="<YOUR_BASESCAN_API_KEY>"
```

### Simulation / Dry Run
Before broadcasting, verify execution against the Base Sepolia RPC state:
```bash
forge script script/Deploy.s.sol \
  --fork-url https://sepolia.base.org \
  -vvv
```

### Live Broadcast & On-Chain Deployment
```bash
forge script script/Deploy.s.sol \
  --rpc-url https://sepolia.base.org \
  --broadcast \
  --verify \
  -vvvv
```

---

## 6. Contract Verification on Basescan

If verification was not executed during broadcast or needs re-running:

### Verify AgentRegistry
```bash
forge verify-contract <AGENT_REGISTRY_ADDRESS> \
  src/AgentRegistry.sol:AgentRegistry \
  --chain-id 84532 \
  --verifier-url "https://api-sepolia.basescan.org/api" \
  --etherscan-api-key "${ETHERSCAN_API_KEY}" \
  --constructor-args $(cast abi-encode "constructor(address)" "${ADMIN_ADDRESS}")
```

### Verify Escrow
```bash
forge verify-contract <ESCROW_ADDRESS> \
  src/Escrow.sol:Escrow \
  --chain-id 84532 \
  --verifier-url "https://api-sepolia.basescan.org/api" \
  --etherscan-api-key "${ETHERSCAN_API_KEY}" \
  --constructor-args $(cast abi-encode "constructor(address,address,address,address)" \
    "${ADMIN_ADDRESS}" "${ARBITRATOR_ADDRESS}" "${PAUSER_ADDRESS}" "0x036CbD53842c5426634e7929541eC2318f3dCF7e")
```

---

## 7. Post-Deployment Verification Checklist

- [ ] Deployed address recorded in deployment log artifact.
- [ ] Constructor arguments verified against on-chain bytecode.
- [ ] `paymentToken` confirmed to return official USDC address on the target network.
- [ ] `DEFAULT_ADMIN_ROLE` confirmed held by intended multi-sig.
- [ ] `ARBITRATOR_ROLE` confirmed held by platform arbitration authority.
- [ ] `PAUSER_ROLE` confirmed held by emergency response account.
- [ ] Test transaction executed (create and fund test escrow of 1 USDC).
- [ ] Basescan source code verification green and visible on explorer.
