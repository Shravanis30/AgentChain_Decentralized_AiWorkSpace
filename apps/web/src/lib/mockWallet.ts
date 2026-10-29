import { privateKeyToAccount } from "viem/accounts";

/**
 * Deterministic developer wallet for automated browser testing,
 * headless evaluation, and demo mode.
 * Matches root .dev_wallet.txt
 */
export function getDevWalletAccount() {
  const devKey = "0xde19875165ee4184f097579dfd1bfaa80eebf69fa82121678c4b2b4c8bde0430" as const;
  return privateKeyToAccount(devKey);
}
