"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { AuthGuard } from "@/components/auth/AuthGuard";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { createAgent } from "@/lib/api";

const DEFAULT_MANIFEST = {
  protocol_version: "1.0",
  agent: {
    name: "Custom Research Agent",
    version: "1.0.0",
  },
  description: "Deterministic text summarization and extraction agent.",
  capabilities: ["text_summarization"],
  input_schema: {
    type: "object",
    required: ["text"],
    properties: {
      text: {
        type: "string",
        minLength: 1,
        maxLength: 100000,
        description: "Input text payload to summarize",
      },
    },
    additionalProperties: false,
  },
  output_schema: {
    type: "object",
    required: ["summary", "word_count"],
    properties: {
      summary: { type: "string" },
      word_count: { type: "integer", minimum: 0 },
    },
    additionalProperties: false,
  },
  tools: [],
  runtime: {
    timeout_seconds: 30,
    max_retries: 1,
    memory_mb: 256,
  },
  pricing: {
    model: "per_execution",
    amount: "0.50",
    currency: "USDC",
  },
  verification: {
    type: "deterministic",
    rules: { deterministic_output: true },
  },
};

export default function NewAgentPage() {
  const router = useRouter();
  const [name, setName] = useState("Custom Research Agent");
  const [slug, setSlug] = useState("custom-research-agent");
  const [description, setDescription] = useState("Deterministic text summarization and extraction agent.");
  const [manifestText, setManifestText] = useState(JSON.stringify(DEFAULT_MANIFEST, null, 2));

  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);

    let parsedManifest: Record<string, unknown>;
    try {
      parsedManifest = JSON.parse(manifestText);
    } catch {
      setError("Invalid JSON format in Agent Manifest. Please check syntax.");
      return;
    }

    setLoading(true);
    try {
      const created = await createAgent({
        name: name.trim(),
        slug: slug.trim(),
        description: description.trim() || undefined,
        manifest: parsedManifest,
      });
      router.push(`/agents/${created.id}`);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Failed to register agent";
      setError(msg);
      setLoading(false);
    }
  };

  return (
    <AuthGuard allowedRoles={["DEVELOPER", "AGENT_OPERATOR", "ADMIN"]}>
      <div className="max-w-4xl mx-auto px-4 py-8 space-y-6">
        <div className="flex items-center justify-between border-b border-border pb-4">
          <div>
            <h1 className="text-2xl font-bold tracking-tight">Register New Agent</h1>
            <p className="text-xs text-muted-foreground mt-0.5">
              Create an AgentChain agent manifest conforming to Protocol 1.0.
            </p>
          </div>
          <Link href="/agents">
            <Button variant="outline" size="sm">
              Back to Catalog
            </Button>
          </Link>
        </div>

        {error && (
          <div className="p-4 bg-destructive/10 border border-destructive/20 rounded-xl text-destructive text-sm">
            {error}
          </div>
        )}

        <form onSubmit={handleSubmit} className="space-y-6">
          <Card>
            <CardHeader>
              <CardTitle className="text-base">Metadata</CardTitle>
              <CardDescription className="text-xs">
                Unique identifier and descriptive details for public discovery.
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                <div>
                  <label className="text-xs font-semibold block mb-1">Agent Name *</label>
                  <input
                    type="text"
                    required
                    value={name}
                    onChange={(e) => {
                      setName(e.target.value);
                      if (slug === "" || slug.startsWith("custom-")) {
                        setSlug(
                          e.target.value
                            .toLowerCase()
                            .replace(/[^a-z0-9]+/g, "-")
                            .replace(/^-|-$/g, "")
                        );
                      }
                    }}
                    className="w-full bg-background border border-border rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary"
                  />
                </div>
                <div>
                  <label className="text-xs font-semibold block mb-1">Slug (URL-friendly, unique) *</label>
                  <input
                    type="text"
                    required
                    value={slug}
                    onChange={(e) => setSlug(e.target.value.toLowerCase().replace(/[^a-z0-9-_]/g, ""))}
                    className="w-full bg-background border border-border rounded-lg px-3 py-2 text-sm font-mono focus:outline-none focus:ring-2 focus:ring-primary"
                  />
                </div>
              </div>

              <div>
                <label className="text-xs font-semibold block mb-1">Description</label>
                <textarea
                  rows={2}
                  value={description}
                  onChange={(e) => setDescription(e.target.value)}
                  className="w-full bg-background border border-border rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary"
                />
              </div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="flex flex-row items-center justify-between">
              <div>
                <CardTitle className="text-base">Agent Manifest (JSON)</CardTitle>
                <CardDescription className="text-xs">
                  Defines protocol version, capabilities, schemas, timeouts, and pricing.
                </CardDescription>
              </div>
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={() => setManifestText(JSON.stringify(DEFAULT_MANIFEST, null, 2))}
              >
                Reset Template
              </Button>
            </CardHeader>
            <CardContent>
              <textarea
                rows={18}
                value={manifestText}
                onChange={(e) => setManifestText(e.target.value)}
                className="w-full bg-secondary/40 font-mono text-xs p-3 rounded-lg border border-border focus:outline-none focus:ring-2 focus:ring-primary"
                spellCheck={false}
              />
            </CardContent>
          </Card>

          <div className="flex justify-end gap-3">
            <Link href="/agents">
              <Button type="button" variant="outline">
                Cancel
              </Button>
            </Link>
            <Button type="submit" disabled={loading}>
              {loading ? "Registering..." : "Create Agent in DRAFT"}
            </Button>
          </div>
        </form>
      </div>
    </AuthGuard>
  );
}
