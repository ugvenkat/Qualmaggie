"use client";

import { useEffect, useState } from "react";
import { RefreshCw, Loader2, ChevronLeft, ChevronRight } from "lucide-react";
import { getOpenPositions, getPositionsHistory } from "@/lib/api";
import { BackendBusy } from "@/components/BackendBusy";

// ─── Types ────────────────────────────────────────────────────────────────────

interface OpenPosition {
  position_id: number;
  symbol: string;
  sector: string;
  entry_date: string;
  entry_price: number;
  shares: number;
  initial_stop_loss: number;
  current_stop: number;
  partial_sold_shares: number | null;
  partial_sold_price: number | null;
  partial_sold_date: string | null;
  pattern_type: string;
  risk_amount: number;
}

interface Trade {
  trade_id: number;
  symbol: string;
  sector: string;
  entry_date: string;
  exit_date: string | null;
  entry_price: number;
  exit_price: number | null;
  shares: number;
  initial_stop_loss: number;
  partial_exit_date: string | null;
  partial_exit_price: number | null;
  partial_shares: number | null;
  pattern_type: string;
  pnl: number | null;
  pnl_pct: number | null;
  exit_reason: string | null;
  backtest_run_id: number | null;
}

// ─── Constants ────────────────────────────────────────────────────────────────

const PAGE_SIZE = 50;

// ─── Helpers ──────────────────────────────────────────────────────────────────

function f(v: number | null | undefined): number {
  if (v == null) return 0;
  return parseFloat(String(v));
}

function holdDays(entryDate: string, exitDate?: string | null): number {
  const from = new Date(entryDate).getTime();
  const to = exitDate ? new Date(exitDate).getTime() : Date.now();
  return Math.round((to - from) / 86400000);
}

function Badge({ color, children }: { color: "green" | "yellow" | "red" | "muted"; children: React.ReactNode }) {
  const map = {
    green: { bg: "rgba(16,185,129,0.12)", fg: "var(--green)" },
    yellow: { bg: "rgba(234,179,8,0.12)", fg: "var(--yellow)" },
    red: { bg: "rgba(239,68,68,0.12)", fg: "var(--red)" },
    muted: { bg: "rgba(100,100,100,0.12)", fg: "var(--text-muted)" },
  };
  return (
    <span
      className="text-xs px-2 py-0.5 rounded font-medium"
      style={{ background: map[color].bg, color: map[color].fg }}
    >
      {children}
    </span>
  );
}

// ─── Open Positions Table ─────────────────────────────────────────────────────

