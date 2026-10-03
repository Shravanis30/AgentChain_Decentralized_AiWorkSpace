"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import {
  fetchAgentReputationProfile,
  fetchAgentReputationEvents,
  fetchAgentReputationScore,
  fetchAgent,
  ReputationProfile,
  ReputationEvent,
  ReputationScore,
  AgentItem,
} from "@/lib/api";

export default function AgentReputationPage() {
  const params = useParams();
  const agentId = params.id as string;

  const [agent, setAgent] = useState<AgentItem | null>(null);
  const [profile, setProfile] = useState<ReputationProfile | null>(null);
  const [events, setEvents] = useState<ReputationEvent[]>([]);
  const [score, setScore] = useState<ReputationScore | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedEvent, setSelectedEvent] = useState<ReputationEvent | null>(null);

  const loadData = async () => {
    if (!agentId) return;
    setLoading(true);
    setError(null);
    try {
      const [agentRes, profileRes, eventsRes, scoreRes] = await Promise.allSettled([
        fetchAgent(agentId),
        fetchAgentReputationProfile(agentId),
        fetchAgentReputationEvents(agentId),
        fetchAgentReputationScore(agentId),
      ]);

      if (agentRes.status === "fulfilled") setAgent(agentRes.value);
      if (profileRes.status === "fulfilled") setProfile(profileRes.value);
      if (eventsRes.status === "fulfilled") {
        setEvents(eventsRes.value || []);
        if (eventsRes.value && eventsRes.value.length > 0) {
          setSelectedEvent(eventsRes.value[0]);
        }
      }
      if (scoreRes.status === "fulfilled") setScore(scoreRes.value);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Failed to load reputation records";
      setError(msg);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadData();
  }, [agentId]);

  const getOutcomeBadge = (outcome: string) => {
    switch (outcome) {
      case "VERIFIED_SUCCESS":
        return <Badge className="bg-emerald-500/15 text-emerald-700 dark:text-emerald-400 border-emerald-500/30">SUCCESS</Badge>;
      case "VERIFIED_FAILURE":
        return <Badge variant="destructive" className="bg-rose-500/15 text-rose-700 dark:text-rose-400 border-rose-500/30">FAILURE</Badge>;
      case "VERIFIED_TIMEOUT":
        return <Badge variant="secondary" className="bg-amber-500/15 text-amber-700 dark:text-amber-400 border-amber-500/30">TIMEOUT</Badge>;
      case "VERIFIED_CANCELLATION":
        return <Badge variant="outline" className="bg-muted text-muted-foreground border-border">CANCELLATION</Badge>;
      default:
        return <Badge variant="outline">{outcome}</Badge>;
    }
  };

  const getStatusBadge = (status: string) => {
    switch (status) {
      case "CONFIRMED":
        return <Badge className="bg-emerald-500/15 text-emerald-700 dark:text-emerald-400 border-emerald-500/30">32-CONFIRMED</Badge>;
      case "CONFIRMING":
        return <Badge className="bg-blue-500/15 text-blue-700 dark:text-blue-400 border-blue-500/30">CONFIRMING</Badge>;
      case "PENDING":
      case "SUBMITTED":
        return <Badge className="bg-amber-500/15 text-amber-700 dark:text-amber-400 border-amber-500/30">{status}</Badge>;
      case "REORGED":
      case "INVALIDATED":
        return <Badge className="bg-red-500/15 text-red-700 dark:text-red-400 border-red-500/30">{status}</Badge>;
      default:
        return <Badge variant="outline">{status}</Badge>;
    }
  };

  return (
    <div className="container max-w-6xl mx-auto py-8 px-4 space-y-8 text-foreground">
      {/* Top Header */}
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-4 border-b border-border pb-6">
        <div>
          <div className="flex items-center gap-3">
            <Link href={`/agents/${agentId}`} className="text-sm text-muted-foreground hover:text-foreground transition">
              &larr; Back to Agent
            </Link>
            <span className="text-muted-foreground/60">/</span>
            <span className="text-sm text-muted-foreground font-medium">Reputation Evidence</span>
          </div>
          <h1 className="text-3xl font-extrabold tracking-tight mt-2 flex items-center gap-3 text-foreground">
            <span>{agent?.name || "Agent"} Reputation</span>
            <Badge variant="outline" className="text-xs uppercase tracking-wider bg-primary/10 text-primary border-primary/20">
              Observational Only
            </Badge>
          </h1>
          <p className="text-muted-foreground text-sm mt-1">
            Grounded strictly in cryptographic execution proofs anchored to ResultNotary. No subjective star ratings.
          </p>
        </div>
        <div className="flex items-center gap-3">
          <Button variant="outline" size="sm" onClick={loadData} disabled={loading} className="border-border">
            {loading ? "Refreshing..." : "Refresh Evidence"}
          </Button>
        </div>
      </div>

      {error && (
        <div className="bg-destructive/10 border border-destructive/30 text-destructive p-4 rounded-xl text-sm">
          {error}
        </div>
      )}

      {/* Aggregate Counters Grid */}
      <div className="grid grid-cols-2 md:grid-cols-5 gap-4">
        <Card className="border-border bg-card">
          <CardHeader className="p-4 pb-2">
            <CardDescription className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
              Verified Executions
            </CardDescription>
            <CardTitle className="text-2xl font-bold text-foreground mt-1">
              {profile?.total_verified_executions ?? 0}
            </CardTitle>
          </CardHeader>
          <CardContent className="p-4 pt-0 text-xs text-muted-foreground">
            Canonical evidence records
          </CardContent>
        </Card>

        <Card className="border-border bg-card">
          <CardHeader className="p-4 pb-2">
            <CardDescription className="text-xs font-semibold uppercase tracking-wider text-emerald-600 dark:text-emerald-400">
              Verified Successes
            </CardDescription>
            <CardTitle className="text-2xl font-bold text-emerald-600 dark:text-emerald-400 mt-1">
              {profile?.verified_successes ?? 0}
            </CardTitle>
          </CardHeader>
          <CardContent className="p-4 pt-0 text-xs text-muted-foreground">
            Cryptographically notarized
          </CardContent>
        </Card>

        <Card className="border-border bg-card">
          <CardHeader className="p-4 pb-2">
            <CardDescription className="text-xs font-semibold uppercase tracking-wider text-rose-600 dark:text-rose-400">
              Verified Failures
            </CardDescription>
            <CardTitle className="text-2xl font-bold text-rose-600 dark:text-rose-400 mt-1">
              {profile?.verified_failures ?? 0}
            </CardTitle>
          </CardHeader>
          <CardContent className="p-4 pt-0 text-xs text-muted-foreground">
            Terminal execution failures
          </CardContent>
        </Card>

        <Card className="border-border bg-card">
          <CardHeader className="p-4 pb-2">
            <CardDescription className="text-xs font-semibold uppercase tracking-wider text-amber-600 dark:text-amber-400">
              Verified Timeouts
            </CardDescription>
            <CardTitle className="text-2xl font-bold text-amber-600 dark:text-amber-400 mt-1">
              {profile?.verified_timeouts ?? 0}
            </CardTitle>
          </CardHeader>
          <CardContent className="p-4 pt-0 text-xs text-muted-foreground">
            Exceeded execution deadline
          </CardContent>
        </Card>

        <Card className="border-border bg-card">
          <CardHeader className="p-4 pb-2">
            <CardDescription className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
              Cancellations
            </CardDescription>
            <CardTitle className="text-2xl font-bold text-foreground mt-1">
              {profile?.verified_cancellations ?? 0}
            </CardTitle>
          </CardHeader>
          <CardContent className="p-4 pt-0 text-xs text-muted-foreground">
            Neutral client cancellations
          </CardContent>
        </Card>
      </div>

      {/* Phase 6.5: Deterministic Reputation Score Panel */}
      {score && (
        <Card className="border-primary/20 bg-card shadow-sm">
          <CardHeader className="pb-3 border-b border-border flex flex-row items-center justify-between">
            <div>
              <CardTitle className="text-base font-semibold text-foreground flex items-center gap-2">
                Deterministic Reputation Score
                <Badge variant="outline" className="text-[10px] bg-primary/10 text-primary border-primary/30">
                  {score.policy_version}
                </Badge>
              </CardTitle>
              <CardDescription className="text-xs text-muted-foreground mt-0.5">
                Derived exclusively from canonical verified execution evidence · Not a star rating
              </CardDescription>
            </div>
            {score.calculation_reason === "INITIAL_CALCULATION" && (
              <Badge className="bg-secondary text-secondary-foreground border-border text-[10px]">Cold Start</Badge>
            )}
          </CardHeader>
          <CardContent className="p-4">
            <div className="flex flex-col md:flex-row md:items-center gap-6">
              {/* Score Gauge */}
              <div className="flex flex-col items-center gap-1 min-w-[100px]">
                <div className="relative">
                  <div
                    className="w-20 h-20 rounded-full border-4 border-border flex items-center justify-center bg-secondary/50"
                    style={{
                      background: `conic-gradient(from 180deg, ${
                        score.score_scaled >= 7000 ? "#10b981" :
                        score.score_scaled >= 4000 ? "#f59e0b" : "#f43f5e"
                      } ${score.score_scaled / 100}%, transparent ${score.score_scaled / 100}%)`
                    }}
                  >
                    <div className="w-14 h-14 rounded-full bg-card border border-border flex items-center justify-center shadow-inner">
                      <span className={`text-lg font-black ${
                        score.score_scaled >= 7000 ? "text-emerald-600 dark:text-emerald-400" :
                        score.score_scaled >= 4000 ? "text-amber-600 dark:text-amber-400" : "text-rose-600 dark:text-rose-400"
                      }`}>
                        {(score.score_scaled / 100).toFixed(0)}
                      </span>
                    </div>
                  </div>
                </div>
                <span className="text-[10px] text-muted-foreground">out of 100</span>
              </div>

              {/* Score Metrics */}
              <div className="flex-1 grid grid-cols-2 md:grid-cols-4 gap-3 text-xs">
                <div className="bg-secondary/60 rounded-lg p-3 border border-border/50">
                  <div className="text-muted-foreground mb-1">Score Scaled</div>
                  <div className="font-mono text-foreground font-bold text-sm">{score.score_scaled} / 10000</div>
                </div>
                <div className="bg-secondary/60 rounded-lg p-3 border border-border/50">
                  <div className="text-muted-foreground mb-1">Success Rate</div>
                  <div className="font-mono text-emerald-600 dark:text-emerald-400 font-bold text-sm">
                    {score.success_rate_scaled !== null
                      ? `${(score.success_rate_scaled / 100).toFixed(1)}%`
                      : "—"}
                  </div>
                </div>
                <div className="bg-secondary/60 rounded-lg p-3 border border-border/50">
                  <div className="text-muted-foreground mb-1">Experience</div>
                  <div className="font-mono text-foreground font-bold text-sm">{score.experience_count} execs</div>
                </div>
                <div className="bg-secondary/60 rounded-lg p-3 border border-border/50">
                  <div className="text-muted-foreground mb-1">Evidence Events</div>
                  <div className="font-mono text-primary font-bold text-sm">{score.evidence_event_count}</div>
                </div>
              </div>

              {/* Evidence Hash */}
              <div className="text-[11px] font-mono text-muted-foreground min-w-0 flex-shrink">
                <div className="text-muted-foreground/80 mb-0.5 text-[10px]">Evidence Set Hash</div>
                <span className="text-foreground select-all break-all">{score.evidence_set_hash}</span>
              </div>
            </div>

            {/* Policy transparency */}
            <div className="mt-4 pt-3 border-t border-border text-[11px] text-muted-foreground leading-relaxed">
              <span className="font-semibold uppercase tracking-wider text-foreground">Formula: </span>
              score = round(verified_successes / (verified_successes + verified_failures) × 10000) · TIMEOUT and CANCELLATION are neutral (excluded from ratio)
            </div>
          </CardContent>
        </Card>
      )}

      {/* Main Content Area: Evidence Log + Detailed Inspector */}
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-8">
        {/* Evidence Events List */}
        <div className="lg:col-span-7 space-y-4">
          <div className="flex items-center justify-between">
            <h2 className="text-lg font-semibold tracking-tight text-foreground">
              Canonical Evidence Ledger ({events.length})
            </h2>
            <span className="text-xs text-muted-foreground">Excludes non-canonical & reorged</span>
          </div>

          {events.length === 0 ? (
            <Card className="border-border p-8 text-center text-muted-foreground bg-card">
              No verified reputation evidence events recorded for this agent yet.
            </Card>
          ) : (
            <div className="space-y-2">
              {events.map((evt) => {
                const isSelected = selectedEvent?.id === evt.id;
                return (
                  <div
                    key={evt.id}
                    onClick={() => setSelectedEvent(evt)}
                    className={`p-4 rounded-xl border transition cursor-pointer flex flex-col md:flex-row md:items-center justify-between gap-3 ${
                      isSelected
                        ? "bg-primary/10 border-primary/50 shadow-sm"
                        : "bg-card border-border hover:border-primary/40 hover:bg-secondary/40"
                    }`}
                  >
                    <div className="space-y-1">
                      <div className="flex items-center gap-2">
                        {getOutcomeBadge(evt.outcome_type)}
                        {getStatusBadge(evt.status)}
                      </div>
                      <div className="text-xs font-mono text-muted-foreground truncate max-w-sm mt-1">
                        Exec: {evt.execution_id}
                      </div>
                      <div className="text-[11px] text-muted-foreground/75">
                        {new Date(evt.created_at).toLocaleString()}
                      </div>
                    </div>

                    <div className="text-right flex flex-col items-end">
                      <span className="text-xs font-mono text-muted-foreground">
                        Block #{evt.block_number ?? "Pending"}
                      </span>
                      {evt.transaction_hash && (
                        <span className="text-[11px] font-mono text-primary/80 truncate max-w-[120px]">
                          {evt.transaction_hash.slice(0, 10)}...
                        </span>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>

        {/* Selected Event Verification Inspector */}
        <div className="lg:col-span-5">
          <Card className="border-border bg-card sticky top-20 shadow-sm">
            <CardHeader className="pb-3 border-b border-border">
              <CardTitle className="text-base font-semibold text-foreground">
                Evidence Chain Inspector
              </CardTitle>
              <CardDescription className="text-xs text-muted-foreground">
                Auditable cryptographic proofs for selected record
              </CardDescription>
            </CardHeader>
            <CardContent className="p-4 space-y-4 text-xs">
              {selectedEvent ? (
                <>
                  <div>
                    <span className="text-muted-foreground block mb-1">Reputation Event ID</span>
                    <span className="font-mono text-foreground break-all select-all font-medium">
                      {selectedEvent.id}
                    </span>
                  </div>

                  <div>
                    <span className="text-muted-foreground block mb-1">Execution ID</span>
                    <span className="font-mono text-primary break-all select-all font-medium">
                      {selectedEvent.execution_id}
                    </span>
                  </div>

                  <div>
                    <span className="text-muted-foreground block mb-1">Agent Version ID</span>
                    <span className="font-mono text-foreground break-all select-all">
                      {selectedEvent.agent_version_id}
                    </span>
                  </div>

                  <div>
                    <span className="text-muted-foreground block mb-1">Outcome Classification</span>
                    <div className="mt-0.5">{getOutcomeBadge(selectedEvent.outcome_type)}</div>
                  </div>

                  {selectedEvent.result_hash && (
                    <div>
                      <span className="text-muted-foreground block mb-1">Canonical Result Hash (SHA-256)</span>
                      <span className="font-mono text-emerald-600 dark:text-emerald-400 break-all select-all bg-secondary/80 border border-border p-2 rounded block">
                        0x{selectedEvent.result_hash}
                      </span>
                    </div>
                  )}

                  <div>
                    <span className="text-muted-foreground block mb-1">Evidence Hash</span>
                    <span className="font-mono text-foreground break-all select-all bg-secondary/80 border border-border p-2 rounded block">
                      0x{selectedEvent.evidence_hash}
                    </span>
                  </div>

                  <div className="pt-2 border-t border-border space-y-2">
                    <div className="flex justify-between">
                      <span className="text-muted-foreground">Chain ID</span>
                      <span className="font-mono text-foreground">{selectedEvent.chain_id}</span>
                    </div>
                    <div className="flex justify-between">
                      <span className="text-muted-foreground">Registry Contract</span>
                      <span className="font-mono text-foreground">
                        {selectedEvent.contract_address.slice(0, 10)}...{selectedEvent.contract_address.slice(-6)}
                      </span>
                    </div>
                    <div className="flex justify-between">
                      <span className="text-muted-foreground">Confirmations</span>
                      <span className="font-mono text-emerald-600 dark:text-emerald-400 font-semibold">
                        {selectedEvent.confirmations} depth
                      </span>
                    </div>
                    {selectedEvent.transaction_hash && (
                      <div className="flex justify-between items-center">
                        <span className="text-muted-foreground">Transaction</span>
                        <span className="font-mono text-primary truncate max-w-[150px]">
                          {selectedEvent.transaction_hash}
                        </span>
                      </div>
                    )}
                  </div>

                  <div className="bg-primary/5 border border-primary/20 p-3 rounded-lg text-[11px] text-foreground/80 leading-relaxed">
                    Cryptographic Proof Path:
                    <br />
                    Agent &rarr; Execution &rarr; SHA-256 Result &rarr; ResultNotary &rarr; ReputationRegistry &rarr; Canonical Event
                  </div>
                </>
              ) : (
                <div className="text-muted-foreground py-6 text-center">
                  Select an evidence event from the ledger to inspect cryptographic proofs.
                </div>
              )}
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  );
}
