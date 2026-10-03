"use client";

import React, { createContext, useContext, useEffect, useState, useCallback } from "react";
import { useAccount, useDisconnect, useSignMessage } from "wagmi";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { fetchAuthMe, fetchNonce, logoutUser, verifySignature, User, Wallet } from "@/lib/api";

interface AuthContextType {
  user: User | null;
  isAuthenticated: boolean;
  isLoading: boolean;
  isSigning: boolean;
  error: string | null;
  signInWithEthereum: () => Promise<void>;
  signOut: () => Promise<void>;
  clearError: () => void;
}

const AuthContext = createContext<AuthContextType | undefined>(undefined);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const { address, isConnected, chainId } = useAccount();
  const { disconnect } = useDisconnect();
  const { signMessageAsync } = useSignMessage();
  const queryClient = useQueryClient();

  const [isSigning, setIsSigning] = useState<boolean>(false);
  const [authError, setAuthError] = useState<string | null>(null);

  // Fetch authenticated user via TanStack Query
  const {
    data: user,
    isLoading: isUserLoading,
    refetch: refetchUser,
  } = useQuery({
    queryKey: ["auth_me"],
    queryFn: fetchAuthMe,
    retry: false,
    staleTime: 5 * 60 * 1000,
  });

  const isAuthenticated = !!user;

  // Invalidate session if connected account does not match authenticated user's wallet
  useEffect(() => {
    if (user && user.primary_wallet && address) {
      if (user.primary_wallet.address.toLowerCase() !== address.toLowerCase()) {
        // Connected wallet has switched accounts
        setAuthError(
          `Connected wallet (${address.slice(0, 6)}...${address.slice(-4)}) differs from authenticated user (${user.primary_wallet.address.slice(0, 6)}...${user.primary_wallet.address.slice(-4)}). Please sign in with the active wallet.`
        );
      } else {
        setAuthError(null);
      }
    }
  }, [address, user]);

  const signInWithEthereum = useCallback(async () => {
    if (!address || !isConnected) {
      setAuthError("Please connect your wallet first");
      return;
    }

    setIsSigning(true);
    setAuthError(null);

    try {
      const activeChainId = chainId || 31337;
      // 1. Backend generates cryptographically secure nonce
      const { nonce } = await fetchNonce(address, activeChainId);

      // 2. Construct EIP-4361 SIWE message
      const domain = window.location.host;
      const origin = window.location.origin;
      const issuedAt = new Date().toISOString();
      const expirationTime = new Date(Date.now() + 10 * 60 * 1000).toISOString();

      const message = `${domain} wants you to sign in with your Ethereum account:\n${address}\n\nSign in with Ethereum to AgentChain\n\nURI: ${origin}\nVersion: 1\nChain ID: ${activeChainId}\nNonce: ${nonce}\nIssued At: ${issuedAt}\nExpiration Time: ${expirationTime}`;

      // 3. Prompt user signature in wallet
      const signature = await signMessageAsync({ message });

      // 4. Send signature to backend for cryptographic verification & session creation
      await verifySignature(message, signature);

      // 5. Update user state
      await refetchUser();
    } catch (err: unknown) {
      console.error("SIWE Authentication failed:", err);
      let errMsg = "Authentication failed";
      if (err instanceof Error) {
        if (err.message.includes("User rejected") || err.message.includes("rejected the request")) {
          errMsg = "Signature request was rejected in wallet";
        } else {
          errMsg = err.message;
        }
      }
      setAuthError(errMsg);
    } finally {
      setIsSigning(false);
    }
  }, [address, isConnected, chainId, signMessageAsync, refetchUser]);

  const signOut = useCallback(async () => {
    try {
      await logoutUser();
    } catch (err) {
      console.warn("Logout API failed:", err);
    } finally {
      queryClient.setQueryData(["auth_me"], null);
      disconnect();
      setAuthError(null);
    }
  }, [queryClient, disconnect]);

  const clearError = () => setAuthError(null);

  return (
    <AuthContext.Provider
      value={{
        user: user || null,
        isAuthenticated,
        isLoading: isUserLoading,
        isSigning,
        error: authError,
        signInWithEthereum,
        signOut,
        clearError,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error("useAuth must be used within an AuthProvider");
  }
  return context;
}