function OpenPositionsTable({ positions }: { positions: OpenPosition[] }) {
  if (positions.length === 0) {
    return (
      <p className="text-sm py-6 text-center" style={{ color: "var(--text-muted)" }}>
        No open positions
      </p>
    );
  }

  return (
    <div style={{ overflowX: "auto" }}>
      <table className="w-full text-sm" style={{ borderCollapse: "collapse" }}>
        <thead>
          <tr style={{ borderBottom: "1px solid var(--border)" }}>
            {["Symbol", "Sector", "Pattern", "Entry Date", "Entry $", "Shares", "Init Stop", "Curr Stop", "Hold", "Partial"].map(h => (
              <th
                key={h}
                className="text-left py-2 pr-5 text-xs font-medium uppercase tracking-wide"
                style={{ color: "var(--text-muted)", whiteSpace: "nowrap" }}
              >
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {positions.map(p => {
            const days = holdDays(p.entry_date);
            const stopPct = p.entry_price > 0
              ? ((f(p.current_stop) - f(p.entry_price)) / f(p.entry_price)) * 100
              : null;

            return (
              <tr key={p.position_id} style={{ borderBottom: "1px solid var(--border-dim)" }}>
                <td className="py-2.5 pr-5 font-mono font-semibold" style={{ color: "var(--text-primary)" }}>
                  {p.symbol}
                </td>
                <td className="py-2.5 pr-5 text-xs" style={{ color: "var(--text-secondary)" }}>
                  {p.sector || "—"}
                </td>
                <td className="py-2.5 pr-5">
                  <Badge color="muted">{p.pattern_type}</Badge>
                </td>
                <td className="py-2.5 pr-5 font-mono text-xs" style={{ color: "var(--text-secondary)" }}>
                  {p.entry_date?.slice(0, 10)}
                </td>
                <td className="py-2.5 pr-5 font-mono" style={{ color: "var(--text-primary)" }}>
                  ${f(p.entry_price).toFixed(2)}
                </td>
                <td className="py-2.5 pr-5 font-mono" style={{ color: "var(--text-primary)" }}>
                  {p.shares}
                </td>
                <td className="py-2.5 pr-5 font-mono text-xs" style={{ color: "var(--red)" }}>
                  ${f(p.initial_stop_loss).toFixed(2)}
                </td>
                <td className="py-2.5 pr-5 font-mono text-xs">
                  <span style={{ color: "var(--red)" }}>${f(p.current_stop).toFixed(2)}</span>
                  {stopPct != null && (
                    <span className="ml-1 text-xs" style={{ color: "var(--text-muted)" }}>
                      ({stopPct.toFixed(1)}%)
                    </span>
                  )}
                </td>
                <td className="py-2.5 pr-5 font-mono text-xs" style={{ color: days > 15 ? "var(--yellow)" : "var(--text-secondary)" }}>
                  {days}d
                </td>
                <td className="py-2.5 pr-5 text-xs" style={{ color: "var(--text-muted)" }}>
                  {p.partial_sold_shares
                    ? <span style={{ color: "var(--green)" }}>{p.partial_sold_shares} @ ${f(p.partial_sold_price).toFixed(2)}</span>
                    : "—"
                  }
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

// ─── Trades History Table ─────────────────────────────────────────────────────

function TradesTable({ trades }: { trades: Trade[] }) {
  if (trades.length === 0) {
    return (
      <p className="text-sm py-6 text-center" style={{ color: "var(--text-muted)" }}>
        No closed trades
      </p>
    );
  }

  return (
    <div style={{ overflowX: "auto" }}>
      <table className="w-full text-sm" style={{ borderCollapse: "collapse" }}>
        <thead>
          <tr style={{ borderBottom: "1px solid var(--border)" }}>
            {["Symbol", "Sector", "Pattern", "Entry", "Exit", "Entry $", "Exit $", "Shares", "PnL %", "Hold", "Exit Reason"].map(h => (
              <th
                key={h}
                className="text-left py-2 pr-5 text-xs font-medium uppercase tracking-wide"
                style={{ color: "var(--text-muted)", whiteSpace: "nowrap" }}
              >
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {trades.map(t => {
            const pnlPct = t.pnl_pct != null ? f(t.pnl_pct) : null;
            const win = pnlPct != null && pnlPct > 0;
            const loss = pnlPct != null && pnlPct <= 0;
            const days = t.exit_date ? holdDays(t.entry_date, t.exit_date) : null;

            return (
              <tr
                key={t.trade_id}
                style={{
                  borderBottom: "1px solid var(--border-dim)",
                  background: win
                    ? "rgba(16,185,129,0.04)"
                    : loss
                    ? "rgba(239,68,68,0.04)"
                    : "transparent",
                }}
              >
                <td className="py-2.5 pr-5 font-mono font-semibold" style={{ color: "var(--text-primary)" }}>
                  {t.symbol}
                </td>
                <td className="py-2.5 pr-5 text-xs" style={{ color: "var(--text-secondary)" }}>
                  {t.sector || "—"}
                </td>
                <td className="py-2.5 pr-5">
                  <Badge color="muted">{t.pattern_type}</Badge>
                </td>
                <td className="py-2.5 pr-5 font-mono text-xs" style={{ color: "var(--text-secondary)" }}>
                  {t.entry_date?.slice(0, 10)}
                </td>
                <td className="py-2.5 pr-5 font-mono text-xs" style={{ color: "var(--text-secondary)" }}>
                  {t.exit_date?.slice(0, 10) ?? "—"}
                </td>
                <td className="py-2.5 pr-5 font-mono text-xs" style={{ color: "var(--text-primary)" }}>
                  ${f(t.entry_price).toFixed(2)}
                </td>
                <td className="py-2.5 pr-5 font-mono text-xs" style={{ color: "var(--text-primary)" }}>
                  {t.exit_price != null ? `$${f(t.exit_price).toFixed(2)}` : "—"}
                </td>
                <td className="py-2.5 pr-5 font-mono text-xs" style={{ color: "var(--text-secondary)" }}>
                  {t.shares}
                </td>
                <td
                  className="py-2.5 pr-5 font-mono text-xs font-medium"
                  style={{ color: win ? "var(--green)" : loss ? "var(--red)" : "var(--text-muted)" }}
                >
                  {pnlPct != null
                    ? `${pnlPct >= 0 ? "+" : ""}${(pnlPct * 100).toFixed(2)}%`
                    : "—"}
                </td>
                <td className="py-2.5 pr-5 font-mono text-xs" style={{ color: "var(--text-secondary)" }}>
                  {days != null ? `${days}d` : "—"}
                </td>
                <td className="py-2.5 pr-5 text-xs">
                  {t.exit_reason ? (
                    <Badge color={t.exit_reason === "StopLoss" ? "red" : t.exit_reason === "TrailStop" ? "yellow" : "muted"}>
                      {t.exit_reason}
                    </Badge>
                  ) : "—"}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

// ─── Pagination Controls ──────────────────────────────────────────────────────

function Pagination({
  page,
  totalPages,
  totalItems,
  onPrev,
  onNext,
}: {
  page: number;
  totalPages: number;
  totalItems: number;
  onPrev: () => void;
  onNext: () => void;
}) {
  if (totalPages <= 1) return null;

  const btnBase: React.CSSProperties = {
    display: "flex",
    alignItems: "center",
    gap: 4,
    padding: "4px 10px",
    fontSize: 12,
    borderRadius: 4,
    border: "1px solid var(--border)",
    background: "var(--bg-elevated)",
    color: "var(--text-secondary)",
    cursor: "pointer",
  };
  const btnDisabled: React.CSSProperties = {
    ...btnBase,
    opacity: 0.35,
    cursor: "default",
  };

  return (
    <div
      className="flex items-center justify-between mt-4 pt-3"
      style={{ borderTop: "1px solid var(--border)" }}
    >
      <button style={page === 1 ? btnDisabled : btnBase} onClick={onPrev} disabled={page === 1}>
        <ChevronLeft size={13} />
        Previous
      </button>

      <span className="text-xs" style={{ color: "var(--text-muted)" }}>
        Page <span style={{ color: "var(--text-primary)", fontWeight: 600 }}>{page}</span>
        {" "}of{" "}
        <span style={{ color: "var(--text-primary)", fontWeight: 600 }}>{totalPages}</span>
        <span className="ml-2" style={{ color: "var(--text-muted)" }}>
          ({totalItems.toLocaleString()} trades total)
        </span>
      </span>

      <button style={page === totalPages ? btnDisabled : btnBase} onClick={onNext} disabled={page === totalPages}>
        Next
        <ChevronRight size={13} />
      </button>
    </div>
  );
}

// ─── Main Page ────────────────────────────────────────────────────────────────

type Tab = "open" | "history";

export default function PositionsPage() {
  const [tab, setTab] = useState<Tab>("open");
  const [positions, setPositions] = useState<OpenPosition[]>([]);
  const [trades, setTrades] = useState<Trade[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [historyPage, setHistoryPage] = useState(1);

  const fetchAll = async () => {
    setLoading(true);
    setError(null);
    setHistoryPage(1);
    try {
      const [posRes, tradeRes] = await Promise.all([
        getOpenPositions(),
        getPositionsHistory(),
      ]);
      setPositions(posRes.data ?? []);
      setTrades(tradeRes.data ?? []);
    } catch {
      setError("Failed to load positions");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { fetchAll(); }, []);

  // Pagination derived values
  const totalTrades = trades.length;
  const totalPages = Math.max(1, Math.ceil(totalTrades / PAGE_SIZE));
  const pagedTrades = trades.slice((historyPage - 1) * PAGE_SIZE, historyPage * PAGE_SIZE);

  const handleTabChange = (t: Tab) => {
    setTab(t);
    if (t === "history") setHistoryPage(1);
  };

  return (
    <div style={{ maxWidth: 1200 }}>
      <div className="mb-5 flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold">Positions &amp; Trades</h1>
          <p className="text-sm mt-1" style={{ color: "var(--text-secondary)" }}>
            Live open positions and closed trade history
          </p>
        </div>
        <button
          onClick={fetchAll}
          disabled={loading}
          className="flex items-center gap-1.5 text-xs px-3 py-1.5 rounded transition-opacity"
          style={{
            background: "var(--bg-elevated)",
            border: "1px solid var(--border)",
            color: "var(--text-secondary)",
            opacity: loading ? 0.5 : 1,
          }}
        >
          <RefreshCw size={11} className={loading ? "animate-spin" : ""} />
          Refresh
        </button>
      </div>

      {/* Tabs */}
      <div className="flex gap-1 mb-5" style={{ borderBottom: "1px solid var(--border)" }}>
        {([["open", `Open (${positions.length})`], ["history", `History (${totalTrades})`]] as const).map(([t, label]) => (
          <button
            key={t}
            onClick={() => handleTabChange(t)}
            className="px-4 py-2 text-sm font-medium transition-colors"
            style={{
              color: tab === t ? "var(--text-primary)" : "var(--text-muted)",
              borderBottom: tab === t ? "2px solid var(--green)" : "2px solid transparent",
              marginBottom: -1,
            }}
          >
            {label}
          </button>
        ))}
      </div>

      {loading && (
        <div className="flex items-center justify-center py-12">
          <Loader2 size={20} className="animate-spin" style={{ color: "var(--text-muted)" }} />
        </div>
      )}

      {!loading && error && (
        <BackendBusy onRetry={fetchAll} />
      )}

      {!loading && !error && (
        <div className="card">
          {tab === "open" && <OpenPositionsTable positions={positions} />}
          {tab === "history" && (
            <>
              <TradesTable trades={pagedTrades} />
              <Pagination
                page={historyPage}
                totalPages={totalPages}
                totalItems={totalTrades}
                onPrev={() => setHistoryPage(p => Math.max(1, p - 1))}
                onNext={() => setHistoryPage(p => Math.min(totalPages, p + 1))}
              />
            </>
          )}
        </div>
      )}
    </div>
  );
}
