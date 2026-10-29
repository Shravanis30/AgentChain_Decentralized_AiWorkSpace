"use client";

import React from "react";
import { useAccount, useDisconnect, useSwitchChain } from "wagmi";
import { useAuth } from "@/context/AuthContext";
import { Button } from "@/components/ui/button";
import { Card, CardHeader, CardTitle, CardDescription, CardContent, CardFooter } from "@/components/ui/card";
import { ShieldCheck, AlertCircle, Loader2, ArrowRight, Network, X, Sparkles } from "lucide-react";

const SUPPORTED_CHAIN_IDS = [31337, 84532, 8453];

export function AuthenticationModal() {
  const { isConnected, address, chainId } = useAccount();
  const { disconnect } = useDisconnect();
  const { chains, switchChain } = useSwitchChain();
  const { isAuthenticated, isSigning, isDevWalletSigning, error, signInWithEthereum, signInWithDevWallet, clearError } = useAuth();

  if (!isConnected || isAuthenticated) {
    return null;
  }

  const isChainSupported = chainId ? SUPPORTED_CHAIN_IDS.includes(chainId) : true;
  const activeChainName = chains.find((c) => c.id === chainId)?.name || (chainId ? `Chain ID ${chainId}` : "Local / Anvil");

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-background/80 backdrop-blur-sm p-3 sm:p-4 overflow-y-auto animate-in fade-in duration-200">
      <Card className="max-w-md w-full border-border bg-card shadow-2xl my-auto max-h-[92vh] flex flex-col relative">
        <button
          onClick={() => {
            clearError();
            disconnect();
          }}
          className="absolute top-3.5 right-3.5 p-1.5 rounded-full text-muted-foreground hover:text-foreground hover:bg-secondary/80 transition-colors"
          title="Disconnect & Close"
          aria-label="Disconnect & Close"
        >
          <X className="w-4 h-4" />
        </button>

        <CardHeader className="space-y-2 text-center pb-3 p-4 sm:p-6 shrink-0">
          <div className="w-10 h-10 sm:w-12 sm:h-12 rounded-full bg-primary/10 border border-primary/20 flex items-center justify-center mx-auto text-primary">
            <ShieldCheck className="w-5 h-5 sm:w-6 sm:h-6" />
          </div>
          <CardTitle className="text-lg sm:text-xl font-bold">Sign-In with Ethereum</CardTitle>
          <CardDescription className="text-xs sm:text-sm">
            Wallet connected: <span className="font-mono text-foreground font-medium">{address?.slice(0, 6)}...{address?.slice(-4)}</span>.
            Sign a cryptographic message to authenticate your AgentChain session.
          </CardDescription>
        </CardHeader>

        <CardContent className="space-y-3 sm:space-y-4 p-4 sm:p-6 pt-0 overflow-y-auto">
          <div className="rounded-lg bg-secondary/50 border border-border p-3 text-xs text-muted-foreground space-y-1.5">
            <div className="flex justify-between items-center font-mono">
              <span>Standard:</span>
              <span className="text-foreground">EIP-4361 (SIWE)</span>
            </div>
            <div className="flex justify-between items-center font-mono">
              <span>Network:</span>
              <span className="text-foreground flex items-center gap-1 font-semibold truncate max-w-[160px]">
                <Network className="w-3 h-3 text-primary shrink-0" />
                <span className="truncate">{activeChainName}</span>
              </span>
            </div>
            <div className="flex justify-between items-center font-mono">
              <span>Protection:</span>
              <span className="text-foreground">Replay proof</span>
            </div>
            <div className="flex justify-between items-center font-mono">
              <span>Storage:</span>
              <span className="text-foreground">HttpOnly Cookie</span>
            </div>
          </div>

          {!isChainSupported && (
            <div className="rounded-lg bg-amber-500/10 border border-amber-500/20 p-2.5 text-xs text-amber-600 dark:text-amber-400 space-y-2">
              <div className="flex items-start gap-1.5 font-medium">
                <AlertCircle className="w-4 h-4 shrink-0 mt-0.5" />
                <span>Unsupported Network ({activeChainName}). Please switch to a supported network:</span>
              </div>
              <div className="flex flex-wrap gap-1.5 pt-1">
                <Button
                  size="sm"
                  variant="outline"
                  className="h-7 text-xs bg-card"
                  onClick={() => switchChain({ chainId: 31337 })}
                >
                  Local Anvil (31337)
                </Button>
                <Button
                  size="sm"
                  variant="outline"
                  className="h-7 text-xs bg-card"
                  onClick={() => switchChain({ chainId: 84532 })}
                >
                  Base Sepolia (84532)
                </Button>
              </div>
            </div>
          )}

          {chains.length > 0 && isChainSupported && (
            <div className="rounded-lg border border-border/60 bg-muted/30 p-2.5 text-xs space-y-1.5">
              <span className="text-muted-foreground block text-[11px] font-medium">Switch Target Network:</span>
              <div className="flex flex-wrap gap-1.5">
                {chains.map((chain) => (
                  <Button
                    key={chain.id}
                    variant={chainId === chain.id ? "default" : "outline"}
                    size="sm"
                    className="h-6 text-[11px] px-2"
                    onClick={() => switchChain({ chainId: chain.id })}
                  >
                    {chain.name}
                  </Button>
                ))}
              </div>
            </div>
          )}

          {error && (
            <div className="rounded-lg bg-destructive/10 border border-destructive/20 p-3 text-xs text-destructive flex items-start gap-2">
              <AlertCircle className="w-4 h-4 shrink-0 mt-0.5" />
              <div className="flex-1 break-words">
                <p className="font-medium">Authentication Error</p>
                <p className="opacity-90">{error}</p>
              </div>
            </div>
          )}
        </CardContent>

        <CardFooter className="flex flex-col gap-2 p-4 sm:p-6 pt-0 shrink-0">
          <Button
            className="w-full gap-2 shadow-lg shadow-primary/20"
            disabled={isSigning || isDevWalletSigning || !isChainSupported}
            onClick={() => {
              clearError();
              signInWithEthereum();
            }}
          >
            {isSigning ? (
              <>
                <Loader2 className="w-4 h-4 animate-spin shrink-0" />
                <span>Awaiting Wallet Signature...</span>
              </>
            ) : (
              <>
                <span>Sign SIWE Message</span>
                <ArrowRight className="w-4 h-4 shrink-0" />
              </>
            )}
          </Button>

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
            onClick={() => {
              clearError();
              signInWithDevWallet();
            }}
          >
            {isDevWalletSigning ? (
              <>
                <Loader2 className="w-3.5 h-3.5 animate-spin shrink-0 text-primary" />
                <span>Signing In with Dev Wallet...</span>
              </>
            ) : (
              <>
                <Sparkles className="w-3.5 h-3.5 text-primary shrink-0" />
                <span>1-Click Sign In with Dev Wallet (Demo Mode)</span>
              </>
            )}
          </Button>

          <Button
            variant="ghost"
            size="sm"
            className="w-full text-xs text-muted-foreground hover:text-foreground h-7"
            onClick={() => {
              clearError();
              disconnect();
            }}
          >
            Disconnect Wallet & Dismiss
          </Button>
        </CardFooter>
      </Card>
    </div>
  );
}
