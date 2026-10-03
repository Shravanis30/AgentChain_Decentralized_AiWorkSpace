"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { fetchOrchestrations, type Orchestration } from "@/lib/api";

export default function OrchestrationsListPage() {
  const [orchestrations, setOrchestrations] = useState<Orchestration[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const loadData = async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetchOrchestrations(50, 0);
      setOrchestrations(res.items);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to load orchestrations");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadData();
  }, []);

  const getStatusBadge = (status: string) => {
    const s = status.toUpperCase();
    if (s === "SUCCEEDED") return <Badge className="bg-emerald-500/15 text-emerald-700 dark:text-emerald-400 border-emerald-500/25">SUCCEEDED</Badge>;
    if (s === "RUNNING") return <Badge className="bg-cyan-500/15 text-cyan-700 dark:text-cyan-400 border-cyan-500/25 animate-pulse">RUNNING</Badge>;
    if (s === "READY") return <Badge className="bg-blue-500/15 text-blue-700 dark:text-blue-400 border-blue-500/25">READY</Badge>;
    if (s === "PLANNING") return <Badge className="bg-amber-500/15 text-amber-700 dark:text-amber-400 border-amber-500/25">PLANNING</Badge>;
    if (s === "FAILED") return <Badge className="bg-rose-500/15 text-rose-700 dark:text-rose-400 border-rose-500/25">FAILED</Badge>;
    if (s === "CANCELLED") return <Badge className="bg-zinc-500/15 text-zinc-700 dark:text-zinc-400 border-zinc-500/25">CANCELLED</Badge>;
    return <Badge variant="outline">{status}</Badge>;
  };

  const calculateDuration = (started: string | null, completed: string | null, failed: string | null) => {
    if (!started) return "-";
    const startTime = new Date(started).getTime();
    const endTime = completed ? new Date(completed).getTime() : failed ? new Date(failed).getTime() : Date.now();
    const diffSec = Math.max(0, Math.round((endTime - startTime) / 1000));
    if (diffSec < 60) return `${diffSec}s`;
    const mins = Math.floor(diffSec / 60);
    const secs = diffSec % 60;
    return `${mins}m ${secs}s`;
  };

  const totalCount = orchestrations.length;
  const runningCount = orchestrations.filter((o) => o.status === "RUNNING").length;
  const succeededCount = orchestrations.filter((o) => o.status === "SUCCEEDED").length;
  const failedCount = orchestrations.filter((o) => o.status === "FAILED").length;

  return (
    <div className="space-y-8">
      {/* Top Banner */}
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-4 border-b border-border/40 pb-6">
        <div>
          <div className="flex items-center gap-3">
            <h1 className="text-3xl font-extrabold tracking-tight bg-gradient-to-r from-blue-400 via-indigo-300 to-purple-400 bg-clip-text text-transparent">
              Multi-Agent Orchestrations
            </h1>
            <Badge variant="outline" className="text-xs font-mono uppercase bg-indigo-500/10 text-indigo-400 border-indigo-500/20">
              LangGraph DAG
            </Badge>
          </div>
          <p className="text-sm text-muted-foreground mt-1">
            Submit complex goals decomposed into dependency-aware DAGs executed across isolated sandboxed agents.
          </p>
        </div>

        <div className="flex items-center gap-3">
          <Button variant="outline" size="sm" onClick={loadData} disabled={loading}>
            {loading ? "Refreshing..." : "Refresh"}
          </Button>
          <Link href="/orchestrations/new">
            <Button size="sm" className="bg-gradient-to-r from-blue-600 to-indigo-600 hover:from-blue-500 hover:to-indigo-500 text-white shadow-lg shadow-indigo-500/20">
              + New Orchestration
            </Button>
          </Link>
        </div>
      </div>

      {/* Metric Cards */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <Card className="bg-card/50 backdrop-blur border-border/60">
          <CardHeader className="p-4 pb-2">
            <CardDescription className="text-xs uppercase tracking-wider">Total Orchestrations</CardDescription>
            <CardTitle className="text-2xl font-mono">{totalCount}</CardTitle>
          </CardHeader>
        </Card>
        <Card className="bg-card/50 backdrop-blur border-border/60">
          <CardHeader className="p-4 pb-2">
            <CardDescription className="text-xs uppercase tracking-wider text-cyan-400">Active / Running</CardDescription>
            <CardTitle className="text-2xl font-mono text-cyan-400">{runningCount}</CardTitle>
          </CardHeader>
        </Card>
        <Card className="bg-card/50 backdrop-blur border-border/60">
          <CardHeader className="p-4 pb-2">
            <CardDescription className="text-xs uppercase tracking-wider text-emerald-400">Succeeded</CardDescription>
            <CardTitle className="text-2xl font-mono text-emerald-400">{succeededCount}</CardTitle>
          </CardHeader>
        </Card>
        <Card className="bg-card/50 backdrop-blur border-border/60">
          <CardHeader className="p-4 pb-2">
            <CardDescription className="text-xs uppercase tracking-wider text-rose-400">Failed</CardDescription>
            <CardTitle className="text-2xl font-mono text-rose-400">{failedCount}</CardTitle>
          </CardHeader>
        </Card>
      </div>

      {/* Main Content List */}
      {error && (
        <div className="p-4 bg-rose-500/10 border border-rose-500/20 rounded-lg text-rose-400 text-sm">
          {error}
        </div>
      )}

      {loading && orchestrations.length === 0 ? (
        <div className="text-center py-16 text-muted-foreground text-sm animate-pulse">
          Loading orchestrations...
        </div>
      ) : orchestrations.length === 0 ? (
        <Card className="border-dashed border-border/60 bg-card/20">
          <CardContent className="flex flex-col items-center justify-center py-16 text-center space-y-4">
            <div className="w-12 h-12 rounded-full bg-indigo-500/10 border border-indigo-500/20 flex items-center justify-center text-indigo-400 font-mono text-lg">
              DAG
            </div>
            <div className="space-y-1">
              <h3 className="font-semibold text-lg">No orchestrations submitted yet</h3>
              <p className="text-sm text-muted-foreground max-w-md">
                Launch a multi-agent orchestration to coordinate multiple specialized agents working on research, analysis, and synthesis.
              </p>
            </div>
            <Link href="/orchestrations/new">
              <Button className="bg-indigo-600 hover:bg-indigo-500 text-white">
                Launch Reference Orchestration
              </Button>
            </Link>
          </CardContent>
        </Card>
      ) : (
        <div className="space-y-4">
          {orchestrations.map((orch) => {
            const taskSuccess = orch.tasks?.filter((t) => t.status === "SUCCEEDED").length || 0;
            const taskTotal = orch.tasks?.length || 0;
            return (
              <Card key={orch.id} className="bg-card/40 backdrop-blur border-border/50 hover:border-indigo-500/40 transition-colors">
                <CardContent className="p-5 flex flex-col md:flex-row md:items-center justify-between gap-4">
                  <div className="space-y-2 flex-1 min-w-0">
                    <div className="flex items-center gap-3">
                      {getStatusBadge(orch.status)}
                      <span className="font-mono text-xs text-muted-foreground">
                        {orch.id.slice(0, 8)}...{orch.id.slice(-4)}
                      </span>
                      <span className="text-xs text-muted-foreground font-mono">
                        v{orch.graph_version}
                      </span>
                    </div>

                    <h3 className="font-semibold text-base text-foreground truncate" title={orch.goal}>
                      {orch.goal}
                    </h3>

                    <div className="flex flex-wrap items-center gap-x-6 gap-y-1 text-xs text-muted-foreground">
                      <div>
                        Tasks: <span className="text-foreground font-medium">{taskSuccess}/{taskTotal} completed</span>
                      </div>
                      <div>
                        Duration: <span className="text-foreground font-mono">{calculateDuration(orch.started_at, orch.completed_at, orch.failed_at)}</span>
                      </div>
                      <div>
                        Artifacts: <span className="text-foreground font-medium">{orch.artifacts?.length || 0}</span>
                      </div>
                      <div>
                        Created: <span className="text-foreground">{new Date(orch.created_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })}</span>
                      </div>
                    </div>
                  </div>

                  <div className="flex items-center gap-3 shrink-0">
                    <Link href={`/orchestrations/${orch.id}`}>
                      <Button variant="outline" size="sm" className="font-mono text-xs hover:bg-indigo-500/10 hover:text-indigo-400 hover:border-indigo-500/30">
                        View DAG & Stream →
                      </Button>
                    </Link>
                  </div>
                </CardContent>
              </Card>
            );
          })}
        </div>
      )}
    </div>
  );
}
