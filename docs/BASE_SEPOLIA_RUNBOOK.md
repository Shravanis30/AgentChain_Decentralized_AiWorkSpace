# AgentChain Base Sepolia Live Integration Runbook

> **Status**: Live Base Sepolia testing has NOT been executed as of Phase 6.2B.2.
> No live testnet credentials are available in the development environment.
> This runbook defines the exact procedure to execute the first live end-to-end test.

---

## IMPORTANT

- Use **testnet assets only**. Never use Mainnet ETH or USDC.
- Never commit private keys, API keys, or seed phrases to version control.
- Never use a funded Mainnet wallet as the test wallet.
- Base Mainnet (chain_id=8453) remains hard-blocked regardless of testnet credentials.

---

## 1. Required Environment

### 1.1 RPC Configuration

| Variable | Value |
|---------|-------|
| `BASE_SEPOLIA_RPC_URL` | `https://sepolia.base.org` or Alchemy/Infura Base Sepolia endpoint |
| `ETHERSCAN_API_KEY` | Optional — for contract verification |

Recommended providers:
- Alchemy: `https://base-sepolia.g.alchemy.com/v2/<API_KEY>`
- Infura: `https://base-sepolia.infura.io/v3/<PROJECT_ID>`
- Public (rate-limited): `https://sepolia.base.org`

### 1.2 Required Test Wallet

- A dedicated throwaway test wallet (NOT your personal or Mainnet wallet).
- Must have:
  - Sufficient Base Sepolia ETH for gas (minimum 0.01 ETH)
  - Sufficient Base Sepolia USDC for escrow creation (minimum 10 USDC)
- NEVER share the private key of a wallet that holds Mainnet value.

```bash
# Generate a fresh test wallet (example using cast):
cast wallet new

# Note: Store private key securely (e.g., 1Password or local .env only)
# Example output:
#   Address:     0xAbCd...
#   Private key: 0x1234...
```

### 1.3 Required Testnet ETH

Obtain Base Sepolia ETH:
- Coinbase Faucet: https://www.coinbase.com/faucets/base-ethereum-sepolia-faucet
- Alchemy Faucet: https://sepoliafaucet.com

Minimum: 0.01 ETH for gas.

### 1.4 Required Base Sepolia USDC

Base Sepolia USDC address: `0x036CbD53842c5426634e7929541eC2318f3dCF7e`

Obtain via Circle's testnet faucet or USDC Bridge from Sepolia.

### 1.5 Required Environment Variables

Create a local `.env.sepolia` file (NEVER commit this):

```bash
# Network
BASE_SEPOLIA_RPC_URL=https://sepolia.base.org

# Relayer
RELAYER_PRIVATE_KEY=0x<test_wallet_private_key>

# Contract
ESCROW_CONTRACT_BASE_SEPOLIA=<deployed_escrow_address>
MOCK_USDC_CONTRACT=  # Leave empty — uses BASE_SEPOLIA_USDC constant

# Settlement
ALLOW_SEPOLIA_TRANSACTIONS=true
BLOCK_MAINNET_SETTLEMENT=true  # MUST remain true

# Confirmations
BASE_SEPOLIA_CONFIRMATIONS=3
MAX_REORG_DEPTH=32
```

---

## 2. Contract Deployment Procedure

### 2.1 Deploy Escrow Contract

```bash
cd contracts

# Source your .env.sepolia file
export $(cat ../.env.sepolia | xargs)

# Verify Foundry can reach the RPC
cast block-number --rpc-url $BASE_SEPOLIA_RPC_URL

# Deploy Escrow with the USDC token address
forge script script/Deploy.s.sol:DeployScript \
  --rpc-url $BASE_SEPOLIA_RPC_URL \
  --broadcast \
  --verify \
  --etherscan-api-key $ETHERSCAN_API_KEY \
  -vvvv
```

Record:
- `ESCROW_CONTRACT_BASE_SEPOLIA=<deployed_address>`
- Deployment transaction hash
- Deployment block number

Update your `.env.sepolia` with the deployed contract address.

---

## 3. Database & Service Startup

```bash
# Apply migrations
cd backend
alembic upgrade head

# Start indexer (in a separate terminal)
cd services/indexer
CHAIN_ID=84532 \
BASE_SEPOLIA_RPC_URL=$BASE_SEPOLIA_RPC_URL \
ESCROW_CONTRACT_BASE_SEPOLIA=$ESCROW_CONTRACT_BASE_SEPOLIA \
python -m indexer.main

# Start backend API (in a separate terminal)
cd backend
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

---

## 4. Escrow Creation Procedure

### 4.1 Approve USDC Spending

```bash
# Approve Escrow contract to spend USDC on behalf of the test wallet
cast send 0x036CbD53842c5426634e7929541eC2318f3dCF7e \
  "approve(address,uint256)" \
  $ESCROW_CONTRACT_BASE_SEPOLIA \
  10000000 \
  --rpc-url $BASE_SEPOLIA_RPC_URL \
  --private-key $RELAYER_PRIVATE_KEY
```

### 4.2 Create and Fund Escrow

```bash
# Parameters (adjust as needed):
# referenceId: 32-byte task reference (left-padded)
# beneficiary: developer/agent wallet address
# amount: 10 USDC = 10_000_000 (6 decimals)
# executionDeadline: UNIX timestamp ~1 hour from now
# salt: random unique uint256

DEADLINE=$(date -v+1H +%s)  # macOS
# On Linux: DEADLINE=$(date -d '1 hour' +%s)
SALT=$RANDOM$RANDOM

