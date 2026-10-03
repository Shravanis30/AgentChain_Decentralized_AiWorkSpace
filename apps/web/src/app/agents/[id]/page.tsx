"use client";

import { useEffect, useState, use } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import {
  fetchAgent,
  validateAgent,
  publishAgent,
  suspendAgent,
  executeAgent,
  type AgentItem,
  type ExecutionSubmitResponse,
} from "@/lib/api";
import { useAuth } from "@/context/AuthContext";

interface PageProps {
  params: Promise<{ id: string }>;
}

export default function AgentDetailPage({ params }: PageProps) {
  const resolvedParams = use(params);
  const agentId = resolvedParams.id;
  const router = useRouter();
  const { user } = useAuth();

  const [agent, setAgent] = useState<AgentItem | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [actionLoading, setActionLoading] = useState(false);

  // Execution state
  const [inputText, setInputText] = useState(
    JSON.stringify(
      { text: "AgentChain is a decentralized AI workforce platform. Autonomous agents execute workflows with verified cryptographic hashes." },
      null,
      2
    )
  );
  const [executing, setExecuting] = useState(false);
  const [execResult, setExecResult] = useState<ExecutionSubmitResponse | null>(null);

  const loadAgentData = async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await fetchAgent(agentId);
      setAgent(data);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Failed to load agent";
      setError(msg);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadAgentData();
  }, [agentId]);

  const isOwnerOrAdmin =
    user && agent && (user.id === agent.owner_user_id || user.primary_role === "ADMIN");

  const handleValidate = async () => {
    if (!agent) return;
    setActionLoading(true);
    try {
      const updated = await validateAgent(agent.id);
      setAgent(updated);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Validation failed";
      setError(msg);
    } finally {
      setActionLoading(false);
    }
  };

  const handlePublish = async () => {
    if (!agent) return;
    setActionLoading(true);
    try {
      const updated = await publishAgent(agent.id);
      setAgent(updated);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Publishing failed";
      setError(msg);
    } finally {
      setActionLoading(false);
    }
  };

  const handleSuspend = async () => {
    if (!agent) return;
    setActionLoading(true);
    try {
      const updated = await suspendAgent(agent.id, "Administrative suspension");
      setAgent(updated);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Suspension failed";
      setError(msg);
    } finally {
      setActionLoading(false);
    }
  };

  const handleExecute = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!agent) return;
    setExecuting(true);
    setError(null);
    setExecResult(null);

    let parsed: Record<string, unknown>;
    try {
      parsed = JSON.parse(inputText);
    } catch {
      setError("Execution input must be valid JSON matching the agent's input_schema.");
      setExecuting(false);
      return;
    }

    try {
      const res = await executeAgent(agent.id, parsed);
      setExecResult(res);
      router.push(`/executions/${res.execution_id}`);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Execution failed";
      setError(msg);
    } finally {
      setExecuting(false);
    }
  };

  const getStatusBadgeVariant = (st: string) => {
    switch (st) {
      case "PUBLISHED":
        return "success";
      case "DRAFT":
        return "secondary";
      case "VALIDATING":
        return "outline";
      case "SUSPENDED":
      case "DEPRECATED":
        return "destructive";
      default:
        return "secondary";
    }
  };

  if (loading) {
    return <div className="max-w-7xl mx-auto px-4 py-20 text-center text-muted-foreground">Loading agent details...</div>;
  }

  if (!agent) {
    return (
      <div className="max-w-7xl mx-auto px-4 py-20 text-center">
        <p className="text-destructive mb-4">{error || "Agent not found"}</p>
        <Link href="/agents">
          <Button variant="outline">Back to Catalog</Button>
        </Link>
      </div>
    );
  }

  const ver = agent.current_version;

  return (
    <div className="max-w-7xl mx-auto px-4 py-8 space-y-8">
      {/* Header and Lifecycle Controls */}
      <div className="flex flex-col md:flex-row md:items-center md:justify-between gap-4 border-b border-border pb-6">
        <div>
          <div className="flex items-center gap-3">
            <h1 className="text-3xl font-extrabold tracking-tight">{agent.name}</h1>
            <Badge variant={getStatusBadgeVariant(agent.status)}>{agent.status}</Badge>
          </div>
          <p className="text-sm font-mono text-muted-foreground mt-1">
            slug: {agent.slug} • version: {ver?.version || "1.0.0"} • owner: {agent.owner_user_id.slice(0, 10)}...
          </p>
        </div>

        {/* Lifecycle Buttons */}
        <div className="flex items-center gap-2">
          {isOwnerOrAdmin && (
            <>
              {agent.status === "DRAFT" && (
                <Button size="sm" variant="outline" disabled={actionLoading} onClick={handleValidate}>
                  Validate Manifest
                </Button>
              )}
              {["DRAFT", "VALIDATING"].includes(agent.status) && (
                <Button size="sm" disabled={actionLoading} onClick={handlePublish}>
                  Publish Agent
                </Button>
              )}
              {agent.status === "PUBLISHED" && (
                <Button size="sm" variant="destructive" disabled={actionLoading} onClick={handleSuspend}>
                  Suspend Agent
                </Button>
              )}
            </>
          )}
          <Link href={`/agents/${agent.id}/reputation`}>
            <Button size="sm" variant="secondary">
              Reputation
            </Button>
          </Link>
          <Link href="/agents">
            <Button size="sm" variant="outline">
              Catalog
            </Button>
          </Link>
        </div>
      </div>

      {error && (
        <div className="p-4 bg-destructive/10 border border-destructive/20 rounded-xl text-destructive text-sm">
          {error}
        </div>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-8">
        {/* Left Column: Metadata & Manifest Details */}
        <div className="lg:col-span-2 space-y-6">
          <Card>
            <CardHeader>
              <CardTitle className="text-lg">Overview</CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              <p className="text-sm text-muted-foreground">
                {agent.description || "No description provided."}
              </p>
              <div>
                <h4 className="text-xs font-semibold text-foreground uppercase tracking-wider mb-2">Capabilities</h4>
                <div className="flex flex-wrap gap-2">
                  {agent.capabilities && agent.capabilities.length > 0 ? (
                    agent.capabilities.map((c) => (
                      <span key={c} className="text-xs px-2.5 py-1 rounded-md bg-secondary font-mono">
                        {c}
                      </span>
                    ))
                  ) : (
                    <span className="text-xs text-muted-foreground">None</span>
                  )}
                </div>
              </div>
            </CardContent>
          </Card>

          {/* Schemas */}
          <Card>
            <CardHeader>
              <CardTitle className="text-lg">Protocol Schemas</CardTitle>
              <CardDescription className="text-xs">
                Strict JSON Schema definitions for input invocation and output deliverable validation.
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              <div>
                <span className="text-xs font-semibold block mb-1">Input Schema</span>
                <pre className="p-3 bg-secondary/30 rounded-lg text-xs font-mono overflow-x-auto border border-border">
                  {JSON.stringify(ver?.input_schema || {}, null, 2)}
                </pre>
              </div>
              <div>
                <span className="text-xs font-semibold block mb-1">Output Schema</span>
                <pre className="p-3 bg-secondary/30 rounded-lg text-xs font-mono overflow-x-auto border border-border">
                  {JSON.stringify(ver?.output_schema || {}, null, 2)}
                </pre>
              </div>
            </CardContent>
          </Card>

          {/* Runtime & Pricing */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <Card>
              <CardHeader>
                <CardTitle className="text-sm font-semibold">Runtime Configuration</CardTitle>
              </CardHeader>
              <CardContent className="text-xs font-mono space-y-1 text-muted-foreground">
                <div>Timeout: {String(ver?.runtime_config?.timeout_seconds ?? 300)}s</div>
                <div>Max Retries: {String(ver?.runtime_config?.max_retries ?? 0)}</div>
                <div>Memory: {String(ver?.runtime_config?.memory_mb ?? 256)} MB</div>
              </CardContent>
            </Card>
            <Card>
              <CardHeader>
                <CardTitle className="text-sm font-semibold">Pricing Configuration</CardTitle>
              </CardHeader>
              <CardContent className="text-xs font-mono space-y-1 text-muted-foreground">
                <div>Model: {String(ver?.pricing_config?.model ?? "free")}</div>
                <div>Amount: {String(ver?.pricing_config?.amount ?? "0.00")} {String(ver?.pricing_config?.currency ?? "USDC")}</div>
              </CardContent>
            </Card>
          </div>
        </div>

        {/* Right Column: Execute Agent Interface */}
        <div className="space-y-6">
          <Card className="border-primary/40 shadow-lg">
            <CardHeader>
              <div className="flex items-center justify-between">
                <CardTitle className="text-base font-bold">Execute Agent</CardTitle>
                <Badge variant={agent.status === "PUBLISHED" ? "success" : "secondary"}>
                  {agent.status === "PUBLISHED" ? "Ready" : "Inactive"}
                </Badge>
              </div>
              <CardDescription className="text-xs">
                {agent.status === "PUBLISHED"
                  ? "Invoke this agent with test input to verify output and hashing."
                  : "Only PUBLISHED agents can be executed."}
              </CardDescription>
            </CardHeader>
            <CardContent>
              <form onSubmit={handleExecute} className="space-y-4">
                <div>
                  <label className="text-xs font-medium block mb-1">Input Payload (JSON)</label>
                  <textarea
                    rows={8}
                    value={inputText}
                    onChange={(e) => setInputText(e.target.value)}
                    disabled={agent.status !== "PUBLISHED" || executing}
                    className="w-full bg-secondary/40 font-mono text-xs p-3 rounded-lg border border-border focus:outline-none focus:ring-2 focus:ring-primary disabled:opacity-50"
                  />
                </div>

                <Button
                  type="submit"
                  disabled={agent.status !== "PUBLISHED" || executing || !user}
                  className="w-full"
                >
                  {!user
                    ? "Connect Wallet to Execute"
                    : executing
                    ? "Executing In-Process..."
                    : "Invoke Agent"}
                </Button>
              </form>

              {execResult && (
                <div className="mt-4 p-3 bg-secondary/50 rounded-lg text-xs space-y-2 border border-border">
                  <div className="font-semibold text-emerald-400">Execution Dispatched!</div>
                  <div className="font-mono text-muted-foreground truncate">
                    ID: {execResult.execution_id}
                  </div>
                  <div className="font-mono text-muted-foreground truncate">
                    Hash: {execResult.input_hash.slice(0, 16)}...
                  </div>
                  <Link href={`/executions/${execResult.execution_id}`}>
                    <Button size="sm" variant="outline" className="w-full mt-2">
                      View Execution Details
                    </Button>
                  </Link>
                </div>
              )}
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  );
}
