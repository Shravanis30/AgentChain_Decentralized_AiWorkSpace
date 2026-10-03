"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import {
  fetchDistributions,
  Distribution,
} from "@/lib/api";

function formatAmount(amountStr: string): string {
  try {
    const raw = BigInt(amountStr);
    const unit = BigInt(1000000);
    const whole = raw / unit;
    const decimals = (raw % unit).toString().padStart(6, "0");
    return `${whole}.${decimals.slice(0, 2)} USDC`;
  } catch {
    return `${amountStr} base units`;
  }
}

function getStatusBadge(status: string) {
  switch (status) {
    case "CONFIRMED":
      return (
        <span className="px-2.5 py-1 text-xs font-semibold rounded-full bg-emerald-500/15 text-emerald-700 dark:text-emerald-400 border border-emerald-500/25">
          CONFIRMED (Canonical On-Chain)
        </span>
      );
    case "SUBMITTED":
      return (
        <span className="px-2.5 py-1 text-xs font-semibold rounded-full bg-blue-500/15 text-blue-700 dark:text-blue-400 border border-blue-500/25">
          SUBMITTED (Mined, Awaiting 32 Confs)
        </span>
      );
    case "AUTHORIZED":
      return (
        <span className="px-2.5 py-1 text-xs font-semibold rounded-full bg-indigo-500/15 text-indigo-700 dark:text-indigo-400 border border-indigo-500/25">
          AUTHORIZED (Ready for Settlement)
        </span>
      );
    case "PENDING":
      return (
        <span className="px-2.5 py-1 text-xs font-semibold rounded-full bg-amber-500/15 text-amber-700 dark:text-amber-400 border border-amber-500/25">
          PENDING
        </span>
      );
    case "REORGED":
      return (
        <span className="px-2.5 py-1 text-xs font-semibold rounded-full bg-orange-500/15 text-orange-700 dark:text-orange-400 border border-orange-500/25">
          REORGED (Chain Reorg Invalidation)
        </span>
      );
    case "FAILED":
      return (
        <span className="px-2.5 py-1 text-xs font-semibold rounded-full bg-red-500/15 text-red-700 dark:text-red-400 border border-red-500/25">
          FAILED
        </span>
      );
    default:
      return (
        <span className="px-2.5 py-1 text-xs font-semibold rounded-full bg-muted text-muted-foreground">
          {status}
        </span>
      );
  }
}

