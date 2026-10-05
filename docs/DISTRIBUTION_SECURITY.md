# Revenue Distribution Security — AgentChain Phase 6.2C

## 1. Threat Model & Boundaries

The economic distribution layer transfers funds from escrow to three distinct destinations upon settlement release:
1. Agent Developer (85%)
2. Staking Pool (10%)
3. DAO Treasury (5% + remainder)

The primary security objectives are:
- Preventing unauthorized diversion of funds.
- Preventing double distribution or replayed payouts.
- Enforcing non-bypassable settlement authorization.
- Eliminating reentrancy and malicious token attack vectors.
- Maintaining transient custody (0 funds held by distributor between calls).

---

## 2. Recipient Authority & Derivation

### Developer Recipient
- Derived strictly from `EscrowRecord.beneficiary` on-chain (and corresponding `settlement.beneficiary_address` in the authorized settlement).
- API callers cannot specify or override the developer recipient address.
- Verified against registered agent creator identity in `SettlementAuthorizationEngine`.

### Staking & DAO Recipients
- Configured authoritatively via immutable platform deployment addresses (`RevenueDistributor.stakerRecipientAddress` and `RevenueDistributor.daoRecipientAddress`).
- Immutable in smart contracts; validated on backend against platform configuration.
- Per-settlement requests cannot supply or modify staker or DAO addresses.

### Duplicate Recipient Prevention
To prevent accidental misconfiguration or self-dealing exploits:
- `stakerRecipient != daoRecipient` is enforced at deployment.
- `developerRecipient != stakerRecipient` and `developerRecipient != daoRecipient` are enforced during distribution. Any attempt to use duplicate addresses reverts with `DuplicateRecipient`.

---

## 3. Access Control & Authorization

- **Escrow-Only Execution:** `RevenueDistributor.distribute(...)` can only be invoked by `authorizedEscrow`. Any other caller reverts with `UnauthorizedCaller`.
- **Pre-requisite Settlement Authorization:** `DistributionService` consumes authorized settlements from `SettlementService`. It does not independently authorize payments.
- **Admin Control:** The distributor contract permits only a single binding of `authorizedEscrow` via `setEscrowContract`. Once set, it cannot be changed.

---

## 4. Reentrancy & Token Safety

- **ReentrancyGuard:** Both `Escrow.sol` and `RevenueDistributor.sol` utilize OpenZeppelin `ReentrancyGuard` (`nonReentrant` modifier).
- **SafeERC20:** All token transfers utilize `SafeERC20.safeTransfer` to handle non-compliant ERC20 implementations (reverting on failure).
- **Token Whitelisting:** On Base Sepolia and Base Mainnet, official Circle USDC addresses are verified at constructor time. Arbitrary tokens revert with `InvalidPaymentToken`.
- **Decimals Check:** On local networks, tokens must implement 6 decimals.
- **Checks-Effects-Interactions:** State transitions in Escrow and Distributor are committed prior to external token transfers.

---

## 5. Mainnet Hard Stop

Base Mainnet settlement and distribution remain strictly disabled:
- `CHAIN_ID_BASE_MAINNET` (8453) checks fail-closed with `DistributionMainnetBlockedError`.
- `BLOCK_MAINNET_SETTLEMENT = true` is enforced globally across backend services.
- `LocalAccountSigner` is prohibited from mainnet transaction submission.

---

## 6. Staking & DAO Scope Boundaries

- **Staking Scope:** Only the 10% fund transfer to the designated staking pool address is implemented. Staking mechanics, yield, lockups, slashing, and claims belong strictly to future phases.
- **DAO Scope:** Only the 5% fund transfer to the designated DAO treasury address is implemented. Governance proposals, voting, and quorum belong strictly to future phases.
