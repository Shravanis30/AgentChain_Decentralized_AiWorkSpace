"use client";

import React from "react";
import { useAccount, useConnect, useDisconnect } from "wagmi";
import { Button } from "@/components/ui/button";
import { Wallet as WalletIcon, LogOut, CheckCircle2 } from "lucide-react";

export function WalletConnectButton() {
  const { address, isConnected } = useAccount();
  const { connect, connectors, isPending } = useConnect();
  const { disconnect } = useDisconnect();

  if (isConnected && address) {
    return (
      <div className="flex items-center gap-2">
        <div className="flex items-center gap-1.5 px-3 py-1.5 rounded-full bg-secondary/80 border border-border text-xs font-mono">
          <span className="w-2 h-2 rounded-full bg-emerald-500 animate-pulse" />
          {address.slice(0, 6)}...{address.slice(-4)}
        </div>
        <Button
          variant="ghost"
          size="sm"
          onClick={() => disconnect()}
          title="Disconnect Wallet"
          className="text-muted-foreground hover:text-destructive px-2"
        >
          <LogOut className="w-4 h-4" />
        </Button>
      </div>
    );
  }

  const primaryConnector = connectors[0];

  return (
    <Button
      variant="outline"
      size="sm"
      disabled={isPending}
      onClick={() => primaryConnector && connect({ connector: primaryConnector })}
      className="gap-2 border-primary/30 hover:border-primary/60"
    >
      <WalletIcon className="w-4 h-4 text-primary" />
      {isPending ? "Connecting..." : "Connect Wallet"}
    </Button>
  );
}
