"use client";

import React from "react";
import { useAccount } from "wagmi";
import { useAuth } from "@/context/AuthContext";
import { Button } from "@/components/ui/button";
import { Card, CardHeader, CardTitle, CardDescription, CardContent, CardFooter } from "@/components/ui/card";
import { ShieldCheck, AlertCircle, Loader2, ArrowRight } from "lucide-react";

export function AuthenticationModal() {
  const { isConnected, address } = useAccount();
  const { isAuthenticated, isSigning, error, signInWithEthereum, clearError } = useAuth();

  if (!isConnected || isAuthenticated) {
    return null;
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-background/80 backdrop-blur-sm p-4 animate-in fade-in duration-200">
      <Card className="max-w-md w-full border-border bg-card shadow-2xl">
        <CardHeader className="space-y-2 text-center pb-4">
          <div className="w-12 h-12 rounded-full bg-primary/10 border border-primary/20 flex items-center justify-center mx-auto text-primary">
            <ShieldCheck className="w-6 h-6" />
          </div>
          <CardTitle className="text-xl font-bold">Sign-In with Ethereum</CardTitle>
          <CardDescription className="text-sm">
            Wallet connected: <span className="font-mono text-foreground font-medium">{address?.slice(0, 6)}...{address?.slice(-4)}</span>.
            Sign a cryptographic message to authenticate your AgentChain session.
          </CardDescription>
        </CardHeader>

        <CardContent className="space-y-4">
          <div className="rounded-lg bg-secondary/50 border border-border p-3 text-xs text-muted-foreground space-y-1">
            <div className="flex justify-between font-mono">
              <span>Standard:</span>
              <span className="text-foreground">EIP-4361 (SIWE)</span>
            </div>
            <div className="flex justify-between font-mono">
              <span>Protection:</span>
              <span className="text-foreground">Nonce replay + Session fixation</span>
            </div>
            <div className="flex justify-between font-mono">
              <span>Storage:</span>
              <span className="text-foreground">HttpOnly Secure Cookie</span>
            </div>
          </div>

          {error && (
            <div className="rounded-lg bg-destructive/10 border border-destructive/20 p-3 text-xs text-destructive flex items-start gap-2">
              <AlertCircle className="w-4 h-4 shrink-0 mt-0.5" />
              <div className="flex-1">
                <p className="font-medium">Authentication Error</p>
                <p className="opacity-90">{error}</p>
              </div>
            </div>
          )}
        </CardContent>

        <CardFooter className="flex flex-col gap-2">
          <Button
            className="w-full gap-2 shadow-lg shadow-primary/20"
            disabled={isSigning}
            onClick={() => {
              clearError();
              signInWithEthereum();
            }}
          >
            {isSigning ? (
              <>
                <Loader2 className="w-4 h-4 animate-spin" />
                Awaiting Wallet Signature...
              </>
            ) : (
              <>
                Sign SIWE Message
                <ArrowRight className="w-4 h-4" />
              </>
            )}
          </Button>
          <p className="text-center text-[11px] text-muted-foreground">
            No transaction fees. No private keys are ever shared.
          </p>
        </CardFooter>
      </Card>
    </div>
  );
}
