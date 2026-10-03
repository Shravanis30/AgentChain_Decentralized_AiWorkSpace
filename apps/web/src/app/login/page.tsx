"use client";

import React from "react";
import Link from "next/link";
import { useAccount, useConnect } from "wagmi";
import { useAuth } from "@/context/AuthContext";
import { Button } from "@/components/ui/button";
import { Card, CardHeader, CardTitle, CardDescription, CardContent, CardFooter } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { ShieldCheck, Wallet as WalletIcon, ArrowRight, CheckCircle2, Lock, KeyRound, AlertCircle, Loader2 } from "lucide-react";

export default function LoginPage() {
  const { address, isConnected } = useAccount();
  const { connect, connectors, isPending: isConnecting } = useConnect();
  const { user, isAuthenticated, isLoading, isSigning, error, signInWithEthereum, signOut } = useAuth();

  const primaryConnector = connectors[0];

  return (
    <div className="max-w-4xl mx-auto px-4 py-16 space-y-12">
      <div className="text-center space-y-4 max-w-xl mx-auto">
        <div className="inline-flex items-center gap-2 px-3 py-1 rounded-full bg-primary/10 border border-primary/20 text-xs font-medium text-primary">
          <ShieldCheck className="w-3.5 h-3.5" />
          <span>Production Identity & Session Security</span>
        </div>
        <h1 className="text-4xl font-extrabold tracking-tight text-foreground sm:text-5xl">
          Sign In with Ethereum
        </h1>
        <p className="text-muted-foreground text-sm sm:text-base">
          AgentChain never trusts arbitrary frontend wallet addresses. Identity is established through cryptographic
          EIP-4361 verification and secure server-side sessions.
        </p>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-8 items-start">
        {/* Interactive Authentication Card */}
        <Card className="border-border bg-card shadow-xl relative overflow-hidden">
          <div className="absolute top-0 left-0 right-0 h-1 bg-gradient-to-r from-primary/50 via-primary to-accent" />
          <CardHeader>
            <CardTitle className="text-xl">Authentication Portal</CardTitle>
            <CardDescription>
              Connect your EVM wallet and verify cryptographic ownership.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-6">
            {error && (
              <div className="p-3 rounded-lg bg-destructive/10 border border-destructive/20 text-destructive text-xs flex items-start gap-2">
                <AlertCircle className="w-4 h-4 shrink-0 mt-0.5" />
                <span>{error}</span>
              </div>
            )}

            {/* State: Authenticated */}
            {isAuthenticated && user ? (
              <div className="space-y-4 p-4 rounded-xl bg-secondary/40 border border-border">
                <div className="flex items-center gap-3">
                  <div className="w-10 h-10 rounded-full bg-emerald-500/10 border border-emerald-500/20 flex items-center justify-center text-emerald-500">
                    <CheckCircle2 className="w-5 h-5" />
                  </div>
                  <div>
                    <p className="text-sm font-semibold text-foreground">Authenticated Session Active</p>
                    <p className="text-xs font-mono text-muted-foreground">
                      {user.primary_wallet?.address.slice(0, 8)}...{user.primary_wallet?.address.slice(-6)}
                    </p>
                  </div>
                </div>

                <div className="grid grid-cols-2 gap-2 text-xs pt-2 border-t border-border">
                  <div>
                    <span className="text-muted-foreground">Assigned Role:</span>
                    <p className="font-semibold text-foreground">{user.primary_role}</p>
                  </div>
                  <div>
                    <span className="text-muted-foreground">Session Cookie:</span>
                    <p className="font-semibold text-emerald-500">HttpOnly / Verified</p>
                  </div>
                </div>

                <div className="flex gap-2 pt-2">
                  <Link href="/dashboard" className="flex-1">
                    <Button className="w-full gap-2" size="sm">
                      Go to Dashboard <ArrowRight className="w-4 h-4" />
                    </Button>
                  </Link>
                  <Button variant="outline" size="sm" onClick={() => signOut()}>
                    Sign Out
                  </Button>
                </div>
              </div>
            ) : !isConnected ? (
              /* State: Wallet not connected */
              <div className="space-y-4 text-center py-6">
                <div className="w-16 h-16 rounded-full bg-secondary/60 border border-border flex items-center justify-center mx-auto text-muted-foreground">
                  <WalletIcon className="w-8 h-8" />
                </div>
                <div className="space-y-1">
                  <p className="text-sm font-medium">Step 1: Connect Injected Wallet</p>
                  <p className="text-xs text-muted-foreground">
                    Connect MetaMask or any EVM compatible browser wallet.
                  </p>
                </div>
                <Button
                  className="w-full gap-2 shadow-lg shadow-primary/20"
                  disabled={isConnecting}
                  onClick={() => primaryConnector && connect({ connector: primaryConnector })}
                >
                  <WalletIcon className="w-4 h-4" />
                  {isConnecting ? "Connecting..." : "Connect MetaMask"}
                </Button>
              </div>
            ) : (
              /* State: Wallet connected, awaiting SIWE */
              <div className="space-y-4 text-center py-4">
                <div className="w-16 h-16 rounded-full bg-primary/10 border border-primary/20 flex items-center justify-center mx-auto text-primary">
                  <KeyRound className="w-8 h-8" />
                </div>
                <div className="space-y-1">
                  <p className="text-sm font-medium">Step 2: Sign EIP-4361 Message</p>
                  <p className="text-xs font-mono text-muted-foreground">
                    Connected: {address?.slice(0, 6)}...{address?.slice(-4)}
                  </p>
                </div>
                <p className="text-xs text-muted-foreground text-left bg-secondary/30 p-3 rounded-lg border border-border">
                  The backend has generated a secure, single-use nonce for this wallet. Sign the message to prove wallet ownership and establish your session.
                </p>
                <Button
                  className="w-full gap-2 shadow-lg shadow-primary/20"
                  disabled={isSigning || isLoading}
                  onClick={() => signInWithEthereum()}
                >
                  {isSigning ? (
                    <>
                      <Loader2 className="w-4 h-4 animate-spin" />
                      Awaiting Signature in Wallet...
                    </>
                  ) : (
                    <>
                      Sign In with Ethereum
                      <ArrowRight className="w-4 h-4" />
                    </>
                  )}
                </Button>
              </div>
            )}
          </CardContent>
        </Card>

        {/* Technical Architecture Info */}
        <div className="space-y-6">
          <Card className="border-border bg-card/60 backdrop-blur-sm">
            <CardHeader className="pb-3">
              <CardTitle className="text-base flex items-center gap-2">
                <Lock className="w-4 h-4 text-primary" />
                Cryptographic Authentication Pipeline
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-4 text-xs text-muted-foreground">
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

          <div className="p-4 rounded-xl bg-secondary/30 border border-border space-y-2 text-xs">
            <p className="font-semibold text-foreground">Supported Chains</p>
            <div className="flex flex-wrap gap-2">
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
