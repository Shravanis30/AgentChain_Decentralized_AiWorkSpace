"use client";

import React from "react";
import Link from "next/link";
import { useAuth } from "@/context/AuthContext";
import { Button } from "@/components/ui/button";
import { Card, CardHeader, CardTitle, CardDescription, CardContent, CardFooter } from "@/components/ui/card";
import { ShieldAlert, Loader2, KeyRound } from "lucide-react";

interface AuthGuardProps {
  children: React.ReactNode;
  allowedRoles?: string[];
}

export function AuthGuard({ children, allowedRoles }: AuthGuardProps) {
  const { user, isAuthenticated, isLoading } = useAuth();

  if (isLoading) {
    return (
      <div className="min-h-[50vh] flex flex-col items-center justify-center space-y-4">
        <Loader2 className="w-8 h-8 animate-spin text-primary" />
        <p className="text-sm text-muted-foreground">Verifying authentication session...</p>
      </div>
    );
  }

  if (!isAuthenticated || !user) {
    return (
      <div className="max-w-md mx-auto my-16 px-4">
        <Card className="border-border bg-card shadow-lg text-center">
          <CardHeader className="space-y-2">
            <div className="w-12 h-12 rounded-full bg-primary/10 border border-primary/20 flex items-center justify-center mx-auto text-primary">
              <KeyRound className="w-6 h-6" />
            </div>
            <CardTitle className="text-xl">Authentication Required</CardTitle>
            <CardDescription>
              This section of AgentChain requires a verified Sign-In with Ethereum (SIWE) session.
            </CardDescription>
          </CardHeader>
          <CardContent className="text-xs text-muted-foreground">
            Wallet connection alone is not sufficient. Please sign in to verify wallet ownership.
          </CardContent>
          <CardFooter className="flex justify-center">
            <Link href="/login">
              <Button className="gap-2">
                Sign In with Wallet
              </Button>
            </Link>
          </CardFooter>
        </Card>
      </div>
    );
  }

  if (allowedRoles && allowedRoles.length > 0) {
    const hasRole = allowedRoles.includes(user.primary_role) || user.primary_role === "ADMIN";
    if (!hasRole) {
      return (
        <div className="max-w-md mx-auto my-16 px-4">
          <Card className="border-destructive/30 bg-card shadow-lg text-center">
            <CardHeader className="space-y-2">
              <div className="w-12 h-12 rounded-full bg-destructive/10 border border-destructive/20 flex items-center justify-center mx-auto text-destructive">
                <ShieldAlert className="w-6 h-6" />
              </div>
              <CardTitle className="text-xl">Access Denied</CardTitle>
              <CardDescription>
                Your account ({user.primary_role}) does not have permission to view this resource.
              </CardDescription>
            </CardHeader>
            <CardContent className="text-xs text-muted-foreground">
              Required roles: {allowedRoles.join(", ")}
            </CardContent>
            <CardFooter className="flex justify-center">
              <Link href="/dashboard">
                <Button variant="outline">Back to Dashboard</Button>
              </Link>
            </CardFooter>
          </Card>
        </div>
      );
    }
  }

  return <>{children}</>;
}
