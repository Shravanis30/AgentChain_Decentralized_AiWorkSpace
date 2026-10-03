"use client";

import { useState } from "react";
import Link from "next/link";
import {
  fetchNotarizationByExecutionId,
  verifyNotarization,
  ResultNotarization,
  NotarizationVerifyResponse,
} from "@/lib/api";

export default function NotarizationsPage() {
  const [searchExecId, setSearchExecId] = useState("");
  const [chainId, setChainId] = useState<number>(31337);
  const [loading, setLoading] = useState(false);
  const [verifying, setVerifying] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notarization, setNotarization] = useState<ResultNotarization | null>(null);
  const [verificationResult, setVerificationResult] = useState<NotarizationVerifyResponse | null>(null);

  const handleLookup = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!searchExecId.trim()) return;

    setLoading(true);
    setError(null);
    setNotarization(null);
    setVerificationResult(null);

    try {
      const data = await fetchNotarizationByExecutionId(searchExecId.trim(), chainId);
      setNotarization(data);
    } catch (err: any) {
      setError(err.message || "Failed to load notarization proof");
    } finally {
      setLoading(false);
    }
  };

  const handleVerify = async () => {
    if (!notarization) return;
    setVerifying(true);
    try {
      const result = await verifyNotarization(notarization.execution_id, {
        chain_id: notarization.chain_id,
      });
      setVerificationResult(result);
    } catch (err: any) {
      setError(err.message || "Verification request failed");
    } finally {
      setVerifying(false);
    }
  };

  const getStatusBadge = (status: string, isCanonical: boolean) => {
    if (!isCanonical || status === "REORGED") {
      return (
        <span className="px-3 py-1 text-xs font-semibold rounded-full bg-red-500/15 text-red-700 dark:text-red-400 border border-red-500/25">
          REORGED (Orphaned by Chain Reorganization)
        </span>
      );
    }
    switch (status) {
      case "CONFIRMED":
        return (
          <span className="px-3 py-1 text-xs font-semibold rounded-full bg-emerald-500/15 text-emerald-700 dark:text-emerald-400 border border-emerald-500/25">
            CONFIRMED (Canonical On-Chain)
          </span>
        );
      case "CONFIRMING":
        return (
          <span className="px-3 py-1 text-xs font-semibold rounded-full bg-blue-500/15 text-blue-700 dark:text-blue-400 border border-blue-500/25">
            CONFIRMING (Mined, Accumulating Confirmations)
          </span>
        );
      case "SUBMITTED":
        return (
          <span className="px-3 py-1 text-xs font-semibold rounded-full bg-indigo-500/15 text-indigo-700 dark:text-indigo-400 border border-indigo-500/25">
            SUBMITTED (Mined on Relayer)
          </span>
        );
      case "PENDING":
        return (
          <span className="px-3 py-1 text-xs font-semibold rounded-full bg-amber-500/15 text-amber-700 dark:text-amber-400 border border-amber-500/25">
            PENDING (Queued to Transaction Outbox)
          </span>
        );
      default:
        return (
          <span className="px-3 py-1 text-xs font-semibold rounded-full bg-muted text-muted-foreground border border-border">
            {status}
          </span>
        );
    }
  };

  const getVerificationBadge = (vStatus: string) => {
    switch (vStatus) {
      case "VERIFIED":
        return (
          <div className="p-4 rounded-xl bg-emerald-950/40 border border-emerald-500/30 text-emerald-300">
            <div className="flex items-center space-x-2 font-semibold">
              <span className="w-2.5 h-2.5 rounded-full bg-emerald-400 animate-pulse" />
              <span>CRYPTOGRAPHICALLY VERIFIED</span>
            </div>
            <p className="mt-1 text-xs text-emerald-400/80">
              Canonical result hash matches the immutable on-chain proof recorded in ResultNotary.
            </p>
          </div>
        );
      case "HASH_MISMATCH":
        return (
          <div className="p-4 rounded-xl bg-red-950/40 border border-red-500/30 text-red-300">
            <div className="flex items-center space-x-2 font-semibold">
              <span className="w-2.5 h-2.5 rounded-full bg-red-500" />
              <span>HASH MISMATCH VIOLATION</span>
            </div>
            <p className="mt-1 text-xs text-red-400/80">
              Output computation does not match the canonical result hash anchored to the blockchain.
            </p>
          </div>
        );
      case "REORGED":
        return (
          <div className="p-4 rounded-xl bg-orange-950/40 border border-orange-500/30 text-orange-300">
            <div className="flex items-center space-x-2 font-semibold">
              <span className="w-2.5 h-2.5 rounded-full bg-orange-500" />
              <span>REORGANIZATION INVALIDATED</span>
            </div>
            <p className="mt-1 text-xs text-orange-400/80">
              The notarization block was orphaned during a chain reorg. Proof is not currently canonical.
            </p>
          </div>
        );
      default:
        return (
          <div className="p-4 rounded-xl bg-amber-950/40 border border-amber-500/30 text-amber-300">
            <div className="flex items-center space-x-2 font-semibold">
              <span className="w-2.5 h-2.5 rounded-full bg-amber-400" />
              <span>{vStatus}</span>
            </div>
          </div>
        );
    }
  };

  const getChainName = (id: number) => {
    switch (id) {
      case 31337:
        return "Local Anvil (31337)";
      case 84532:
        return "Base Sepolia (84532)";
      case 8453:
        return "Base Mainnet (8453 - BLOCKED)";
      default:
        return `Chain ${id}`;
    }
  };

  return (
    <div className="max-w-6xl mx-auto px-4 py-8 space-y-8">
      {/* Header */}
      <div>
        <div className="flex items-center space-x-3">
          <h1 className="text-2xl font-bold tracking-tight text-foreground">
            Cryptographic Result Notarization Proofs
          </h1>
          <span className="px-2.5 py-0.5 rounded text-xs font-semibold bg-primary/10 text-primary border border-primary/20">
            Phase 6.3 Read-Only Observability
          </span>
        </div>
        <p className="text-sm text-muted-foreground mt-1">
          Verify immutable on-chain result hash anchoring, RFC 8785 canonicalization, and canonical confirmation depth.
        </p>
      </div>

      {/* Search Bar */}
      <form onSubmit={handleLookup} className="flex flex-col sm:flex-row gap-3">
        <div className="flex-1">
          <input
            type="text"
            placeholder="Enter Agent Execution UUID (e.g., 550e8400-e29b-41d4-a716-446655440000)"
            value={searchExecId}
            onChange={(e) => setSearchExecId(e.target.value)}
            className="w-full px-4 py-2.5 rounded-lg border border-border bg-card text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-primary/40 font-mono text-sm"
          />
        </div>
        <div className="w-full sm:w-48">
          <select
            value={chainId}
            onChange={(e) => setChainId(Number(e.target.value))}
            className="w-full px-3 py-2.5 rounded-lg border border-border bg-card text-foreground focus:outline-none focus:ring-2 focus:ring-primary/40 text-sm"
          >
            <option value={31337}>Local Anvil (31337)</option>
            <option value={84532}>Base Sepolia (84532)</option>
            <option value={8453} disabled>Base Mainnet (8453 - Blocked)</option>
          </select>
        </div>
        <button
          type="submit"
          disabled={loading || !searchExecId.trim()}
          className="px-6 py-2.5 rounded-lg bg-primary hover:bg-primary/90 text-primary-foreground font-medium text-sm transition-colors disabled:opacity-50"
        >
          {loading ? "Searching..." : "Lookup Proof"}
        </button>
      </form>

      {/* Error display */}
      {error && (
        <div className="p-4 rounded-xl bg-destructive/10 border border-destructive/20 text-destructive text-sm">
          {error}
        </div>
      )}

      {/* Proof Card */}
      {notarization && (
        <div className="border border-border rounded-2xl bg-card overflow-hidden divide-y divide-border">
          {/* Top Bar */}
          <div className="p-6 flex flex-col md:flex-row items-start md:items-center justify-between gap-4 bg-muted/20">
            <div>
              <span className="text-xs text-muted-foreground uppercase tracking-wider font-semibold">
                Execution Identity
              </span>
              <div className="font-mono text-base font-semibold text-foreground mt-0.5">
                {notarization.execution_id}
              </div>
            </div>
            <div>{getStatusBadge(notarization.status, notarization.is_canonical)}</div>
          </div>

          {/* Verification Trigger Banner */}
          <div className="p-6 bg-card flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4">
            <div>
              <h3 className="font-medium text-foreground text-sm">Cryptographic On-Chain Verification</h3>
              <p className="text-xs text-muted-foreground mt-0.5">
                Recomputes canonical digest and verifies status against ResultNotary smart contract.
              </p>
            </div>
            <button
              onClick={handleVerify}
              disabled={verifying}
              className="px-4 py-2 rounded-lg bg-secondary hover:bg-secondary/80 text-foreground text-xs font-semibold border border-border transition-colors disabled:opacity-50"
            >
              {verifying ? "Verifying..." : "Verify Proof"}
            </button>
          </div>

          {/* Verification Result Display */}
          {verificationResult && (
            <div className="p-6 bg-muted/10">
              {getVerificationBadge(verificationResult.verification_status)}
            </div>
          )}

          {/* Details Grid */}
          <div className="p-6 grid grid-cols-1 md:grid-cols-2 gap-6 text-sm">
            {/* Hash & Algorithm */}
            <div className="space-y-4">
              <div>
                <span className="text-xs text-muted-foreground">Canonical Result Hash (Immutable)</span>
                <div className="font-mono text-xs text-foreground bg-muted/50 p-2.5 rounded border border-border break-all mt-1">
                  0x{notarization.result_hash}
                </div>
              </div>

              <div>
                <span className="text-xs text-muted-foreground">Hash Algorithm / Canonicalization</span>
                <div className="font-medium text-foreground mt-0.5">
                  {notarization.hash_algorithm} &bull; {notarization.canonicalization_version}
                </div>
              </div>

              {notarization.artifact_reference && (
                <div>
                  <span className="text-xs text-muted-foreground">Artifact Reference</span>
                  <div className="font-mono text-xs text-primary break-all mt-0.5">
                    {notarization.artifact_reference}
                  </div>
                </div>
              )}

              {notarization.artifact_commitment && (
                <div>
                  <span className="text-xs text-muted-foreground">Artifact Commitment</span>
                  <div className="font-mono text-xs text-muted-foreground break-all mt-0.5">
                    0x{notarization.artifact_commitment}
                  </div>
                </div>
              )}
            </div>

            {/* Blockchain Evidence */}
            <div className="space-y-4">
              <div>
                <span className="text-xs text-muted-foreground">Target Network & Contract</span>
                <div className="font-medium text-foreground mt-0.5">
                  {getChainName(notarization.chain_id)}
                </div>
                <div className="font-mono text-xs text-muted-foreground break-all">
                  {notarization.contract_address}
                </div>
              </div>

              <div>
                <span className="text-xs text-muted-foreground">Transaction Hash</span>
                <div className="font-mono text-xs text-foreground break-all mt-0.5">
                  {notarization.transaction_hash || "Pending relayer submission"}
                </div>
              </div>

              <div className="grid grid-cols-2 gap-4">
                <div>
                  <span className="text-xs text-muted-foreground">Block Number</span>
                  <div className="font-medium text-foreground mt-0.5">
                    {notarization.block_number ?? "Pending mining"}
                  </div>
                </div>
                <div>
                  <span className="text-xs text-muted-foreground">Confirmation Depth</span>
                  <div className="font-medium text-foreground mt-0.5">
                    {notarization.confirmations} blocks
                  </div>
                </div>
              </div>

              <div>
                <span className="text-xs text-muted-foreground">Canonical Status</span>
                <div className="font-medium mt-0.5">
                  {notarization.is_canonical ? (
                    <span className="text-emerald-400">Canonical Chain Member</span>
                  ) : (
                    <span className="text-red-400">Non-Canonical / Orphaned</span>
                  )}
                </div>
              </div>
            </div>
          </div>

          {/* Canonical Payload Inspector */}
          {notarization.payload_json && Object.keys(notarization.payload_json).length > 0 && (
            <div className="p-6 bg-muted/5">
              <span className="text-xs text-muted-foreground font-semibold uppercase tracking-wider">
                Canonical Payload Envelope (RFC 8785 Source)
              </span>
              <pre className="mt-2 p-3 rounded-lg bg-card border border-border text-xs font-mono text-muted-foreground overflow-x-auto">
                {JSON.stringify(notarization.payload_json, null, 2)}
              </pre>
            </div>
          )}
        </div>
      )}

      {/* Empty / Initial State */}
      {!notarization && !loading && !error && (
        <div className="border border-border/60 border-dashed rounded-2xl p-12 text-center text-muted-foreground">
          <div className="w-12 h-12 rounded-full bg-muted mx-auto flex items-center justify-center text-xl mb-3">
            🔒
          </div>
          <h3 className="font-semibold text-foreground text-sm">No Execution Notarization Selected</h3>
          <p className="text-xs max-w-md mx-auto mt-1">
            Enter an Agent Execution UUID above to inspect its cryptographic result hash, on-chain ResultNotary proof, and confirmation depth.
          </p>
        </div>
      )}
    </div>
  );
}
