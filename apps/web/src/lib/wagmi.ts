import { http, createConfig, injected } from "wagmi";
import { base, baseSepolia, foundry, mainnet, sepolia } from "wagmi/chains";

export const config = createConfig({
  chains: [foundry, baseSepolia, base, mainnet, sepolia],
  connectors: [injected()],
  transports: {
    [foundry.id]: http("http://localhost:8545"),
    [baseSepolia.id]: http("https://sepolia.base.org"),
    [base.id]: http("https://mainnet.base.org"),
    [mainnet.id]: http("https://cloudflare-eth.com"),
    [sepolia.id]: http("https://rpc.sepolia.org"),
  },
  ssr: true,
});