export default function DistributionsPage() {
  const [distributions, setDistributions] = useState<Distribution[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [statusFilter, setStatusFilter] = useState<string>("");
  const [chainFilter, setChainFilter] = useState<string>("");

  useEffect(() => {
    async function loadData() {
      setLoading(true);
      setError(null);
      try {
        const cId = chainFilter ? parseInt(chainFilter, 10) : undefined;
        const res = await fetchDistributions({
          chain_id: cId,
          status: statusFilter || undefined,
        });
        setDistributions(res.items || []);
      } catch (err: any) {
        setError(err.message || "Failed to load distributions");
      } finally {
        setLoading(false);
      }
    }
    loadData();
  }, [statusFilter, chainFilter]);

  return (
    <main className="max-w-7xl mx-auto px-4 py-8">
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-4 border-b border-border pb-6 mb-8">
        <div>
          <h1 className="text-3xl font-extrabold tracking-tight">85/10/5 Revenue Distributions</h1>
          <p className="text-muted-foreground mt-1">
            Deterministic on-chain split: 85% Developer, 10% Stakers, 5% (+ remainder) DAO Treasury.
          </p>
        </div>
        <div className="flex items-center gap-3">
          <select
            aria-label="Filter by status"
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value)}
            className="px-3 py-2 bg-card border border-border rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-primary"
          >
            <option value="">All Statuses</option>
            <option value="PENDING">Pending</option>
            <option value="AUTHORIZED">Authorized</option>
            <option value="SUBMITTED">Submitted</option>
            <option value="CONFIRMED">Confirmed</option>
            <option value="REORGED">Reorged</option>
            <option value="FAILED">Failed</option>
          </select>

          <select
            aria-label="Filter by network"
            value={chainFilter}
            onChange={(e) => setChainFilter(e.target.value)}
            className="px-3 py-2 bg-card border border-border rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-primary"
          >
            <option value="">All Networks</option>
            <option value="31337">Anvil (31337)</option>
            <option value="84532">Base Sepolia (84532)</option>
            <option value="8453">Base Mainnet (8453)</option>
          </select>
        </div>
      </div>

      {/* Observational Notice */}
      <div className="p-4 mb-6 rounded-lg bg-blue-500/10 border border-blue-500/20 text-blue-300 text-sm flex items-center justify-between">
        <div>
          <span className="font-semibold">Observational View Only:</span> Economic distribution percentages (85/10/5), recipients, and payment token are enforced strictly by smart contract rules and cannot be manually modified.
        </div>
        <div className="text-xs px-2.5 py-1 bg-blue-500/20 rounded font-mono">
          Model: Option B (RevenueDistributor.sol)
        </div>
      </div>

      {error && (
        <div className="p-4 mb-6 rounded-lg bg-red-500/10 border border-red-500/20 text-red-400 text-sm">
          {error}
        </div>
      )}

      {loading ? (
        <div className="py-16 text-center text-muted-foreground">Loading distribution records...</div>
      ) : distributions.length === 0 ? (
        <div className="py-16 text-center border border-dashed border-border rounded-xl">
          <p className="text-muted-foreground text-sm">No revenue distributions found matching criteria.</p>
        </div>
      ) : (
        <div className="grid gap-6">
          {distributions.map((d) => (
            <div
              key={d.id}
              className="p-6 bg-card border border-border rounded-xl shadow-sm space-y-4 hover:border-primary/50 transition-colors"
            >
              <div className="flex flex-col md:flex-row md:items-center justify-between gap-2 border-b border-border/50 pb-3">
                <div className="flex items-center gap-3">
                  <span className="font-mono text-sm font-bold text-foreground">
                    Dist #{d.id.slice(0, 8)}
                  </span>
                  {getStatusBadge(d.status)}
                  <span className="text-xs font-mono px-2 py-0.5 rounded bg-muted text-muted-foreground">
                    Chain: {d.chain_id}
                  </span>
                  <span className="text-xs font-mono px-2 py-0.5 rounded bg-muted text-muted-foreground">
                    v{d.distribution_version}
                  </span>
                </div>
                <div className="text-right">
                  <span className="text-lg font-bold text-emerald-400">
                    Gross: {formatAmount(d.gross_amount)}
                  </span>
                </div>
              </div>

              {/* 85/10/5 Breakdown Cards */}
              <div className="grid grid-cols-1 md:grid-cols-3 gap-4 pt-1">
                <div className="p-3 bg-muted/30 border border-border/50 rounded-lg">
                  <div className="flex items-center justify-between mb-1">
                    <span className="text-xs font-medium text-emerald-400">Developer (85%)</span>
                    <span className="font-bold text-sm">{formatAmount(d.developer_amount)}</span>
                  </div>
                  <div className="text-xs font-mono text-muted-foreground truncate" title={d.developer_recipient}>
                    {d.developer_recipient}
                  </div>
                </div>

                <div className="p-3 bg-muted/30 border border-border/50 rounded-lg">
                  <div className="flex items-center justify-between mb-1">
                    <span className="text-xs font-medium text-cyan-400">Staking Pool (10%)</span>
                    <span className="font-bold text-sm">{formatAmount(d.staker_amount)}</span>
                  </div>
                  <div className="text-xs font-mono text-muted-foreground truncate" title={d.staker_recipient}>
                    {d.staker_recipient}
                  </div>
                </div>

                <div className="p-3 bg-muted/30 border border-border/50 rounded-lg">
                  <div className="flex items-center justify-between mb-1">
                    <span className="text-xs font-medium text-purple-400">DAO Treasury (5% + Remainder)</span>
                    <span className="font-bold text-sm">{formatAmount(d.dao_amount)}</span>
                  </div>
                  <div className="text-xs font-mono text-muted-foreground truncate" title={d.dao_recipient}>
                    {d.dao_recipient}
                  </div>
                </div>
              </div>

              {/* Blockchain Confirmation Metadata */}
              <div className="flex flex-wrap items-center justify-between gap-4 text-xs text-muted-foreground border-t border-border/40 pt-3">
                <div className="flex items-center gap-4">
                  <span>
                    Escrow ID: <span className="font-mono text-foreground">{d.escrow_id.slice(0, 10)}...</span>
                  </span>
                  <span>
                    Settlement: <span className="font-mono text-foreground">{d.settlement_id.slice(0, 8)}...</span>
                  </span>
                  {d.distribution_tx_hash && (
                    <span>
                      Tx:{" "}
                      <span className="font-mono text-foreground">
                        {d.distribution_tx_hash.slice(0, 12)}...
                      </span>
                    </span>
                  )}
                </div>
                <div className="flex items-center gap-4">
                  <span>
                    Block: <span className="font-mono text-foreground">{d.block_number ?? "Pending"}</span>
                  </span>
                  <span>
                    Confirmations:{" "}
                    <span className="font-mono text-foreground font-semibold">
                      {d.confirmations} / 32
                    </span>
                  </span>
                </div>
              </div>
            </div>
          ))}
        </div>
      )}
    </main>
  );
}