cast send $ESCROW_CONTRACT_BASE_SEPOLIA \
  "createAndFundEscrow(bytes32,address,uint256,uint256,uint256)" \
  "0x$(python3 -c 'import secrets; print(secrets.token_hex(32))')" \
  <beneficiary_address> \
  10000000 \
  $DEADLINE \
  $SALT \
  --rpc-url $BASE_SEPOLIA_RPC_URL \
  --private-key $RELAYER_PRIVATE_KEY \
  -vvvv
```

Record:
- Transaction hash
- Block number
- Emitted `escrowId` from `EscrowCreated` event

---

## 5. Lock Procedure

```bash
cast send $ESCROW_CONTRACT_BASE_SEPOLIA \
  "lockEscrow(bytes32)" \
  <escrow_id_bytes32> \
  --rpc-url $BASE_SEPOLIA_RPC_URL \
  --private-key $RELAYER_PRIVATE_KEY
```

---

## 6. Indexer Startup and Event Verification

Wait for the indexer to pick up the `EscrowCreated` and `EscrowFunded` events.

Expected indexer logs:
```
INFO [event_indexer] Indexed EscrowCreated event for escrow <id> at block <n> on chain 84532
INFO [event_indexer] EscrowChainState created for escrow <id>: state=FUNDED
```

Verify in database:
```sql
SELECT escrow_id, current_chain_state, is_canonical, confirmation_status
FROM escrow_chain_states
WHERE chain_id = 84532;
```

Expected: `is_canonical=true`, `confirmation_status='CONFIRMED'` after 3 blocks.

---

## 7. Settlement Authorization

```bash
# Via the API
curl -X POST http://localhost:8000/api/v1/settlements/ \
  -H 'Authorization: Bearer <your_jwt>' \
  -H 'Content-Type: application/json' \
  -d '{
    "chain_id": 84532,
    "escrow_id": "<escrow_id>",
    "action": "RELEASE"
  }'
```

Expected response:
```json
{
  "id": "<settlement_uuid>",
  "status": "PENDING_AUTHORIZATION",
  ...
}
```

---

## 8. Relayer Startup

```bash
cd backend
BASE_SEPOLIA_RPC_URL=$BASE_SEPOLIA_RPC_URL \
RELAYER_PRIVATE_KEY=$RELAYER_PRIVATE_KEY \
ESCROW_CONTRACT_BASE_SEPOLIA=$ESCROW_CONTRACT_BASE_SEPOLIA \
python -m app.services.blockchain.relayer_worker
```

Expected relayer logs:
```
INFO [relayer] [PREFLIGHT OK] Relayer startup verified: chain=84532 (base-sepolia), ...
INFO [relayer] Broadcasted tx 0x... (nonce=0) for intent <id> on chain 84532
```

---

## 9. Transaction Confirmation

Monitor until settlement status transitions:
- `PENDING_AUTHORIZATION` → `SUBMITTED` → `CONFIRMED`

```bash
# Poll settlement status
curl http://localhost:8000/api/v1/settlements/<settlement_id> \
  -H 'Authorization: Bearer <your_jwt>'
```

Expected final state: `"status": "CONFIRMED"`

---

## 10. Expected Events

After a successful RELEASE settlement:

| Event | Block | Status |
|-------|-------|--------|
| `EscrowCreated` | N | CONFIRMED |
| `EscrowFunded` | N | CONFIRMED |
| `EscrowLocked` | N+1 | CONFIRMED |
| `EscrowReleased` | N+K | CONFIRMED (after relayer + 3 confirms) |

All events must have `is_canonical=true` in `blockchain_events`.

---

## 11. What to Record

For each live test, record:
- Chain ID: `84532`
- Escrow contract address
- Escrow ID (bytes32 hex)
- Transaction hashes (create, fund, lock, release)
- Block numbers
- Confirmation depth observed
- Settlement UUID and final status

---

## 12. Cleanup Procedure

```bash
# Revoke USDC approval
cast send 0x036CbD53842c5426634e7929541eC2318f3dCF7e \
  "approve(address,uint256)" \
  $ESCROW_CONTRACT_BASE_SEPOLIA \
  0 \
  --rpc-url $BASE_SEPOLIA_RPC_URL \
  --private-key $RELAYER_PRIVATE_KEY

# Recover remaining testnet ETH (send to a backup wallet)
# The test wallet may be abandoned after the run since it never held Mainnet value.
```

---

## 13. Verification of Transaction Hashes

```bash
# Verify a transaction on Basescan Sepolia
cast receipt <tx_hash> --rpc-url $BASE_SEPOLIA_RPC_URL

# Or via Basescan UI:
# https://sepolia.basescan.org/tx/<tx_hash>
```

---

## 14. Failure Recovery

| Failure Point | Recovery |
|--------------|---------|
| RPC error during deployment | Retry with backoff; try secondary RPC provider |
| Escrow contract not found | Re-deploy; update `ESCROW_CONTRACT_BASE_SEPOLIA` |
| USDC approve failed | Check balance; re-approve |
| Indexer not picking up events | Verify `ESCROW_CONTRACT_BASE_SEPOLIA` is set; restart indexer |
| Settlement stuck PENDING | Verify indexer confirmed 3 blocks; check `confirmation_status` |
| Relayer preflight fails | Check `RELAYER_PRIVATE_KEY`, RPC chain ID match, USDC address |
| Settlement stuck SUBMITTED | Check relayer logs for nonce issues; may need bump |
| Transaction reverted | Check Escrow state is correct before calling |

---

*Live test status: NOT EXECUTED as of Phase 6.2B.2 — environment credentials unavailable.*
*Update this document with transaction hashes after first live execution.*
