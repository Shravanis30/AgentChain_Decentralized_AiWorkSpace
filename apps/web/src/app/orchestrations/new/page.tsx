"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { createOrchestration, startOrchestration, type TaskDefinitionPayload } from "@/lib/api";

const PRESET_LINEAR = {
  name: "3-Stage Sequential Pipeline (Research → Analysis → Synthesis)",
  description: "Standard end-to-end vertical slice demonstrating sequential dependency resolution and artifact passing.",
  goal: "Research AgentChain multi-agent sandboxing, analyze security boundaries, and synthesize recommendations.",
  tasks: [
    {
      task_key: "research",
      capability: "research",
      agent_id: "agent-research",
      agent_version: "1.0.0",
      input: { topic: "AgentChain Worker Sandboxing", depth: "standard" },
      depends_on: [],
      max_attempts: 2,
    },
    {
      task_key: "analysis",
      capability: "analysis",
      agent_id: "agent-analysis",
      agent_version: "1.0.0",
      input: { focus_area: "Security Boundaries & Determinism" },
      depends_on: ["research"],
      max_attempts: 2,
    },
    {
      task_key: "synthesis",
      capability: "synthesis",
      agent_id: "agent-synthesis",
      agent_version: "1.0.0",
      input: { report_format: "Executive Summary" },
      depends_on: ["analysis"],
      max_attempts: 2,
    },
  ],
};

const PRESET_PARALLEL = {
  name: "Parallel Branching Pipeline (Research + Market → Synthesis)",
  description: "Demonstrates bounded concurrent scheduling of independent tasks joining at a synthesis node.",
  goal: "Evaluate technological runtime characteristics and market positioning simultaneously, then combine into a unified report.",
  tasks: [
    {
      task_key: "tech_research",
      capability: "research",
      agent_id: "agent-research",
      agent_version: "1.0.0",
      input: { topic: "gVisor vs Docker isolated worker performance" },
      depends_on: [],
      max_attempts: 2,
    },
    {
      task_key: "risk_analysis",
      capability: "analysis",
      agent_id: "agent-analysis",
      agent_version: "1.0.0",
      input: { topic: "Execution outbox at-least-once edge cases" },
      depends_on: [],
      max_attempts: 2,
    },
    {
      task_key: "final_synthesis",
      capability: "synthesis",
      agent_id: "agent-synthesis",
      agent_version: "1.0.0",
      input: { report_type: "Comprehensive Architecture Assessment" },
      depends_on: ["tech_research", "risk_analysis"],
      max_attempts: 2,
    },
  ],
};

