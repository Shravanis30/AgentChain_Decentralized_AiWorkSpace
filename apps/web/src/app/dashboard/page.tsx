"use client";

import { useEffect, useState } from "react";
import { Card, CardHeader, CardTitle, CardDescription, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Activity, Database, HardDrive, RefreshCw, Server, Shield } from "lucide-react";
import { AuthGuard } from "@/components/auth/AuthGuard";


interface DependencyHealth {
  database: string;
  redis: string;
  qdrant: string;
  object_storage: string;
}

interface HealthResponse {
  status: "ok" | "degraded";
  version: string;
  dependencies: DependencyHealth;
}

export default function DashboardPage() {
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);
  const [lastChecked, setLastChecked] = useState<string>("");

  const fetchHealth = async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetch("http://localhost:8000/api/v1/health", { cache: "no-store" });
      if (!res.ok) {
        throw new Error(`HTTP error ${res.status}`);
      }
      const data: HealthResponse = await res.json();
      setHealth(data);
      setLastChecked(new Date().toLocaleTimeString());
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to fetch health status");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchHealth();
  }, []);

  const getStatusBadge = (statusStr?: string) => {
    if (statusStr === "connected" || statusStr === "ok") {
      return <Badge variant="success">Connected</Badge>;
    }
    return <Badge variant="destructive">{statusStr || "Unreachable"}</Badge>;
  };

  return (
    <AuthGuard>
      <div className="max-w-6xl mx-auto px-4 py-10 space-y-8">
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 border-b border-border pb-6">
        <div>
          <h1 className="text-3xl font-bold tracking-tight text-foreground">Infrastructure Status</h1>
          <p className="text-muted-foreground mt-1">
            Real-time telemetry and health monitoring of core AgentChain backend dependencies.
          </p>
        </div>
        <div className="flex items-center gap-3">
          {lastChecked && <span className="text-xs text-muted-foreground">Updated: {lastChecked}</span>}
          <Button onClick={fetchHealth} disabled={loading} variant="outline" size="sm" className="gap-2">
            <RefreshCw className={`w-4 h-4 ${loading ? "animate-spin" : ""}`} />
            Refresh
          </Button>
        </div>
      </div>

      {error && (
        <div className="p-4 rounded-lg bg-destructive/10 border border-destructive/20 text-destructive text-sm flex items-center gap-3">
          <Activity className="w-5 h-5 shrink-0" />
          <span>
            Unable to connect to FastAPI backend at <code>http://localhost:8000/api/v1/health</code>. Ensure backend
            is running. ({error})
          </span>
        </div>
      )}

      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6">
        {/* Overall Backend Status */}
        <Card className="border-border bg-card">
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-sm font-medium">FastAPI Backend</CardTitle>
            <Server className="h-4 w-4 text-muted-foreground" />
          </CardHeader>
          <CardContent className="space-y-2">
            <div className="text-2xl font-bold">
              {health?.status === "ok" ? "Healthy" : health?.status ? "Degraded" : "Offline"}
            </div>
            <div className="flex items-center justify-between text-xs text-muted-foreground">
              <span>Version: {health?.version || "0.1.0"}</span>
              {getStatusBadge(health?.status)}
            </div>
          </CardContent>
        </Card>

        {/* PostgreSQL Database */}
        <Card className="border-border bg-card">
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-sm font-medium">PostgreSQL 16</CardTitle>
            <Database className="h-4 w-4 text-muted-foreground" />
          </CardHeader>
          <CardContent className="space-y-2">
            <div className="text-2xl font-bold">Relational DB</div>
            <div className="flex items-center justify-between text-xs text-muted-foreground">
              <span>Port 5432</span>
              {getStatusBadge(health?.dependencies.database)}
            </div>
          </CardContent>
        </Card>

        {/* Redis Cache */}
        <Card className="border-border bg-card">
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-sm font-medium">Redis 7</CardTitle>
            <Activity className="h-4 w-4 text-muted-foreground" />
          </CardHeader>
          <CardContent className="space-y-2">
            <div className="text-2xl font-bold">State Cache</div>
            <div className="flex items-center justify-between text-xs text-muted-foreground">
              <span>Port 6379</span>
              {getStatusBadge(health?.dependencies.redis)}
            </div>
          </CardContent>
        </Card>

        {/* Qdrant Vector DB */}
        <Card className="border-border bg-card">
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-sm font-medium">Qdrant Vector DB</CardTitle>
            <HardDrive className="h-4 w-4 text-muted-foreground" />
          </CardHeader>
          <CardContent className="space-y-2">
            <div className="text-2xl font-bold">Vector Search</div>
            <div className="flex items-center justify-between text-xs text-muted-foreground">
              <span>Port 6333</span>
              {getStatusBadge(health?.dependencies.qdrant)}
            </div>
          </CardContent>
        </Card>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        {/* MinIO Object Storage */}
        <Card className="border-border bg-card">
          <CardHeader>
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <HardDrive className="w-5 h-5 text-primary" />
                <CardTitle className="text-lg">MinIO / S3 Object Storage</CardTitle>
              </div>
              {getStatusBadge(health?.dependencies.object_storage)}
            </div>
            <CardDescription>
              Stores immutable task deliverable artifacts, code files, and raw execution traces.
            </CardDescription>
          </CardHeader>
          <CardContent className="text-sm text-muted-foreground space-y-1">
            <div><strong>Endpoint:</strong> http://localhost:9000</div>
            <div><strong>Console UI:</strong> <a href="http://localhost:9001" target="_blank" rel="noreferrer" className="text-primary hover:underline">http://localhost:9001</a></div>
            <div><strong>Bucket:</strong> agentchain-artifacts</div>
          </CardContent>
        </Card>

        {/* Anvil EVM Node */}
        <Card className="border-border bg-card">
          <CardHeader>
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <Shield className="w-5 h-5 text-primary" />
                <CardTitle className="text-lg">Anvil Local Blockchain</CardTitle>
              </div>
              <Badge variant="success">Active</Badge>
            </div>
            <CardDescription>
              Local Ethereum/Base development testnet for smart contract escrow settlement.
            </CardDescription>
          </CardHeader>
          <CardContent className="text-sm text-muted-foreground space-y-1">
            <div><strong>RPC URL:</strong> http://localhost:8545</div>
            <div><strong>Chain ID:</strong> 31337</div>
            <div><strong>Contracts:</strong> AgentRegistry.sol, Escrow.sol</div>
          </CardContent>
        </Card>
      </div>
    </div>
    </AuthGuard>
  );
}
