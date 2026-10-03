import { http, createConfig, injected } from "wagmi";
import { base, baseSepolia, foundry } from "wagmi/chains";

export const config = createConfig({
  chains: [foundry, baseSepolia, base],
  connectors: [injected()],
  transports: {
    [foundry.id]: http("http://localhost:8545"),
    [baseSepolia.id]: http("https://sepolia.base.org"),
    [base.id]: http("https://mainnet.base.org"),
  },
  ssr: true,
});