export default function NewOrchestrationPage() {
  const router = useRouter();

  const [mode, setMode] = useState<"preset" | "custom">("preset");
  const [selectedPreset, setSelectedPreset] = useState<"linear" | "parallel">("linear");
  const [goal, setGoal] = useState(PRESET_LINEAR.goal);
  const [tasksJson, setTasksJson] = useState(JSON.stringify(PRESET_LINEAR.tasks, null, 2));

  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const applyPreset = (presetKey: "linear" | "parallel") => {
    setSelectedPreset(presetKey);
    const preset = presetKey === "linear" ? PRESET_LINEAR : PRESET_PARALLEL;
    setGoal(preset.goal);
    setTasksJson(JSON.stringify(preset.tasks, null, 2));
    setError(null);
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setSubmitting(true);
    setError(null);

    let parsedTasks: TaskDefinitionPayload[] | undefined;
    if (tasksJson.trim()) {
      try {
        parsedTasks = JSON.parse(tasksJson);
        if (!Array.isArray(parsedTasks)) {
          throw new Error("Tasks definition must be a JSON array");
        }
      } catch (err: unknown) {
        setError(`Invalid Tasks JSON: ${err instanceof Error ? err.message : String(err)}`);
        setSubmitting(false);
        return;
      }
    }

    try {
      // 1. Create orchestration
      const orch = await createOrchestration(goal, parsedTasks);
      // 2. Start orchestration
      await startOrchestration(orch.id);
      // 3. Navigate to details view
      router.push(`/orchestrations/${orch.id}`);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to create orchestration");
      setSubmitting(false);
    }
  };

  return (
    <div className="max-w-4xl mx-auto space-y-8">
      {/* Header */}
      <div className="border-b border-border/40 pb-6">
        <div className="flex items-center gap-2 text-xs font-mono text-muted-foreground mb-2">
          <Link href="/orchestrations" className="hover:underline">
            Orchestrations
          </Link>
          <span>/</span>
          <span className="text-foreground">New</span>
        </div>
        <h1 className="text-3xl font-extrabold tracking-tight bg-gradient-to-r from-blue-400 via-indigo-300 to-purple-400 bg-clip-text text-transparent">
          Create Multi-Agent Orchestration
        </h1>
        <p className="text-sm text-muted-foreground mt-1">
          Specify a high-level goal and a DAG of tasks. The orchestrator plans dependency scheduling, dispatches child executions to sandboxed workers, and aggregates results.
        </p>
      </div>

      {error && (
        <div className="p-4 bg-rose-500/10 border border-rose-500/20 rounded-lg text-rose-400 text-sm">
          {error}
        </div>
      )}

      {/* Mode Selector */}
      <div className="flex gap-4">
        <Button
          type="button"
          variant={mode === "preset" ? "default" : "outline"}
          onClick={() => setMode("preset")}
          className={mode === "preset" ? "bg-indigo-600 text-white" : ""}
        >
          Use Workflow Presets
        </Button>
        <Button
          type="button"
          variant={mode === "custom" ? "default" : "outline"}
          onClick={() => setMode("custom")}
          className={mode === "custom" ? "bg-indigo-600 text-white" : ""}
        >
          Custom DAG Editor
        </Button>
      </div>

      <form onSubmit={handleSubmit} className="space-y-6">
        {mode === "preset" && (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <Card
              className={`cursor-pointer transition-all border-2 ${
                selectedPreset === "linear"
                  ? "border-indigo-500 bg-indigo-500/5 shadow-md shadow-indigo-500/10"
                  : "border-border/60 hover:border-border bg-card/30"
              }`}
              onClick={() => applyPreset("linear")}
            >
              <CardHeader className="p-5 pb-3">
                <div className="flex items-center justify-between">
                  <Badge variant="outline" className="font-mono text-xs text-indigo-400 border-indigo-500/30">
                    Linear 3-Stage
                  </Badge>
                  {selectedPreset === "linear" && <span className="text-xs text-indigo-400 font-bold">Selected</span>}
                </div>
                <CardTitle className="text-base mt-2">{PRESET_LINEAR.name}</CardTitle>
                <CardDescription className="text-xs">{PRESET_LINEAR.description}</CardDescription>
              </CardHeader>
              <CardContent className="p-5 pt-0">
                <div className="font-mono text-xs text-muted-foreground bg-muted/40 p-2.5 rounded border border-border/40">
                  research → analysis → synthesis
                </div>
              </CardContent>
            </Card>

            <Card
              className={`cursor-pointer transition-all border-2 ${
                selectedPreset === "parallel"
                  ? "border-indigo-500 bg-indigo-500/5 shadow-md shadow-indigo-500/10"
                  : "border-border/60 hover:border-border bg-card/30"
              }`}
              onClick={() => applyPreset("parallel")}
            >
              <CardHeader className="p-5 pb-3">
                <div className="flex items-center justify-between">
                  <Badge variant="outline" className="font-mono text-xs text-cyan-400 border-cyan-500/30">
                    Branch & Join
                  </Badge>
                  {selectedPreset === "parallel" && <span className="text-xs text-cyan-400 font-bold">Selected</span>}
                </div>
                <CardTitle className="text-base mt-2">{PRESET_PARALLEL.name}</CardTitle>
                <CardDescription className="text-xs">{PRESET_PARALLEL.description}</CardDescription>
              </CardHeader>
              <CardContent className="p-5 pt-0">
                <div className="font-mono text-xs text-muted-foreground bg-muted/40 p-2.5 rounded border border-border/40">
                  [tech_research, risk_analysis] → final_synthesis
                </div>
              </CardContent>
            </Card>
          </div>
        )}

        {/* Goal Input */}
        <div className="space-y-2">
          <label className="text-sm font-semibold tracking-wide flex items-center justify-between">
            <span>Overall User Goal</span>
            <span className="text-xs text-muted-foreground font-normal">Required</span>
          </label>
          <textarea
            value={goal}
            onChange={(e) => setGoal(e.target.value)}
            required
            rows={2}
            className="w-full rounded-md border border-border/70 bg-card/50 px-3.5 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500/40"
            placeholder="Describe the high-level objective of this multi-agent orchestration..."
          />
        </div>

        {/* Tasks DAG Definition */}
        <div className="space-y-2">
          <div className="flex items-center justify-between">
            <label className="text-sm font-semibold tracking-wide">
              DAG Task Definitions (JSON)
            </label>
            <span className="text-xs text-muted-foreground font-mono">
              Max 20 tasks • Cycle-free DAG
            </span>
          </div>
          <p className="text-xs text-muted-foreground">
            Each task requires a unique <code className="font-mono text-indigo-300">task_key</code>, target agent or capability, input payload, and explicit <code className="font-mono text-indigo-300">depends_on</code> parent keys.
          </p>
          <textarea
            value={tasksJson}
            onChange={(e) => setTasksJson(e.target.value)}
            rows={12}
            className="w-full font-mono text-xs rounded-md border border-border bg-card dark:bg-black/60 px-3.5 py-2.5 text-foreground dark:text-zinc-200 focus:outline-none focus:ring-2 focus:ring-primary shadow-sm"
            placeholder="[]"
          />
        </div>

        {/* Invariant reminders */}
        <div className="rounded-lg border border-primary/20 bg-primary/5 p-4 text-xs space-y-1.5 text-muted-foreground">
          <div className="font-semibold text-primary">Architectural Guarantees:</div>
          <ul className="list-disc list-inside space-y-1">
            <li>LangGraph orchestrates the DAG but <strong>never</strong> invokes agent code directly.</li>
            <li>Each task dispatches an <code className="font-mono text-foreground">AgentExecution</code> via the PostgreSQL Transactional Outbox and Redis Streams.</li>
            <li>Agents execute inside isolated sandboxes (Docker / LocalProcess) with bounded leases and cryptographic output hashing.</li>
          </ul>
        </div>

        {/* Action Buttons */}
        <div className="flex items-center justify-end gap-3 pt-4 border-t border-border/40">
          <Link href="/orchestrations">
            <Button type="button" variant="outline" disabled={submitting}>
              Cancel
            </Button>
          </Link>
          <Button
            type="submit"
            disabled={submitting || !goal.trim()}
            className="bg-gradient-to-r from-blue-600 to-indigo-600 hover:from-blue-500 hover:to-indigo-500 text-white font-medium shadow-md shadow-indigo-500/20"
          >
            {submitting ? "Launching Orchestration..." : "Submit & Launch DAG →"}
          </Button>
        </div>
      </form>
    </div>
  );
}
