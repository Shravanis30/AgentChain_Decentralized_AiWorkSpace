"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import {
  fetchSettlements,
  Settlement,
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
          CONFIRMED
        </span>
      );
    case "SUBMITTED":
      return (
        <span className="px-2.5 py-1 text-xs font-semibold rounded-full bg-blue-500/15 text-blue-700 dark:text-blue-400 border border-blue-500/25">
          SUBMITTED
        </span>
      );
    case "AUTHORIZED":
      return (
        <span className="px-2.5 py-1 text-xs font-semibold rounded-full bg-indigo-500/15 text-indigo-700 dark:text-indigo-400 border border-indigo-500/25">
          AUTHORIZED
        </span>
      );
    case "PENDING_AUTHORIZATION":
      return (
        <span className="px-2.5 py-1 text-xs font-semibold rounded-full bg-amber-500/15 text-amber-700 dark:text-amber-400 border border-amber-500/25">
          PENDING
        </span>
      );
    case "BLOCKED":
      return (
        <span className="px-2.5 py-1 text-xs font-semibold rounded-full bg-rose-500/15 text-rose-700 dark:text-rose-400 border border-rose-500/25">
          BLOCKED
        </span>
      );
    case "FAILED":
      return (
        <span className="px-2.5 py-1 text-xs font-semibold rounded-full bg-red-500/15 text-red-700 dark:text-red-400 border border-red-500/25">
          FAILED
        </span>
      );
    case "CANCELLED":
      return (
        <span className="px-2.5 py-1 text-xs font-semibold rounded-full bg-zinc-500/15 text-zinc-700 dark:text-zinc-400 border border-zinc-500/25">
          CANCELLED
        </span>
      );
    default:
      return (
        <span className="px-2.5 py-1 text-xs font-semibold rounded-full bg-secondary text-muted-foreground border border-border">
          {status}
        </span>
      );
  }
}

