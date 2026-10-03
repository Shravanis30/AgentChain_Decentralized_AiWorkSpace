"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { fetchAgents, type AgentItem } from "@/lib/api";
import { useAuth } from "@/context/AuthContext";

export default function AgentsPage() {
  const { user } = useAuth();
  const [agents, setAgents] = useState<AgentItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Filters
  const [search, setSearch] = useState("");
  const [capability, setCapability] = useState("");
  const [status, setStatus] = useState("PUBLISHED");
  const [page, setPage] = useState(1);
  const [totalPages, setTotalPages] = useState(1);
  const [total, setTotal] = useState(0);

  const canCreate = user && ["DEVELOPER", "AGENT_OPERATOR", "ADMIN"].includes(user.primary_role);

  const loadAgents = async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetchAgents({
        search: search.trim() || undefined,
        capability: capability.trim() || undefined,
        status: status || undefined,
        page,
        page_size: 9,
      });
      setAgents(res.items);
      setTotalPages(res.total_pages);
      setTotal(res.total);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Failed to load agents";
      setError(msg);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadAgents();
  }, [page, status]);

  const handleSearchSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    setPage(1);
    loadAgents();
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

  return (
    <div className="max-w-7xl mx-auto px-4 py-8 space-y-8">
      {/* Header Banner */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4 border-b border-border pb-6">
        <div>
          <h1 className="text-3xl font-extrabold tracking-tight">Agent Registry & Discovery</h1>
          <p className="text-muted-foreground mt-1 text-sm">
            Discover verified autonomous agents with machine-readable manifests and deterministic execution.
          </p>
        </div>
        {canCreate && (
          <Link href="/agents/new">
            <Button className="shadow-md shadow-primary/20">Register New Agent</Button>
          </Link>
        )}
      </div>

      {/* Filter and Search Bar */}
      <form onSubmit={handleSearchSubmit} className="grid grid-cols-1 sm:grid-cols-4 gap-4 bg-card p-4 rounded-xl border border-border">
        <div className="sm:col-span-2">
          <label className="text-xs font-medium text-muted-foreground mb-1 block">Search Agents</label>
          <input
            type="text"
            placeholder="Search by agent name or description..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="w-full bg-background border border-border rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary"
          />
        </div>
        <div>
          <label className="text-xs font-medium text-muted-foreground mb-1 block">Capability</label>
          <input
            type="text"
            placeholder="e.g. text_summarization"
            value={capability}
            onChange={(e) => setCapability(e.target.value)}
            className="w-full bg-background border border-border rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary"
          />
        </div>
        <div>
          <label className="text-xs font-medium text-muted-foreground mb-1 block">Status</label>
          <select
            value={status}
            onChange={(e) => {
              setStatus(e.target.value);
              setPage(1);
            }}
            className="w-full bg-background border border-border rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary"
          >
            <option value="">All Statuses</option>
            <option value="PUBLISHED">PUBLISHED (Ready)</option>
            <option value="DRAFT">DRAFT</option>
            <option value="VALIDATING">VALIDATING</option>
            <option value="SUSPENDED">SUSPENDED</option>
            <option value="DEPRECATED">DEPRECATED</option>
          </select>
        </div>
        <div className="sm:col-span-4 flex justify-end gap-2">
          <Button type="button" variant="outline" size="sm" onClick={() => { setSearch(""); setCapability(""); setStatus("PUBLISHED"); setPage(1); }}>
            Reset
          </Button>
          <Button type="submit" size="sm">
            Search
          </Button>
        </div>
      </form>

      {/* Agents Grid */}
      {loading ? (
        <div className="py-20 text-center text-muted-foreground">Loading registry catalog...</div>
      ) : error ? (
        <div className="p-4 bg-destructive/10 border border-destructive/20 rounded-xl text-destructive text-sm text-center">
          {error}
        </div>
      ) : agents.length === 0 ? (
        <div className="py-16 text-center border border-dashed border-border rounded-xl">
          <p className="text-muted-foreground">No agents found matching your query.</p>
          {canCreate && (
            <Link href="/agents/new" className="mt-3 inline-block">
              <Button size="sm" variant="outline">Create One Now</Button>
            </Link>
          )}
        </div>
      ) : (
        <div className="space-y-6">
          <div className="text-xs text-muted-foreground">
            Showing {agents.length} of {total} registered agents
          </div>
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
            {agents.map((agent) => (
              <Card key={agent.id} className="hover:border-primary/50 transition-all flex flex-col justify-between">
                <CardHeader className="pb-3">
                  <div className="flex items-center justify-between gap-2">
                    <CardTitle className="text-lg font-bold truncate">{agent.name}</CardTitle>
                    <Badge variant={getStatusBadgeVariant(agent.status)}>{agent.status}</Badge>
                  </div>
                  <CardDescription className="text-xs font-mono text-muted-foreground">
                    slug: {agent.slug} • v{agent.current_version?.version || "1.0.0"}
                  </CardDescription>
                </CardHeader>
                <CardContent className="space-y-4 flex-1 flex flex-col justify-between">
                  <p className="text-sm text-muted-foreground line-clamp-3">
                    {agent.description || "No description provided."}
                  </p>

                  <div className="space-y-3 pt-2 border-t border-border/50">
                    <div className="flex flex-wrap gap-1.5">
                      {agent.capabilities && agent.capabilities.length > 0 ? (
                        agent.capabilities.map((cap) => (
                          <span
                            key={cap}
                            className="text-[11px] px-2 py-0.5 rounded-md bg-secondary text-secondary-foreground font-mono"
                          >
                            {cap}
                          </span>
                        ))
                      ) : (
                        <span className="text-xs text-muted-foreground">No capabilities specified</span>
                      )}
                    </div>

                    <div className="flex items-center justify-between pt-2">
                      <span className="text-xs text-muted-foreground font-mono">
                        Owner: {agent.owner_user_id.slice(0, 8)}...
                      </span>
                      <Link href={`/agents/${agent.id}`}>
                        <Button size="sm" variant="outline">
                          View Details
                        </Button>
                      </Link>
                    </div>
                  </div>
                </CardContent>
              </Card>
            ))}
          </div>

          {/* Pagination */}
          {totalPages > 1 && (
            <div className="flex items-center justify-center gap-3 pt-6">
              <Button
                variant="outline"
                size="sm"
                disabled={page <= 1}
                onClick={() => setPage((p) => Math.max(1, p - 1))}
              >
                Previous
              </Button>
              <span className="text-xs text-muted-foreground">
                Page {page} of {totalPages}
              </span>
              <Button
                variant="outline"
                size="sm"
                disabled={page >= totalPages}
                onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
              >
                Next
              </Button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
