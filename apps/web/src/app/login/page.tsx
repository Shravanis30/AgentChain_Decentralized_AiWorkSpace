"use client";

import React, { useState, useEffect } from "react";
import Link from "next/link";
import { useAccount, useConnect, useDisconnect, useSwitchChain } from "wagmi";
import { useAuth } from "@/context/AuthContext";
import { Button } from "@/components/ui/button";
import { Card, CardHeader, CardTitle, CardDescription, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import {
  ShieldCheck,
  Wallet as WalletIcon,
  ArrowRight,
  CheckCircle2,
  Lock,
  KeyRound,
  AlertCircle,
  Loader2,
  Sparkles,
  Network,
  LogOut,
} from "lucide-react";

const SUPPORTED_CHAIN_IDS = [31337, 84532, 8453];

export default function LoginPage() {
  const { address, isConnected, chainId } = useAccount();
  const { connect, connectors, isPending: isConnecting, error: connectError } = useConnect();
  const { disconnect } = useDisconnect();
  const { switchChain } = useSwitchChain();
  const {
    user,
    isAuthenticated,
    isLoading,
    isSigning,
    isDevWalletSigning,
    error,
    signInWithEthereum,
    signInWithDevWallet,
    signOut,
  } = useAuth();

  const [hasEthereum, setHasEthereum] = useState<boolean>(true);
  const [localConnectError, setLocalConnectError] = useState<string | null>(null);

  useEffect(() => {
    if (typeof window !== "undefined") {
      setHasEthereum(Boolean((window as unknown as { ethereum?: unknown }).ethereum));
    }
  }, []);

  const metamaskConnector =
    connectors.find((c) => c.id === "io.metamask" || c.name.toLowerCase().includes("metamask")) ||
    connectors[0];

  const isChainSupported = chainId ? SUPPORTED_CHAIN_IDS.includes(chainId) : true;

  const handleConnectMetaMask = async () => {
    setLocalConnectError(null);
    if (!metamaskConnector) {
      setLocalConnectError("No wallet connector found. Please install MetaMask or use Dev Wallet.");
      return;
    }
    try {
      connect({ connector: metamaskConnector });
    } catch (err: unknown) {
      console.error("MetaMask connection error:", err);
      setLocalConnectError(err instanceof Error ? err.message : "Failed to connect to MetaMask");
    }
  };

  const activeError = error || localConnectError || (connectError ? connectError.message : null);

  return (
    <div className="max-w-4xl mx-auto px-4 sm:px-6 py-8 sm:py-12 md:py-16 space-y-8 sm:space-y-12">
      <div className="text-center space-y-3 sm:space-y-4 max-w-xl mx-auto">
        <div className="inline-flex items-center gap-2 px-3 py-1 rounded-full bg-primary/10 border border-primary/20 text-xs font-medium text-primary">
          <ShieldCheck className="w-3.5 h-3.5 shrink-0" />
          <span>Production Identity & Session Security</span>
        </div>
        <h1 className="text-2xl sm:text-4xl md:text-5xl font-extrabold tracking-tight text-foreground break-words">
          Sign In with Ethereum
        </h1>
        <p className="text-muted-foreground text-xs sm:text-sm md:text-base">
          AgentChain never trusts arbitrary frontend wallet addresses. Identity is established through cryptographic
          EIP-4361 verification and secure server-side sessions.
        </p>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-6 sm:gap-8 items-start">
        {/* Interactive Authentication Card */}
        <Card className="border-border bg-card shadow-xl relative overflow-hidden">
          <div className="absolute top-0 left-0 right-0 h-1 bg-gradient-to-r from-primary/50 via-primary to-accent" />
          <CardHeader>
            <CardTitle className="text-lg sm:text-xl">Authentication Portal</CardTitle>
            <CardDescription className="text-xs sm:text-sm">
              Connect your EVM wallet or use the automated Dev Wallet demo mode.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-5 sm:space-y-6">
            {activeError && (
              <div className="p-3 rounded-lg bg-destructive/10 border border-destructive/20 text-destructive text-xs flex items-start gap-2 break-words">
                <AlertCircle className="w-4 h-4 shrink-0 mt-0.5" />
                <span className="flex-1">{activeError}</span>
              </div>
            )}

            {/* State: Authenticated */}
            {isAuthenticated && user ? (
              <div className="space-y-4 p-3.5 sm:p-4 rounded-xl bg-secondary/40 border border-border">
                <div className="flex items-center gap-3">
                  <div className="w-10 h-10 rounded-full bg-emerald-500/10 border border-emerald-500/20 flex items-center justify-center text-emerald-500 shrink-0">
                    <CheckCircle2 className="w-5 h-5" />
                  </div>
                  <div className="min-w-0">
                    <p className="text-sm font-semibold text-foreground">Authenticated Session Active</p>
                    <p className="text-xs font-mono text-muted-foreground truncate">
                      {user.primary_wallet?.address}
                    </p>
                  </div>
                </div>

                <div className="grid grid-cols-2 gap-2 text-xs pt-2 border-t border-border">
                  <div>
                    <span className="text-muted-foreground">Assigned Role:</span>
                    <p className="font-semibold text-foreground truncate">{user.primary_role}</p>
                  </div>
                  <div>
                    <span className="text-muted-foreground">Session Cookie:</span>
                    <p className="font-semibold text-emerald-500">HttpOnly Verified</p>
                  </div>
                </div>

                <div className="flex flex-col sm:flex-row gap-2 pt-2">
                  <Link href="/dashboard" className="flex-1">
                    <Button className="w-full gap-2" size="sm">
                      Go to Dashboard <ArrowRight className="w-4 h-4 shrink-0" />
                    </Button>
                  </Link>
                  <Button variant="outline" size="sm" onClick={() => signOut()} className="w-full sm:w-auto">
                    Sign Out
                  </Button>
                </div>
              </div>
            ) : !isConnected ? (
              /* State: Wallet not connected */
              <div className="space-y-4 text-center py-2 sm:py-4">
                <div className="w-14 h-14 sm:w-16 sm:h-16 rounded-full bg-secondary/60 border border-border flex items-center justify-center mx-auto text-muted-foreground">
                  <WalletIcon className="w-7 h-7 sm:w-8 sm:h-8" />
                </div>
                <div className="space-y-1">
                  <p className="text-sm font-medium">Step 1: Connect Injected Wallet</p>
                  <p className="text-xs text-muted-foreground">
                    Connect MetaMask or any EVM compatible browser wallet.
                  </p>
                </div>

                {!hasEthereum && (
                  <div className="p-2.5 rounded-lg bg-muted/50 border border-border text-left text-xs text-muted-foreground space-y-1">
                    <div className="flex items-center gap-1.5 font-medium text-foreground">
                      <AlertCircle className="w-3.5 h-3.5 text-amber-500" />
                      <span>MetaMask Extension Not Detected</span>
                    </div>
                    <p className="text-[11px]">
                      If you do not have MetaMask installed in this browser, use the 1-Click Dev Wallet mode below for full application access.
                    </p>
                  </div>
                )}

                <Button
                  className="w-full gap-2 shadow-lg shadow-primary/20"
                  disabled={isConnecting || isDevWalletSigning}
                  onClick={handleConnectMetaMask}
                >
                  <WalletIcon className="w-4 h-4 shrink-0" />
                  <span>{isConnecting ? "Connecting to MetaMask..." : "Connect MetaMask"}</span>
                </Button>

                <div className="relative w-full my-2 text-center">
                  <div className="absolute inset-0 flex items-center">
                    <span className="w-full border-t border-border" />
                  </div>
                  <span className="relative bg-card px-2 text-[10px] uppercase text-muted-foreground font-semibold">
                    or instant demo access
                  </span>
                </div>

                {/* 1-Click Dev Wallet Authentication Button */}
                <div className="rounded-xl border border-primary/20 bg-primary/5 p-3 space-y-2 text-left">
                  <div className="flex items-center justify-between">
                    <span className="text-xs font-semibold text-foreground flex items-center gap-1.5">
                      <Sparkles className="w-3.5 h-3.5 text-primary" />
                      Dev Wallet Mode
                    </span>
                    <Badge variant="outline" className="text-[10px] bg-primary/10 text-primary border-primary/30">
                      Role: ADMIN
                    </Badge>
                  </div>
                  <p className="text-[11px] text-muted-foreground">
                    Instantly signs in with the deterministic development account (<code>0x162dEB...</code>) using real cryptographic EIP-4361 verification on the backend. No extension required.
                  </p>
                  <Button
                    variant="outline"
                    size="sm"
                    className="w-full gap-2 border-primary/40 hover:bg-primary/10 text-xs font-medium"
                    disabled={isConnecting || isDevWalletSigning}
                    onClick={() => signInWithDevWallet()}
                  >
                    {isDevWalletSigning ? (
                      <>
                        <Loader2 className="w-3.5 h-3.5 animate-spin shrink-0 text-primary" />
                        <span>Verifying SIWE Session...</span>
                      </>
                    ) : (
                      <>
                        <Sparkles className="w-3.5 h-3.5 text-primary shrink-0" />
                        <span>1-Click Sign In with Dev Wallet</span>
                        <ArrowRight className="w-3.5 h-3.5 shrink-0 ml-auto" />
                      </>
                    )}
                  </Button>
                </div>
              </div>
            ) : (
              /* State: Wallet connected, awaiting SIWE */
              <div className="space-y-4 text-center py-2 sm:py-4">
                <div className="w-14 h-14 sm:w-16 sm:h-16 rounded-full bg-primary/10 border border-primary/20 flex items-center justify-center mx-auto text-primary">
                  <KeyRound className="w-7 h-7 sm:w-8 sm:h-8" />
                </div>
                <div className="space-y-1">
                  <p className="text-sm font-medium">Step 2: Sign EIP-4361 Message</p>
                  <p className="text-xs font-mono text-muted-foreground break-all">
                    Connected: {address?.slice(0, 8)}...{address?.slice(-6)}
                  </p>
                </div>

                {!isChainSupported && (
                  <div className="p-3 rounded-lg bg-amber-500/10 border border-amber-500/20 text-left text-xs text-amber-600 dark:text-amber-400 space-y-2">
                    <div className="flex items-start gap-1.5 font-medium">
                      <Network className="w-4 h-4 shrink-0 mt-0.5" />
                      <span>Wallet is on unsupported Chain ID {chainId}. Please switch network:</span>
                    </div>
                    <div className="flex flex-wrap gap-1.5 pt-1">
                      <Button
                        size="sm"
                        variant="outline"
                        className="h-7 text-xs bg-card"
                        onClick={() => switchChain({ chainId: 31337 })}
                      >
                        Switch to Local Anvil (31337)
                      </Button>
                      <Button
                        size="sm"
                        variant="outline"
                        className="h-7 text-xs bg-card"
                        onClick={() => switchChain({ chainId: 84532 })}
                      >
                        Switch to Base Sepolia (84532)
                      </Button>
                    </div>
                  </div>
                )}

                <p className="text-xs text-muted-foreground text-left bg-secondary/30 p-3 rounded-lg border border-border">
                  The backend has generated a secure, single-use nonce for this wallet. Sign the message to prove wallet ownership and establish your session.
                </p>

                <div className="flex flex-col sm:flex-row gap-2">
                  <Button
                    className="flex-1 gap-2 shadow-lg shadow-primary/20"
                    disabled={isSigning || isLoading || !isChainSupported}
                    onClick={() => signInWithEthereum()}
                  >
                    {isSigning ? (
                      <>
                        <Loader2 className="w-4 h-4 animate-spin shrink-0" />
                        <span>Awaiting Signature in Wallet...</span>
                      </>
                    ) : (
                      <>
                        <span>Sign In with Ethereum</span>
                        <ArrowRight className="w-4 h-4 shrink-0" />
                      </>
                    )}
                  </Button>
                  <Button
                    variant="outline"
                    size="default"
                    onClick={() => disconnect()}
                    className="gap-1.5 text-xs text-muted-foreground hover:text-destructive"
                    title="Disconnect Wallet"
                  >
                    <LogOut className="w-3.5 h-3.5" />
                    <span className="sm:hidden">Disconnect</span>
                  </Button>
                </div>

                <div className="relative w-full my-1 text-center">
                  <div className="absolute inset-0 flex items-center">
                    <span className="w-full border-t border-border" />
                  </div>
                  <span className="relative bg-card px-2 text-[10px] uppercase text-muted-foreground font-semibold">
                    or
                  </span>
                </div>

                <Button
                  variant="outline"
                  size="sm"
                  className="w-full gap-2 border-primary/30 hover:bg-primary/5 text-xs text-foreground"
                  disabled={isSigning || isDevWalletSigning}
                  onClick={() => signInWithDevWallet()}
                >
                  <Sparkles className="w-3.5 h-3.5 text-primary shrink-0" />
                  <span>1-Click Sign In with Dev Wallet</span>
                </Button>
              </div>
            )}
          </CardContent>
        </Card>

        {/* Technical Architecture Info */}
        <div className="space-y-6">
          <Card className="border-border bg-card/60 backdrop-blur-sm">
            <CardHeader className="pb-3">
              <CardTitle className="text-sm sm:text-base flex items-center gap-2">
                <Lock className="w-4 h-4 text-primary shrink-0" />
                Cryptographic Authentication Pipeline
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-3 sm:space-y-4 text-xs text-muted-foreground">
              <div className="flex items-start gap-3">
                <span className="flex-shrink-0 w-6 h-6 rounded-full bg-secondary text-foreground flex items-center justify-center font-bold text-xs">
                  1
                </span>
                <div>
                  <strong className="text-foreground">Server-Side Nonce:</strong> Backend generates high-entropy random nonces with 5-minute expiration stored in PostgreSQL.
                </div>
              </div>

              <div className="flex items-start gap-3">
                <span className="flex-shrink-0 w-6 h-6 rounded-full bg-secondary text-foreground flex items-center justify-center font-bold text-xs">
                  2
                </span>
                <div>
                  <strong className="text-foreground">EIP-4361 Message:</strong> Wagmi requests the user to sign the message bound to origin, domain, chain ID, and nonce.
                </div>
              </div>

              <div className="flex items-start gap-3">
                <span className="flex-shrink-0 w-6 h-6 rounded-full bg-secondary text-foreground flex items-center justify-center font-bold text-xs">
                  3
                </span>
                <div>
                  <strong className="text-foreground">Dual Verification:</strong> FastAPI recovers the signing address via <code>eth_account</code> and consumes the nonce in a transactional unit to eliminate replay attacks.
                </div>
              </div>

              <div className="flex items-start gap-3">
                <span className="flex-shrink-0 w-6 h-6 rounded-full bg-secondary text-foreground flex items-center justify-center font-bold text-xs">
                  4
                </span>
                <div>
                  <strong className="text-foreground">Secure Session:</strong> Session token hash (SHA-256) is persisted. Raw token is returned in an <code>HttpOnly, SameSite=Lax</code> cookie. Zero auth credentials in <code>localStorage</code>.
                </div>
              </div>
            </CardContent>
          </Card>

          <div className="p-3.5 sm:p-4 rounded-xl bg-secondary/30 border border-border space-y-2 text-xs">
            <p className="font-semibold text-foreground">Supported Chains</p>
            <div className="flex flex-wrap gap-1.5 sm:gap-2">
              <Badge variant="outline" className="bg-background">Local Anvil (31337)</Badge>
              <Badge variant="outline" className="bg-background">Base Sepolia (84532)</Badge>
              <Badge variant="outline" className="bg-background">Base Mainnet (8453)</Badge>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