export default function SettlementsPage() {
  const [settlements, setSettlements] = useState<Settlement[]>([]);
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
        const res = await fetchSettlements(cId, statusFilter || undefined);
        setSettlements(res.settlements || []);
      } catch (err: unknown) {
        setError(err instanceof Error ? err.message : "Failed to load settlements");
      } finally {
        setLoading(false);
      }
    }
    loadData();
  }, [statusFilter, chainFilter]);

  return (
    <div className="max-w-7xl mx-auto px-4 sm:px-6 py-6 sm:py-8 space-y-6 sm:space-y-8">
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-4 border-b border-border pb-6">
        <div>
          <h1 className="text-2xl sm:text-3xl font-extrabold tracking-tight">Escrow Settlements</h1>
          <p className="text-muted-foreground mt-1 text-xs sm:text-sm">
            Authoritative on-chain escrow release, refund, and dispute resolution tracking.
          </p>
        </div>
        <div className="flex flex-col sm:flex-row items-stretch sm:items-center gap-2 sm:gap-3 w-full md:w-auto">
          <select
            aria-label="Filter by status"
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value)}
            className="px-3 py-2 bg-card border border-border rounded-lg text-xs sm:text-sm focus:outline-none focus:ring-2 focus:ring-primary"
          >
            <option value="">All Statuses</option>
            <option value="PENDING_AUTHORIZATION">Pending Authorization</option>
            <option value="AUTHORIZED">Authorized</option>
            <option value="SUBMITTED">Submitted</option>
            <option value="CONFIRMED">Confirmed</option>
            <option value="BLOCKED">Blocked</option>
            <option value="FAILED">Failed</option>
            <option value="CANCELLED">Cancelled</option>
          </select>

          <select
            aria-label="Filter by network"
            value={chainFilter}
            onChange={(e) => setChainFilter(e.target.value)}
            className="px-3 py-2 bg-card border border-border rounded-lg text-xs sm:text-sm focus:outline-none focus:ring-2 focus:ring-primary"
          >
            <option value="">All Networks</option>
            <option value="31337">Anvil (31337)</option>
            <option value="84532">Base Sepolia (84532)</option>
            <option value="8453">Base Mainnet (8453)</option>
          </select>
        </div>
      </div>

      {error && (
        <div className="p-4 rounded-xl bg-red-500/10 border border-red-500/20 text-red-400 text-xs sm:text-sm break-words">
          {error}
        </div>
      )}

      {loading ? (
        <div className="flex items-center justify-center py-16 text-muted-foreground text-sm">
          Loading settlement records...
        </div>
      ) : settlements.length === 0 ? (
        <div className="text-center py-16 border border-dashed border-border rounded-xl p-4">
          <p className="text-muted-foreground text-sm">No settlement records found matching current criteria.</p>
        </div>
      ) : (
        <div className="space-y-4">
          {settlements.map((s) => (
            <div
              key={s.id}
              className="p-4 sm:p-5 bg-card border border-border rounded-xl shadow-sm hover:border-border/80 transition-colors"
            >
              <div className="flex flex-col lg:flex-row lg:items-center justify-between gap-4">
                <div className="space-y-2 flex-1 min-w-0">
                  <div className="flex flex-wrap items-center gap-2 sm:gap-3">
                    <span className="font-mono text-sm font-bold text-foreground">
                      {s.action}
                    </span>
                    {getStatusBadge(s.status)}
                    <span className="text-[11px] px-2 py-0.5 rounded bg-secondary text-muted-foreground border border-border">
                      Chain {s.chain_id}
                    </span>
                  </div>

                  <div className="text-xs text-muted-foreground space-y-1">
                    <p className="break-all">
                      <span className="font-medium text-foreground">Escrow ID:</span>{" "}
                      <span className="font-mono">{s.escrow_id}</span>
                    </p>
                    <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
                      <div>
                        <span className="font-medium text-foreground">Amount:</span>{" "}
                        <span className="font-semibold text-emerald-400">{formatAmount(s.amount)}</span>
                      </div>
                      <div>
                        <span className="font-medium text-foreground">Client:</span>{" "}
                        <span className="font-mono">{s.client_address.slice(0, 8)}...</span>
                      </div>
                      <div>
                        <span className="font-medium text-foreground">Beneficiary:</span>{" "}
                        <span className="font-mono">{s.beneficiary_address.slice(0, 8)}...</span>
                      </div>
                    </div>
                    {s.orchestration_id && (
                      <p className="break-all">
                        <span className="font-medium text-foreground">Orchestration:</span>{" "}
                        <Link
                          href={`/orchestrations/${s.orchestration_id}`}
                          className="text-primary hover:underline font-mono"
                        >
                          {s.orchestration_id}
                        </Link>
                      </p>
                    )}
                  </div>
                </div>

                <div className="text-left lg:text-right text-xs text-muted-foreground space-y-1 shrink-0 pt-2 lg:pt-0 border-t lg:border-t-0 border-border/40">
                  {s.settlement_tx_hash ? (
                    <div className="break-all">
                      <span className="font-medium text-foreground">Tx Hash:</span>{" "}
                      <span className="font-mono text-primary">{s.settlement_tx_hash.slice(0, 14)}...</span>
                      {s.block_number && (
                        <span className="ml-1 font-mono">
                          (Block #{s.block_number}, {s.confirmations} confs)
                        </span>
                      )}
                    </div>
                  ) : s.status === "SUBMITTED" ? (
                    <div className="text-blue-400 font-medium">Broadcast pending block inclusion...</div>
                  ) : null}

                  {s.blocked_reason && (
                    <div className="text-rose-400 max-w-md break-words">
                      <span className="font-medium">Block Reason:</span> {s.blocked_reason}
                    </div>
                  )}

                  {s.error_message && (
                    <div className="text-red-400 max-w-md break-words">
                      <span className="font-medium">Error:</span> {s.error_message}
                    </div>
                  )}

                  <div>
                    Created: {new Date(s.created_at).toLocaleString()}
                  </div>
                </div>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
