"use client";

import { useEffect, useState, use, useRef } from "react";
import Link from "next/link";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import {
  fetchExecution,
  cancelExecution,
  getExecutionEventsUrl,
  type ExecutionDetailResponse,
} from "@/lib/api";

interface PageProps {
  params: Promise<{ id: string }>;
}

export default function ExecutionDetailPage({ params }: PageProps) {
  const resolvedParams = use(params);
  const executionId = resolvedParams.id;

  const [execution, setExecution] = useState<ExecutionDetailResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [cancelling, setCancelling] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [liveConnected, setLiveConnected] = useState(false);
  const eventSourceRef = useRef<EventSource | null>(null);

  const loadExecution = async () => {
    try {
      const data = await fetchExecution(executionId);
      setExecution(data);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Failed to load execution";
      setError(msg);
    } finally {
      setLoading(false);
    }
  };

  const handleCancel = async () => {
    if (!execution) return;
    setCancelling(true);
    try {
      const updated = await cancelExecution(executionId, "User requested cancellation via dashboard");
      setExecution(updated);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Failed to cancel execution";
      alert(msg);
    } finally {
      setCancelling(false);
    }
  };

  // Initial load + SSE subscription
  useEffect(() => {
    loadExecution();

    const sseUrl = getExecutionEventsUrl(executionId);
    const es = new EventSource(sseUrl, { withCredentials: true });
    eventSourceRef.current = es;

    es.onopen = () => {
      setLiveConnected(true);
    };

    es.addEventListener("execution_event", (event: MessageEvent) => {
      try {
        const parsed = JSON.parse(event.data);
        const payload = parsed.payload || {};

        setExecution((prev) => {
          if (!prev) return null;
          const next = { ...prev };
          if (payload.status) next.status = payload.status;
          if (payload.started_at) next.started_at = payload.started_at;
          if (payload.completed_at) next.completed_at = payload.completed_at;
          if (payload.output) next.output_data = payload.output;
          if (payload.output_hash) next.output_hash = payload.output_hash;
          if (payload.error_code) next.error_code = payload.error_code;
          if (payload.error_message) next.error_message = payload.error_message;
          if (payload.execution_time_ms !== undefined) {
            next.metadata_json = { ...next.metadata_json, execution_time_ms: payload.execution_time_ms };
          }
          return next;
        });

        const terminalStatuses = ["SUCCEEDED", "FAILED", "TIMED_OUT", "CANCELLED"];
        if (terminalStatuses.includes(payload.status?.toUpperCase())) {
          es.close();
          setLiveConnected(false);
        }
      } catch (e) {
        console.error("Error parsing SSE event:", e);
      }
    });

    es.onerror = () => {
      setLiveConnected(false);
      es.close();
    };

    return () => {
      es.close();
      setLiveConnected(false);
    };
  }, [executionId]);

  const getStatusBadge = (st: string) => {
    switch (st) {
      case "SUCCEEDED":
        return <Badge variant="success">SUCCEEDED</Badge>;
      case "RUNNING":
        return (
          <Badge variant="outline" className="border-blue-500 text-blue-400 animate-pulse flex items-center gap-1.5">
            <span className="w-1.5 h-1.5 rounded-full bg-blue-400 animate-ping" />
            RUNNING
          </Badge>
        );
      case "QUEUED":
        return (
          <Badge variant="secondary" className="border-amber-500/50 text-amber-400 flex items-center gap-1.5">
            <span className="w-1.5 h-1.5 rounded-full bg-amber-400" />
            QUEUED
          </Badge>
        );
      case "TIMED_OUT":
        return <Badge variant="destructive">TIMED_OUT</Badge>;
      case "CANCELLED":
        return <Badge variant="secondary" className="text-muted-foreground">CANCELLED</Badge>;
      case "FAILED":
        return <Badge variant="destructive">FAILED</Badge>;
      default:
        return <Badge variant="secondary">{st}</Badge>;
    }
  };

  if (loading) {
    return (
      <div className="max-w-5xl mx-auto px-4 py-20 text-center text-muted-foreground">
        Loading execution record...
      </div>
    );
  }

  if (error || !execution) {
    return (
      <div className="max-w-5xl mx-auto px-4 py-20 text-center space-y-4">
        <div className="p-4 bg-destructive/10 border border-destructive/20 rounded-xl text-destructive text-sm max-w-lg mx-auto">
          {error || "Execution not found or access denied."}
        </div>
        <Link href="/agents">
          <Button variant="outline">Back to Catalog</Button>
        </Link>
      </div>
    );
  }

  const isCancellable = execution.status === "QUEUED" || execution.status === "RUNNING";

  return (
    <div className="max-w-5xl mx-auto px-4 py-8 space-y-8">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4 border-b border-border pb-6">
        <div>
          <div className="flex items-center gap-3">
            <h1 className="text-2xl font-bold tracking-tight">Execution Audit</h1>
            {getStatusBadge(execution.status)}
            {liveConnected && (
              <span className="text-[11px] font-mono text-emerald-400 bg-emerald-950/40 border border-emerald-800/40 px-2 py-0.5 rounded-full flex items-center gap-1">
                <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse" />
                Live SSE
              </span>
            )}
          </div>
          <p className="text-xs font-mono text-muted-foreground mt-1">
            ID: {execution.id}
          </p>
        </div>
        <div className="flex items-center gap-2">
          {isCancellable && (
            <Button
              size="sm"
              variant="destructive"
              disabled={cancelling}
              onClick={handleCancel}
            >
              {cancelling ? "Cancelling..." : "Cancel Execution"}
            </Button>
          )}
          <Link href={`/agents/${execution.agent_id}`}>
            <Button size="sm" variant="outline">
              View Agent
            </Button>
          </Link>
          <Button size="sm" variant="outline" onClick={loadExecution}>
            Refresh
          </Button>
        </div>
      </div>

      {/* Overview Cards */}
      <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
        <Card>
          <CardHeader className="p-4 pb-2">
            <CardDescription className="text-xs">Agent</CardDescription>
            <CardTitle className="text-sm font-semibold truncate">
              {execution.agent?.name || execution.agent_id.slice(0, 8)}
            </CardTitle>
          </CardHeader>
          <CardContent className="p-4 pt-0">
            <span className="text-xs font-mono text-muted-foreground">
              v{execution.agent?.current_version?.version || "1.0.0"}
            </span>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="p-4 pb-2">
            <CardDescription className="text-xs">Queued At</CardDescription>
            <CardTitle className="text-sm font-semibold">
              {execution.queued_at || execution.created_at
                ? new Date(execution.queued_at || execution.created_at).toLocaleTimeString()
                : "-"}
            </CardTitle>
          </CardHeader>
          <CardContent className="p-4 pt-0">
            <span className="text-xs text-muted-foreground">
              {execution.queued_at || execution.created_at
                ? new Date(execution.queued_at || execution.created_at).toLocaleDateString()
                : "-"}
            </span>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="p-4 pb-2">
            <CardDescription className="text-xs">Completed At</CardDescription>
            <CardTitle className="text-sm font-semibold">
              {execution.completed_at ? new Date(execution.completed_at).toLocaleTimeString() : (
                execution.status === "RUNNING" ? "Running..." : "Queued..."
              )}
            </CardTitle>
          </CardHeader>
          <CardContent className="p-4 pt-0">
            <span className="text-xs text-muted-foreground">
              {execution.completed_at ? new Date(execution.completed_at).toLocaleDateString() : "-"}
            </span>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="p-4 pb-2">
            <CardDescription className="text-xs">Execution Duration</CardDescription>
            <CardTitle className="text-sm font-semibold">
              {String(execution.metadata_json?.execution_time_ms ?? "-")} ms
            </CardTitle>
          </CardHeader>
          <CardContent className="p-4 pt-0">
            <span className="text-xs text-muted-foreground">
              Attempt {execution.attempt_count ?? 1}
            </span>
          </CardContent>
        </Card>
      </div>

      {/* Failure / Cancellation Notice if any */}
      {execution.error_code && (
        <Card className="border-destructive/40 bg-destructive/5">
          <CardHeader className="p-4 pb-2">
            <CardTitle className="text-sm font-bold text-destructive">
              Execution Status: {execution.status} ({execution.error_code})
            </CardTitle>
          </CardHeader>
          <CardContent className="p-4 pt-0">
            <p className="text-xs text-destructive font-mono">{execution.error_message}</p>
          </CardContent>
        </Card>
      )}

      {/* Inputs & Outputs with Cryptographic Hashes */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        {/* Input */}
        <Card className="space-y-2">
          <CardHeader>
            <CardTitle className="text-base">Input Payload</CardTitle>
            <CardDescription className="text-xs">
              Canonical JSON RFC 8785 representation
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="p-2.5 bg-secondary/20 rounded border border-border">
              <span className="text-[11px] text-muted-foreground font-semibold block uppercase">
                Canonical SHA-256 Input Hash
              </span>
              <span className="text-xs font-mono text-foreground break-all">
                {execution.input_hash}
              </span>
            </div>
            <pre className="p-3 bg-secondary/30 rounded-lg text-xs font-mono overflow-x-auto border border-border max-h-80">
              {JSON.stringify(execution.input_data, null, 2)}
            </pre>
          </CardContent>
        </Card>

        {/* Output */}
        <Card className="space-y-2">
          <CardHeader>
            <CardTitle className="text-base">Output Deliverable</CardTitle>
            <CardDescription className="text-xs">
              Structured agent execution deliverable
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="p-2.5 bg-secondary/20 rounded border border-border">
              <span className="text-[11px] text-muted-foreground font-semibold block uppercase">
                Canonical SHA-256 Output Hash
              </span>
              <span className="text-xs font-mono text-foreground break-all">
                {execution.output_hash || "Awaiting execution output..."}
              </span>
            </div>
            <pre className="p-3 bg-secondary/30 rounded-lg text-xs font-mono overflow-x-auto border border-border max-h-80">
              {execution.output_data ? JSON.stringify(execution.output_data, null, 2) : "// Awaiting output..."}
            </pre>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
