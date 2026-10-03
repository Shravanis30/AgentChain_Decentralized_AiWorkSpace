"use client";

import { useEffect, useState, use, useRef, useCallback } from "react";
import Link from "next/link";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import {
  fetchOrchestration,
  cancelOrchestration,
  fetchOrchestrationSSETicket,
  getOrchestrationEventsUrl,
  type Orchestration,
  type OrchestrationTask,
  type OrchestrationArtifact,
} from "@/lib/api";

interface PageProps {
  params: Promise<{ id: string }>;
}

export default function OrchestrationDetailPage({ params }: PageProps) {
  const resolvedParams = use(params);
  const orchestrationId = resolvedParams.id;

  const [orchestration, setOrchestration] = useState<Orchestration | null>(null);
  const [loading, setLoading] = useState(true);
  const [cancelling, setCancelling] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [liveConnected, setLiveConnected] = useState(false);
  const [selectedTaskKey, setSelectedTaskKey] = useState<string | null>(null);
  const [eventLogs, setEventLogs] = useState<Array<{ time: string; event: string; detail: string }>>([]);

  const eventSourceRef = useRef<EventSource | null>(null);

  const loadData = useCallback(async () => {
    try {
      const data = await fetchOrchestration(orchestrationId);
      setOrchestration(data);
      if (!selectedTaskKey && data.tasks && data.tasks.length > 0) {
        setSelectedTaskKey(data.tasks[0].task_key);
      }
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to load orchestration");
    } finally {
      setLoading(false);
    }
  }, [orchestrationId, selectedTaskKey]);

  const handleCancel = async () => {
    if (!orchestration) return;
    const confirmCancel = confirm("Are you sure you want to cancel this entire multi-agent orchestration?");
    if (!confirmCancel) return;

    setCancelling(true);
    try {
      const updated = await cancelOrchestration(orchestrationId, "User cancelled from web dashboard");
      setOrchestration(updated);
    } catch (err: unknown) {
      alert(err instanceof Error ? err.message : "Failed to cancel orchestration");
    } finally {
      setCancelling(false);
    }
  };

  // Setup SSE connection using hardened ticket
  useEffect(() => {
    loadData();

    let isMounted = true;

    async function initSSE() {
      try {
        let streamUrl = getOrchestrationEventsUrl(orchestrationId);
        try {
          const ticket = await fetchOrchestrationSSETicket(orchestrationId);
          if (ticket?.token) {
            streamUrl = getOrchestrationEventsUrl(orchestrationId, ticket.token);
          }
        } catch (ticketErr) {
          console.warn("Could not fetch dedicated SSE ticket, trying cookie auth:", ticketErr);
        }

        if (!isMounted) return;

        const es = new EventSource(streamUrl, { withCredentials: true });
        eventSourceRef.current = es;

        es.onopen = () => {
          if (isMounted) setLiveConnected(true);
        };

        es.addEventListener("orchestration_event", (event: MessageEvent) => {
          try {
            const parsed = JSON.parse(event.data);
            const eventType = parsed.event_type || "EVENT";
            const payload = parsed.payload || {};

            if (isMounted) {
              setEventLogs((prev) => [
                {
                  time: new Date().toLocaleTimeString(),
                  event: eventType,
                  detail: payload.task_key ? `Task: ${payload.task_key} (${payload.status || ""})` : payload.status || JSON.stringify(payload).slice(0, 80),
                },
                ...prev.slice(0, 49),
              ]);

              // Reload fresh data from database when significant events occur
              loadData();
            }

            const terminalEvents = ["ORCHESTRATION_SUCCEEDED", "ORCHESTRATION_FAILED", "ORCHESTRATION_CANCELLED"];
            if (terminalEvents.includes(eventType)) {
              es.close();
              if (isMounted) setLiveConnected(false);
            }
          } catch (e) {
            console.error("Error processing SSE message:", e);
          }
        });

        es.onerror = () => {
          if (isMounted) setLiveConnected(false);
        };
      } catch (e) {
        console.error("SSE initialization failed:", e);
      }
    }

    initSSE();

    return () => {
      isMounted = false;
      if (eventSourceRef.current) {
        eventSourceRef.current.close();
      }
    };
  }, [orchestrationId, loadData]);

  const getStatusBadge = (status: string) => {
    const s = status.toUpperCase();
    if (s === "SUCCEEDED") return <Badge className="bg-emerald-500/15 text-emerald-700 dark:text-emerald-400 border-emerald-500/25">SUCCEEDED</Badge>;
    if (s === "RUNNING") return <Badge className="bg-cyan-500/15 text-cyan-700 dark:text-cyan-400 border-cyan-500/25 animate-pulse">RUNNING</Badge>;
    if (s === "READY") return <Badge className="bg-blue-500/15 text-blue-700 dark:text-blue-400 border-blue-500/25">READY</Badge>;
    if (s === "PENDING") return <Badge className="bg-zinc-500/15 text-zinc-700 dark:text-zinc-400 border-zinc-500/25">PENDING</Badge>;
    if (s === "PLANNING") return <Badge className="bg-amber-500/15 text-amber-700 dark:text-amber-400 border-amber-500/25">PLANNING</Badge>;
    if (s === "FAILED") return <Badge className="bg-rose-500/15 text-rose-700 dark:text-rose-400 border-rose-500/25">FAILED</Badge>;
    if (s === "CANCELLED") return <Badge className="bg-zinc-500/15 text-zinc-700 dark:text-zinc-400 border-zinc-500/25">CANCELLED</Badge>;
    return <Badge variant="outline">{status}</Badge>;
  };

  const getTaskStatusColor = (status: string) => {
    const s = status.toUpperCase();
    if (s === "SUCCEEDED") return "border-emerald-500/40 bg-emerald-500/10 text-emerald-700 dark:text-emerald-400";
    if (s === "RUNNING") return "border-cyan-500/50 bg-cyan-500/10 text-cyan-700 dark:text-cyan-400 ring-1 ring-cyan-500/50";
    if (s === "READY") return "border-blue-500/40 bg-blue-500/10 text-blue-700 dark:text-blue-400";
    if (s === "FAILED") return "border-rose-500/50 bg-rose-500/10 text-rose-700 dark:text-rose-400";
    if (s === "CANCELLED") return "border-zinc-500/30 bg-zinc-500/10 text-zinc-700 dark:text-zinc-500";
    return "border-border/60 bg-card text-muted-foreground";
  };

  if (loading && !orchestration) {
    return (
      <div className="text-center py-20 text-muted-foreground text-sm animate-pulse">
        Loading multi-agent orchestration...
      </div>
    );
  }

  if (error || !orchestration) {
    return (
      <div className="space-y-4">
        <div className="p-4 bg-rose-500/10 border border-rose-500/20 rounded-lg text-rose-400 text-sm">
          {error || "Orchestration not found"}
        </div>
        <Link href="/orchestrations">
          <Button variant="outline">← Back to Orchestrations</Button>
        </Link>
      </div>
    );
  }

  const selectedTask = orchestration.tasks.find((t) => t.task_key === selectedTaskKey) || orchestration.tasks[0];
  const canCancel = ["PLANNING", "READY", "RUNNING"].includes(orchestration.status);

  return (
    <div className="space-y-8">
      {/* Header & Controls */}
      <div className="flex flex-col md:flex-row md:items-start justify-between gap-4 border-b border-border/40 pb-6">
        <div className="space-y-2 flex-1">
          <div className="flex items-center gap-2 text-xs font-mono text-muted-foreground">
            <Link href="/orchestrations" className="hover:underline">
              Orchestrations
            </Link>
            <span>/</span>
            <span className="text-foreground">{orchestration.id}</span>
          </div>

          <div className="flex flex-wrap items-center gap-3">
            <h1 className="text-2xl font-bold tracking-tight text-foreground">
              {orchestration.goal}
            </h1>
            {getStatusBadge(orchestration.status)}
            {liveConnected ? (
              <Badge className="bg-emerald-500/10 text-emerald-400 border-emerald-500/20 flex items-center gap-1.5 text-xs">
                <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse" />
                Live SSE
              </Badge>
            ) : (
              <Badge variant="outline" className="text-xs text-muted-foreground">
                Disconnected
              </Badge>
            )}
          </div>

          <div className="flex flex-wrap items-center gap-x-6 gap-y-1 text-xs text-muted-foreground font-mono">
            <div>
              ID: <span className="text-foreground">{orchestration.id}</span>
            </div>
            <div>
              Version: <span className="text-foreground">v{orchestration.graph_version}</span>
            </div>
            <div>
              Created: <span className="text-foreground">{new Date(orchestration.created_at).toLocaleString()}</span>
            </div>
          </div>
        </div>

        <div className="flex items-center gap-3 shrink-0">
          <Button variant="outline" size="sm" onClick={() => loadData()}>
            Refresh
          </Button>
          {canCancel && (
            <Button
              variant="destructive"
              size="sm"
              onClick={handleCancel}
              disabled={cancelling}
            >
              {cancelling ? "Cancelling..." : "Cancel Orchestration"}
            </Button>
          )}
        </div>
      </div>

      {/* Failure Banner */}
      {orchestration.status === "FAILED" && (
        <div className="p-4 bg-rose-500/10 border border-rose-500/30 rounded-lg text-rose-300 text-sm space-y-1">
          <div className="font-semibold flex items-center gap-2">
            <span>Orchestration Execution Failed</span>
            {orchestration.error_code && <code className="font-mono text-xs bg-rose-950/60 px-2 py-0.5 rounded">{orchestration.error_code}</code>}
          </div>
          <p className="text-xs text-rose-300/80">{orchestration.error_message || "An unrecoverable task failure occurred."}</p>
        </div>
      )}

      {/* DAG Visualization Section */}
      <Card className="bg-card/40 backdrop-blur border-border/60">
        <CardHeader className="pb-3 border-b border-border/40">
          <div className="flex items-center justify-between">
            <div>
              <CardTitle className="text-base font-semibold">DAG Execution Pipeline</CardTitle>
              <CardDescription className="text-xs">
                Interactive Directed Acyclic Graph. Click a task to view execution details, input/output hashes, and isolated logs.
              </CardDescription>
            </div>
            <span className="font-mono text-xs text-muted-foreground">
              {orchestration.tasks.filter((t) => t.status === "SUCCEEDED").length} of {orchestration.tasks.length} Completed
            </span>
          </div>
        </CardHeader>
        <CardContent className="p-6">
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            {orchestration.tasks.map((task, idx) => {
              const isSelected = selectedTask?.task_key === task.task_key;
              return (
                <div
                  key={task.id || task.task_key}
                  onClick={() => setSelectedTaskKey(task.task_key)}
                  className={`cursor-pointer rounded-lg p-4 border transition-all relative ${getTaskStatusColor(
                    task.status
                  )} ${isSelected ? "ring-2 ring-indigo-500 shadow-lg shadow-indigo-500/10" : ""}`}
                >
                  <div className="flex items-center justify-between gap-2 mb-2">
                    <span className="font-mono text-xs font-bold text-foreground">
                      #{idx + 1} {task.task_key}
                    </span>
                    {getStatusBadge(task.status)}
                  </div>

                  <div className="text-xs space-y-1">
                    <div className="truncate text-muted-foreground">
                      Agent: <span className="font-mono text-foreground font-medium">{task.agent_id}:{task.agent_version}</span>
                    </div>

                    {task.dependencies && task.dependencies.length > 0 && (
                      <div className="truncate text-muted-foreground text-[11px]">
                        Depends on:{" "}
                        <span className="font-mono text-indigo-300">
                          {task.dependencies.join(", ")}
                        </span>
                      </div>
                    )}

                    <div className="flex items-center justify-between pt-2 border-t border-border/30 text-[11px] text-muted-foreground">
                      <span>Attempt {task.attempt}/{task.max_attempts}</span>
                      {task.execution_id ? (
                        <Link
                          href={`/executions/${task.execution_id}`}
                          onClick={(e) => e.stopPropagation()}
                          className="font-mono text-indigo-400 hover:underline"
                        >
                          exec:{task.execution_id.slice(0, 8)}...
                        </Link>
                      ) : (
                        <span>Not dispatched</span>
                      )}
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        </CardContent>
      </Card>

      {/* Split Details Section: Selected Task vs Artifacts / Final Result */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Selected Task Inspection */}
        {selectedTask ? (
          <Card className="bg-card/40 backdrop-blur border-border/60">
            <CardHeader className="pb-3 border-b border-border/40">
              <div className="flex items-center justify-between">
                <div>
                  <CardTitle className="text-base font-semibold">
                    Task: <span className="font-mono text-indigo-400">{selectedTask.task_key}</span>
                  </CardTitle>
                  <CardDescription className="text-xs">
                    Execution payload, deterministic output hash, and sandbox dispatch.
                  </CardDescription>
                </div>
                {getStatusBadge(selectedTask.status)}
              </div>
            </CardHeader>
            <CardContent className="p-5 space-y-4">
              <div className="grid grid-cols-2 gap-3 text-xs font-mono">
                <div className="bg-card/60 p-2.5 rounded border border-border/40">
                  <div className="text-muted-foreground text-[10px] uppercase">Agent Target</div>
                  <div className="text-foreground truncate font-medium mt-0.5">{selectedTask.agent_id}:{selectedTask.agent_version}</div>
                </div>
                <div className="bg-card/60 p-2.5 rounded border border-border/40">
                  <div className="text-muted-foreground text-[10px] uppercase">Execution Record</div>
                  <div className="text-foreground truncate mt-0.5">
                    {selectedTask.execution_id ? (
                      <Link href={`/executions/${selectedTask.execution_id}`} className="text-indigo-400 hover:underline">
                        {selectedTask.execution_id}
                      </Link>
                    ) : (
                      "Pending Worker Outbox"
                    )}
                  </div>
                </div>
                <div className="bg-card/60 p-2.5 rounded border border-border/40 col-span-2">
                  <div className="text-muted-foreground text-[10px] uppercase">Input Hash (Canonical SHA-256)</div>
                  <div className="text-foreground truncate font-mono text-[11px] mt-0.5">{selectedTask.input_hash || "-"}</div>
                </div>
                {selectedTask.output_hash && (
                  <div className="bg-card/60 p-2.5 rounded border border-border/40 col-span-2">
                    <div className="text-muted-foreground text-[10px] uppercase">Output Hash (Canonical SHA-256)</div>
                    <div className="text-emerald-400 truncate font-mono text-[11px] mt-0.5">{selectedTask.output_hash}</div>
                  </div>
                )}
              </div>

              {selectedTask.error_message && (
                <div className="p-3 bg-rose-500/10 border border-rose-500/30 rounded text-rose-300 text-xs">
                  <div className="font-semibold">Error: {selectedTask.error_code || "EXECUTION_ERROR"}</div>
                  <div className="mt-1">{selectedTask.error_message}</div>
                </div>
              )}

              {selectedTask.output_data && (
                <div className="space-y-1.5">
                  <div className="text-xs font-semibold text-foreground flex items-center justify-between">
                    <span>Task Output Payload</span>
                    <span className="text-[11px] text-muted-foreground font-mono">Validated Schema</span>
                  </div>
                  <pre className="p-3 bg-black/60 rounded border border-border/50 text-xs font-mono text-zinc-200 overflow-x-auto max-h-56">
                    {JSON.stringify(selectedTask.output_data, null, 2)}
                  </pre>
                </div>
              )}
            </CardContent>
          </Card>
        ) : (
          <div className="p-8 text-center text-muted-foreground text-sm border border-dashed rounded-lg">
            Select a task from the DAG above to view detailed execution parameters.
          </div>
        )}

        {/* Final Result / Artifacts */}
        <div className="space-y-6">
          {/* Final Orchestration Result */}
          <Card className="bg-card/40 backdrop-blur border-border/60">
            <CardHeader className="pb-3 border-b border-border/40">
              <CardTitle className="text-base font-semibold">Orchestration Synthesis Result</CardTitle>
              <CardDescription className="text-xs">
                Aggregated output computed across all DAG branches upon graph termination.
              </CardDescription>
            </CardHeader>
            <CardContent className="p-5">
              {orchestration.result ? (
                <pre className="p-3 bg-black/60 rounded border border-border/50 text-xs font-mono text-emerald-300 overflow-x-auto max-h-60">
                  {JSON.stringify(orchestration.result, null, 2)}
                </pre>
              ) : (
                <div className="py-8 text-center text-xs text-muted-foreground font-mono">
                  {orchestration.status === "RUNNING"
                    ? "Orchestration currently active. Result will synthesize once final DAG nodes succeed."
                    : "No final result generated."}
                </div>
              )}
            </CardContent>
          </Card>

          {/* Artifacts Table */}
          <Card className="bg-card/40 backdrop-blur border-border/60">
            <CardHeader className="pb-3 border-b border-border/40">
              <div className="flex items-center justify-between">
                <div>
                  <CardTitle className="text-base font-semibold">Passed Artifacts</CardTitle>
                  <CardDescription className="text-xs">
                    Cryptographically hashed intermediate and terminal data payloads.
                  </CardDescription>
                </div>
                <Badge variant="outline" className="font-mono text-xs">
                  {orchestration.artifacts?.length || 0} Artifacts
                </Badge>
              </div>
            </CardHeader>
            <CardContent className="p-5">
              {!orchestration.artifacts || orchestration.artifacts.length === 0 ? (
                <div className="py-6 text-center text-xs text-muted-foreground font-mono">
                  No artifacts registered yet.
                </div>
              ) : (
                <div className="space-y-3">
                  {orchestration.artifacts.map((art: OrchestrationArtifact) => (
                    <div key={art.id} className="p-3 bg-card/60 rounded border border-border/40 text-xs space-y-1">
                      <div className="flex items-center justify-between">
                        <span className="font-mono font-semibold text-foreground">{art.artifact_key}</span>
                        <Badge variant="outline" className="text-[10px] font-mono">{art.content_type}</Badge>
                      </div>
                      <div className="text-[11px] text-muted-foreground flex flex-wrap gap-x-4">
                        <span>Producer: <code className="font-mono text-indigo-300">{art.producer_agent_id}:{art.producer_agent_version}</code></span>
                        <span>Size: <code className="font-mono text-foreground">{art.size_bytes} B</code></span>
                      </div>
                      <div className="font-mono text-[10px] text-muted-foreground truncate">
                        SHA-256: {art.sha256}
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </CardContent>
          </Card>
        </div>
      </div>

      {/* Live SSE Event Stream Log */}
      <Card className="bg-card/40 backdrop-blur border-border/60">
        <CardHeader className="pb-3 border-b border-border/40">
          <div className="flex items-center justify-between">
            <CardTitle className="text-sm font-semibold font-mono">Live Orchestration Stream</CardTitle>
            <span className="text-xs text-muted-foreground font-mono">SSE / Redis PubSub</span>
          </div>
        </CardHeader>
        <CardContent className="p-4">
          <div className="space-y-1.5 font-mono text-xs max-h-48 overflow-y-auto bg-black/70 p-3 rounded border border-border/40">
            {eventLogs.length === 0 ? (
              <div className="text-muted-foreground text-center py-4">Listening for live orchestration events...</div>
            ) : (
              eventLogs.map((log, i) => (
                <div key={i} className="flex items-center gap-3">
                  <span className="text-muted-foreground text-[11px]">{log.time}</span>
                  <Badge variant="outline" className="text-[10px] py-0 px-1.5 text-indigo-400 border-indigo-500/30">
                    {log.event}
                  </Badge>
                  <span className="text-zinc-200 truncate">{log.detail}</span>
                </div>
              ))
            )}
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
