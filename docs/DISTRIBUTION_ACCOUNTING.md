# Revenue Distribution Accounting — AgentChain Phase 6.2C

## 1. Fundamental Economic Invariant

Every released escrow payout must obey the strict financial conservation invariant:

$$\text{gross\_amount} = \text{developer\_amount} + \text{staker\_amount} + \text{dao\_amount}$$

Under no circumstances may the sum of allocations deviate by even a single base unit ($10^{-6}$ USDC) from the gross amount.

---

## 2. Integer Arithmetic Specification

All computations are performed entirely in token base units ($1 \text{ USDC} = 1,000,000 \text{ base units}$). Floating-point arithmetic is strictly prohibited at both the smart contract and backend levels.

For any gross amount $A \in \mathbb{N}^+$:

1. **Developer Allocation (85%):**
   $$\text{developer\_amount} = \lfloor \frac{A \times 85}{100} \rfloor$$

2. **Staker Allocation (10%):**
   $$\text{staker\_amount} = \lfloor \frac{A \times 10}{100} \rfloor$$

3. **DAO Treasury Allocation (Exact Remainder):**
   $$\text{dao\_amount} = A - \text{developer\_amount} - \text{staker\_amount}$$

### Remainder Allocation Rationale
Calculating all three shares using independent integer division ($\lfloor A \times 5 / 100 \rfloor$) would result in rounding loss (leakage) whenever $A \pmod{100} \neq 0$. By defining the DAO Treasury allocation as the exact remainder ($A - \text{developer} - \text{staker}$), conservation is mathematically guaranteed for every possible `uint256` value:

$$\text{developer\_amount} + \text{staker\_amount} + \text{dao\_amount} \equiv A \quad \forall A \in \mathbb{N}$$

---

## 3. Boundary & Edge Case Table

| Gross Amount ($A$) | Developer ($85\%$) | Staker ($10\%$) | DAO ($5\% + \text{rem}$) | Sum ($dev + stk + dao$) | Conservation Verified |
|---|---|---|---|---|---|
| $0$ | *Reverts* | *Reverts* | *Reverts* | — | Reverts (`InvalidAmount`) |
| $1$ | $0$ | $0$ | $1$ | $1$ | ✓ Exact |
| $2$ | $1$ | $0$ | $1$ | $2$ | ✓ Exact |
| $3$ | $2$ | $0$ | $1$ | $3$ | ✓ Exact |
| $99$ | $84$ | $9$ | $6$ | $99$ | ✓ Exact |
| $100$ | $85$ | $10$ | $5$ | $100$ | ✓ Exact |
| $101$ | $85$ | $10$ | $6$ | $101$ | ✓ Exact |
| $777,777$ | $661,110$ | $77,777$ | $38,890$ | $777,777$ | ✓ Exact |
| $999,999$ | $849,999$ | $99,999$ | $50,001$ | $999,999$ | ✓ Exact |
| $1,000,000$ ($1$ USDC) | $850,000$ | $100,000$ | $50,000$ | $1,000,000$ | ✓ Exact |
| $1,000,001$ | $850,000$ | $100,000$ | $50,001$ | $1,000,001$ | ✓ Exact |
| $10,000,000,000 \times 10^6$ | $8.5 \times 10^{15}$ | $1.0 \times 10^{15}$ | $0.5 \times 10^{15}$ | $10^{16}$ | ✓ Exact |

---

## 4. On-Chain and Off-Chain Parity

- **Solidity Contract (`RevenueDistributor.sol`):**
  Enforces `if (devAmount + stkAmount + daoAmount != grossAmount) revert ConservationInvariantViolated();` before emitting `DistributionExecuted` and triggering `safeTransfer`.
- **Python Backend (`compute_85_10_5_split`):**
  Enforces identical integer arithmetic and asserts conservation prior to persisting records.
- **Indexer:**
  Extracts `grossAmount`, `developerAmount`, `stakerAmount`, `daoAmount` from canonical event parameters and asserts parity.
