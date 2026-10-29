"use client";

import React from "react";
import { useAccount, useConnect, useDisconnect } from "wagmi";
import { Button } from "@/components/ui/button";
import { Wallet as WalletIcon, LogOut } from "lucide-react";

export function WalletConnectButton() {
  const { address, isConnected } = useAccount();
  const { connect, connectors, isPending } = useConnect();
  const { disconnect } = useDisconnect();

  if (isConnected && address) {
    return (
      <div className="flex items-center gap-1 sm:gap-2">
        <div className="flex items-center gap-1.5 px-2.5 sm:px-3 py-1 sm:py-1.5 rounded-full bg-secondary/80 border border-border text-xs font-mono">
          <span className="w-2 h-2 rounded-full bg-emerald-500 animate-pulse shrink-0" />
          <span className="hidden sm:inline">{address.slice(0, 6)}...{address.slice(-4)}</span>
          <span className="sm:hidden text-[11px]">{address.slice(0, 4)}..{address.slice(-2)}</span>
        </div>
        <Button
          variant="ghost"
          size="sm"
          onClick={() => disconnect()}
          title="Disconnect Wallet"
          aria-label="Disconnect Wallet"
          className="text-muted-foreground hover:text-destructive px-1.5 sm:px-2 h-8"
        >
          <LogOut className="w-3.5 h-3.5 sm:w-4 sm:h-4" />
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
      className="gap-1.5 sm:gap-2 px-2.5 sm:px-3 text-xs sm:text-sm border-primary/30 hover:border-primary/60 shrink-0"
    >
      <WalletIcon className="w-3.5 h-3.5 sm:w-4 sm:h-4 text-primary shrink-0" />
      <span>{isPending ? "Connecting..." : "Connect"}</span>
    </Button>
  );
}
