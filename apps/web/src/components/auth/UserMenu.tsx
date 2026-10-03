"use client";

import React from "react";
import Link from "next/link";
import { useAuth } from "@/context/AuthContext";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { LogOut, Shield, User as UserIcon } from "lucide-react";
import { WalletConnectButton } from "./WalletConnectButton";

export function UserMenu() {
  const { user, isAuthenticated, isLoading, signOut } = useAuth();

  if (isLoading) {
    return <div className="w-24 h-8 bg-muted/40 animate-pulse rounded-full" />;
  }

  if (!isAuthenticated || !user) {
    return (
      <div className="flex items-center gap-3">
        <Link href="/login">
          <Button variant="ghost" size="sm" className="text-sm">
            Sign In
          </Button>
        </Link>
        <WalletConnectButton />
      </div>
    );
  }

  const roleColor: Record<string, string> = {
    ADMIN: "bg-red-500/10 text-red-500 border-red-500/20",
    DEVELOPER: "bg-blue-500/10 text-blue-500 border-blue-500/20",
    AGENT_OPERATOR: "bg-purple-500/10 text-purple-500 border-purple-500/20",
    VERIFIER: "bg-amber-500/10 text-amber-500 border-amber-500/20",
    CLIENT: "bg-emerald-500/10 text-emerald-500 border-emerald-500/20",
  };

  const badgeClass = roleColor[user.primary_role] || "bg-secondary text-secondary-foreground";

  return (
    <div className="flex items-center gap-3">
      <div className="flex items-center gap-2 pl-3 pr-2 py-1 rounded-full bg-card border border-border text-xs shadow-sm">
        <div className="flex items-center gap-1.5 font-mono text-muted-foreground">
          <UserIcon className="w-3.5 h-3.5 text-primary" />
          <span>
            {user.primary_wallet
              ? `${user.primary_wallet.address.slice(0, 6)}...${user.primary_wallet.address.slice(-4)}`
              : "No Wallet"}
          </span>
        </div>
        <Badge variant="outline" className={`text-[10px] uppercase font-semibold ${badgeClass}`}>
          {user.primary_role}
        </Badge>
        <Button
          variant="ghost"
          size="sm"
          onClick={() => signOut()}
          className="h-6 w-6 p-0 rounded-full text-muted-foreground hover:text-destructive"
          title="Sign Out"
        >
          <LogOut className="w-3.5 h-3.5" />
        </Button>
      </div>
    </div>
  );
}
