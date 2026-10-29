"use client";

import { useEffect, useState, useCallback } from "react";
import Link from "next/link";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { fetchWorkers, type WorkerInfo } from "@/lib/api";

export default function OperatorWorkersPage() {
  const [workers, setWorkers] = useState<WorkerInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const loadWorkers = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await fetchWorkers();
      setWorkers(data.workers || []);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Failed to load cluster workers";
      setError(msg);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadWorkers();
    const interval = setInterval(loadWorkers, 5000);
    return () => clearInterval(interval);
  }, [loadWorkers]);

  return (
    <div className="max-w-6xl mx-auto px-4 sm:px-6 py-6 sm:py-8 space-y-6 sm:space-y-8">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4 border-b border-border pb-6">
        <div>
          <h1 className="text-xl sm:text-2xl font-bold tracking-tight">Cluster Worker Nodes</h1>
          <p className="text-xs sm:text-sm text-muted-foreground mt-1">
            Real-time worker daemon heartbeats, sandboxes, and capacity telemetry
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button size="sm" variant="outline" onClick={loadWorkers}>
            Refresh
          </Button>
          <Link href="/agents">
            <Button size="sm" variant="outline">
              Back to Catalog
            </Button>
          </Link>
        </div>
      </div>

      {loading && workers.length === 0 && (
        <div className="text-center py-16 text-muted-foreground text-sm">
          Querying cluster worker heartbeats...
        </div>
      )}

      {error && (
        <div className="p-4 bg-destructive/10 border border-destructive/20 rounded-xl text-destructive text-xs sm:text-sm break-words">
          {error} (Requires Operator or Admin role)
        </div>
      )}

      {/* Workers Grid */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4 sm:gap-6">
        {workers.map((w) => (
          <Card key={w.worker_id} className="relative overflow-hidden">
            <CardHeader className="pb-3">
              <div className="flex items-center justify-between gap-2">
                <CardTitle className="text-xs sm:text-sm font-mono font-semibold truncate">
                  {w.worker_id}
                </CardTitle>
                {w.is_alive ? (
                  <Badge variant="success" className="gap-1.5 shrink-0 text-[10px] sm:text-xs">
                    <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse" />
                    ONLINE
                  </Badge>
                ) : (
                  <Badge variant="destructive" className="shrink-0 text-[10px] sm:text-xs">OFFLINE</Badge>
                )}
              </div>
              <CardDescription className="text-xs font-mono truncate">
                v{w.version} • {w.hostname || "unknown host"}
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-3 pt-0">
              <div className="grid grid-cols-2 gap-2 text-xs">
                <div className="p-2 bg-secondary/30 rounded border border-border">
                  <span className="text-muted-foreground block text-[10px] uppercase font-semibold">
                    Status
                  </span>
                  <span className="font-semibold">{w.status}</span>
                </div>
                <div className="p-2 bg-secondary/30 rounded border border-border">
                  <span className="text-muted-foreground block text-[10px] uppercase font-semibold">
                    Active
                  </span>
                  <span className="font-semibold">{w.active_execution_count} sandboxes</span>
                </div>
              </div>

              <div className="text-[11px] text-muted-foreground flex justify-between items-center">
                <span>Heartbeat:</span>
                <span className="font-mono">{w.heartbeat_age_seconds}s ago</span>
              </div>
            </CardContent>
          </Card>
        ))}

        {!loading && workers.length === 0 && !error && (
          <div className="col-span-full text-center py-12 text-muted-foreground bg-secondary/10 rounded-xl border border-dashed border-border text-sm p-4">
            No active worker heartbeats found in Redis cluster registry.
          </div>
        )}
      </div>
    </div>
  );
}
