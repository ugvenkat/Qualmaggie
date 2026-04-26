"use client";

import { useEffect, useMemo, useState } from "react";
import {
  ResponsiveContainer, LineChart, Line,
  XAxis, YAxis, CartesianGrid, Tooltip,
  BarChart, Bar, Cell,
} from "recharts";
import { format, parseISO } from "date-fns";
import { Loader2 } from "lucide-react";
import { listBacktests, getBacktest, getBacktestSnapshots, getBacktestTrades } from "@/lib/api";
import { BackendBusy } from "@/components/BackendBusy";

// ─── Types ────────────────────────────────────────────────────────────────────

interface BacktestRun {
  backtest_run_id: number;
  run_name: string;
  start_date: string;
  end_date: string;
  initial_capital: number;
  final_capital: number | null;
  status: string;
}

interface Performance {
  total_trades: number;
  winning_trades: number;
  losing_trades: number;
  win_rate: number;
  avg_win_pct: number;
  avg_loss_pct: number;
  profit_factor: number;
  max_drawdown_pct: number;
  sharpe_ratio: number | null;
  total_return_pct: number;
}

interface Snapshot {
  snapshot_date: string;
  total_value: number;
}

interface Trade {
  pnl_pct: number | null;
  pnl: number | null;
}

// ─── Helpers ──────────────────────────────────────────────────────────────────

function f(v: number | null | undefined): number {
  if (v == null) return 0;
  return parseFloat(String(v));
}

function dollar(v: number | null | undefined) {
  if (v == null) return "—";
  return `$${f(v).toLocaleString("en-US", { minimumFractionDigits: 0, maximumFractionDigits: 0 })}`;
}

// ─── StatCard ─────────────────────────────────────────────────────────────────

function StatCard({ label, value, color, sub }: {
  label: string; value: string; color?: string; sub?: string;
}) {
  return (
    <div className="card flex flex-col gap-1">
      <div className="text-xs uppercase tracking-wider font-medium" style={{ color: "var(--text-muted)" }}>
        {label}
      </div>
      <div className="text-xl font-semibold" style={{ color: color ?? "var(--text-primary)" }}>
        {value}
      </div>
      {sub && (
        <div className="text-xs" style={{ color: "var(--text-muted)" }}>{sub}</div>
      )}
    </div>
  );
}

// ─── Equity Curve ─────────────────────────────────────────────────────────────

function EquityCurve({ snapshots }: { snapshots: Snapshot[] }) {
  const data = snapshots.map(s => ({
    date: s.snapshot_date.slice(0, 10),
    value: Math.round(f(s.total_value)),
  }));

  const values = data.map(d => d.value);
  const minVal = Math.min(...values);
  const maxVal = Math.max(...values);
  const pad = (maxVal - minVal) * 0.08;

  return (
    <div style={{ height: 260 }}>
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={data} margin={{ top: 8, right: 16, left: 0, bottom: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
          <XAxis
            dataKey="date"
            tick={{ fill: "var(--text-muted)", fontSize: 11 }}
            tickLine={false}
            axisLine={false}
            tickFormatter={d => {
              try { return format(parseISO(d), "MMM yy"); } catch { return d; }
            }}
            interval="preserveStartEnd"
          />
          <YAxis
            domain={[minVal - pad, maxVal + pad]}
            tick={{ fill: "var(--text-muted)", fontSize: 11 }}
            tickLine={false}
            axisLine={false}
            tickFormatter={v => `$${(v / 1000).toFixed(0)}k`}
            width={52}
          />
          <Tooltip
            contentStyle={{
              background: "var(--bg-elevated)",
              border: "1px solid var(--border)",
              borderRadius: 6,
              color: "var(--text-primary)",
              fontSize: 12,
            }}
            formatter={v => [dollar(Number(v)), "Portfolio"]}
            labelFormatter={d => {
              try { return format(parseISO(d as string), "MMM d, yyyy"); } catch { return d as string; }
            }}
          />
          <Line
            type="monotone"
            dataKey="value"
            stroke="var(--green)"
            strokeWidth={2}
            dot={false}
            activeDot={{ r: 3, fill: "var(--green)" }}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

// ─── Trade Distribution ───────────────────────────────────────────────────────

const BUCKETS = [
  { label: "< -10%", min: -Infinity, max: -0.10 },
  { label: "-10–-5%", min: -0.10, max: -0.05 },
  { label: "-5–0%", min: -0.05, max: 0 },
  { label: "0–5%", min: 0, max: 0.05 },
  { label: "5–10%", min: 0.05, max: 0.10 },
  { label: "> 10%", min: 0.10, max: Infinity },
];

function TradeDistribution({ trades }: { trades: Trade[] }) {
  const data = BUCKETS.map(b => ({
    label: b.label,
    count: trades.filter(t => {
      const v = f(t.pnl_pct);
      return v >= b.min && v < b.max;
    }).length,
    positive: b.min >= 0,
  }));

  const maxCount = Math.max(...data.map(d => d.count), 1);

  return (
    <div style={{ height: 200 }}>
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" vertical={false} />
          <XAxis
            dataKey="label"
            tick={{ fill: "var(--text-muted)", fontSize: 11 }}
            tickLine={false}
            axisLine={false}
          />
          <YAxis
            domain={[0, maxCount + 1]}
            tick={{ fill: "var(--text-muted)", fontSize: 11 }}
            tickLine={false}
            axisLine={false}
            allowDecimals={false}
            width={32}
          />
          <Tooltip
            contentStyle={{
              background: "var(--bg-elevated)",
              border: "1px solid var(--border)",
              borderRadius: 6,
              color: "var(--text-primary)",
              fontSize: 12,
            }}
            formatter={(v) => [v, "Trades"]}
          />
          <Bar dataKey="count" radius={[3, 3, 0, 0]}>
            {data.map((entry, idx) => (
              <Cell
                key={idx}
                fill={entry.positive ? "rgba(16,185,129,0.65)" : "rgba(239,68,68,0.65)"}
              />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

// ─── Monthly Heatmap ──────────────────────────────────────────────────────────

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

function cellBg(ret: number | null): string {
  if (ret == null) return "transparent";
  if (ret >= 0.05)  return "rgba(16,185,129,0.40)";
  if (ret >= 0.02)  return "rgba(16,185,129,0.22)";
  if (ret >= 0.005) return "rgba(16,185,129,0.10)";
  if (ret > -0.005) return "transparent";
  if (ret > -0.02)  return "rgba(239,68,68,0.10)";
  if (ret > -0.05)  return "rgba(239,68,68,0.22)";
  return "rgba(239,68,68,0.40)";
}

function cellFg(ret: number | null): string {
  if (ret == null) return "var(--text-muted)";
  return ret >= 0 ? "var(--green)" : "var(--red)";
}

function computeMonthlyReturns(snapshots: Snapshot[]): Record<string, Record<number, number | null>> {
  const byYearMonth: Record<string, number[]> = {};
  for (const s of snapshots) {
    const key = s.snapshot_date.slice(0, 7);
    if (!byYearMonth[key]) byYearMonth[key] = [];
    byYearMonth[key].push(f(s.total_value));
  }

  const result: Record<string, Record<number, number | null>> = {};
  for (const [key, values] of Object.entries(byYearMonth)) {
    const [year, monthStr] = key.split("-");
    const month = parseInt(monthStr, 10) - 1;
    if (!result[year]) result[year] = {};
    const first = values[0];
    const last = values[values.length - 1];
    result[year][month] = first > 0 ? (last - first) / first : null;
  }
  return result;
}

function MonthlyHeatmap({ snapshots }: { snapshots: Snapshot[] }) {
  const monthly = useMemo(() => computeMonthlyReturns(snapshots), [snapshots]);
  const years = Object.keys(monthly).sort();
  if (years.length === 0) return null;

  return (
    <div style={{ overflowX: "auto" }}>
      <table className="text-xs" style={{ borderCollapse: "separate", borderSpacing: "2px 3px", width: "100%" }}>
        <thead>
          <tr>
            <th className="text-left py-1 pr-4 font-medium" style={{ color: "var(--text-muted)", whiteSpace: "nowrap" }}>
              Year
            </th>
            {MONTHS.map(m => (
              <th key={m} className="py-1 text-center font-medium" style={{ color: "var(--text-muted)", minWidth: 46 }}>
                {m}
              </th>
            ))}
            <th className="py-1 text-center font-medium" style={{ color: "var(--text-muted)", minWidth: 56 }}>
              Full Yr
            </th>
          </tr>
        </thead>
        <tbody>
          {years.map(year => {
            const row = monthly[year];
            const monthVals = Array.from({ length: 12 }, (_, i) => row[i] ?? null).filter(v => v != null) as number[];
            const fullYr = monthVals.length > 0
              ? monthVals.reduce((acc, r) => acc * (1 + r), 1) - 1
              : null;

            return (
              <tr key={year}>
                <td className="py-1 pr-4 font-medium" style={{ color: "var(--text-secondary)", whiteSpace: "nowrap" }}>
                  {year}
                </td>
                {Array.from({ length: 12 }, (_, idx) => {
                  const ret = row[idx] ?? null;
                  return (
                    <td
                      key={idx}
                      className="py-1 text-center font-mono rounded"
                      style={{ background: cellBg(ret), color: cellFg(ret), minWidth: 46 }}
                    >
                      {ret != null
                        ? `${ret >= 0 ? "+" : ""}${(ret * 100).toFixed(1)}`
                        : <span style={{ opacity: 0.25 }}>—</span>
                      }
                    </td>
                  );
                })}
                <td
                  className="py-1 text-center font-mono font-semibold rounded"
                  style={{ background: cellBg(fullYr), color: cellFg(fullYr), minWidth: 56 }}
                >
                  {fullYr != null
                    ? `${fullYr >= 0 ? "+" : ""}${(fullYr * 100).toFixed(1)}`
                    : <span style={{ opacity: 0.25 }}>—</span>
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

// ─── Main Page ────────────────────────────────────────────────────────────────

export default function PerformancePage() {
  const [runs, setRuns] = useState<BacktestRun[]>([]);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [performance, setPerformance] = useState<Performance | null>(null);
  const [snapshots, setSnapshots] = useState<Snapshot[]>([]);
  const [trades, setTrades] = useState<Trade[]>([]);
  const [loadedRun, setLoadedRun] = useState<BacktestRun | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [initFailed, setInitFailed] = useState(false);

  const loadRun = async (id: number) => {
    setLoading(true);
    setError(null);
    try {
      const [runRes, snapshotsRes, tradesRes] = await Promise.all([
        getBacktest(id),
        getBacktestSnapshots(id),
        getBacktestTrades(id),
      ]);
      const detail = runRes.data;
      setLoadedRun(detail.run ?? detail);
      setPerformance(detail.performance ?? null);
      setSnapshots(snapshotsRes.data ?? []);
      setTrades(tradesRes.data ?? []);
      setSelectedId(id);
    } catch {
      setError("Failed to load run data");
    } finally {
      setLoading(false);
    }
  };

  const fetchList = () => {
    setInitFailed(false);
    listBacktests()
      .then(r => {
        const completed = (r.data as BacktestRun[]).filter(b => b.status === "Completed");
        setRuns(completed);
        if (completed.length > 0) loadRun(completed[0].backtest_run_id);
      })
      .catch(() => { setInitFailed(true); setError("Failed to load backtest runs"); });
  };

  useEffect(() => { fetchList(); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const returnPct = useMemo(() => {
    if (!loadedRun?.initial_capital || !loadedRun?.final_capital) return null;
    const init = f(loadedRun.initial_capital);
    const fin = f(loadedRun.final_capital);
    return init > 0 ? (fin - init) / init : null;
  }, [loadedRun]);

  return (
    <div style={{ maxWidth: 1100 }}>
      <div className="mb-5">
        <h1 className="text-xl font-semibold">Performance</h1>
        <p className="text-sm mt-1" style={{ color: "var(--text-secondary)" }}>
          Equity curve, monthly returns, and trade statistics
        </p>
      </div>

      {/* Run selector */}
      <div className="card mb-5 flex flex-wrap items-center gap-4" style={{ padding: "12px 16px" }}>
        <label className="text-xs font-medium shrink-0" style={{ color: "var(--text-muted)" }}>
          Backtest Run
        </label>
        <select
          value={selectedId ?? ""}
          onChange={e => { const id = Number(e.target.value); if (id) loadRun(id); }}
          className="text-sm px-2 py-1.5 rounded outline-none"
          style={{
            background: "var(--bg-elevated)",
            border: "1px solid var(--border)",
            color: "var(--text-primary)",
            minWidth: 280,
          }}
        >
          <option value="">Select a run…</option>
          {runs.map(r => {
            const init = f(r.initial_capital);
            const fin = f(r.final_capital);
            const ret = init && fin ? (((fin - init) / init) * 100).toFixed(1) : null;
            return (
              <option key={r.backtest_run_id} value={r.backtest_run_id}>
                #{r.backtest_run_id} {r.run_name}{" "}
                {r.start_date?.slice(0, 4)}–{r.end_date?.slice(0, 4)}
                {ret != null ? ` (${Number(ret) >= 0 ? "+" : ""}${ret}%)` : ""}
              </option>
            );
          })}
        </select>
        {loading && <Loader2 size={14} className="animate-spin" style={{ color: "var(--text-muted)" }} />}
        {loadedRun && !loading && (
          <span className="text-xs ml-auto" style={{ color: "var(--text-muted)" }}>
            {loadedRun.start_date?.slice(0, 10)} → {loadedRun.end_date?.slice(0, 10)}
            {" · "}{dollar(loadedRun.initial_capital)} → {dollar(loadedRun.final_capital ?? undefined)}
          </span>
        )}
      </div>

      {/* Initial load failure — backend busy */}
      {initFailed && !performance && (
        <BackendBusy onRetry={fetchList} />
      )}

      {/* Error loading a specific run (after initial load succeeded) */}
      {error && !initFailed && (
        <p className="text-sm mb-4" style={{ color: "var(--red)" }}>{error}</p>
      )}

      {!loading && !performance && !error && !initFailed && (
        <div className="card flex items-center justify-center" style={{ height: 200 }}>
          <p className="text-sm" style={{ color: "var(--text-muted)" }}>
            No completed backtest runs found. Run a backtest first.
          </p>
        </div>
      )}

      {performance && !loading && (
        <>
          {/* Stat cards */}
          <div className="grid grid-cols-3 gap-3 mb-5">
            <StatCard
              label="Total Return"
              value={returnPct != null ? `${returnPct >= 0 ? "+" : ""}${(returnPct * 100).toFixed(2)}%` : "—"}
              color={returnPct != null ? (returnPct >= 0 ? "var(--green)" : "var(--red)") : undefined}
              sub={`${dollar(loadedRun?.initial_capital)} → ${dollar(loadedRun?.final_capital ?? undefined)}`}
            />
            <StatCard
              label="Win Rate"
              value={`${(f(performance.win_rate) * 100).toFixed(1)}%`}
              sub={`${performance.winning_trades}W / ${performance.losing_trades}L · ${performance.total_trades} trades`}
            />
            <StatCard
              label="Profit Factor"
              value={f(performance.profit_factor).toFixed(2)}
              color={f(performance.profit_factor) >= 1 ? "var(--green)" : "var(--red)"}
            />
            <StatCard
              label="Max Drawdown"
              value={`-${(f(performance.max_drawdown_pct) * 100).toFixed(1)}%`}
              color="var(--red)"
            />
            <StatCard
              label="Avg Win"
              value={`+${(f(performance.avg_win_pct) * 100).toFixed(2)}%`}
              color="var(--green)"
            />
            <StatCard
              label="Avg Loss"
              value={`${(f(performance.avg_loss_pct) * 100).toFixed(2)}%`}
              color="var(--red)"
            />
          </div>

          {/* Equity curve */}
          {snapshots.length > 0 && (
            <div className="card mb-5">
              <div className="text-xs font-semibold uppercase tracking-wider mb-4" style={{ color: "var(--text-muted)" }}>
                Equity Curve
              </div>
              <EquityCurve snapshots={snapshots} />
            </div>
          )}

          {/* Trade distribution + monthly heatmap side-by-side */}
          <div className="grid grid-cols-2 gap-5 mb-5">
            {trades.length > 0 && (
              <div className="card">
                <div className="text-xs font-semibold uppercase tracking-wider mb-4" style={{ color: "var(--text-muted)" }}>
                  Trade Distribution
                </div>
                <TradeDistribution trades={trades} />
              </div>
            )}

            {snapshots.length > 0 && (
              <div className="card">
                <div className="text-xs font-semibold uppercase tracking-wider mb-4" style={{ color: "var(--text-muted)" }}>
                  Monthly Returns (%)
                </div>
                <MonthlyHeatmap snapshots={snapshots} />
                <div className="flex flex-wrap items-center gap-3 mt-4 text-xs" style={{ color: "var(--text-muted)" }}>
                  {[
                    { label: "≥+5%", bg: "rgba(16,185,129,0.40)", fg: "var(--green)" },
                    { label: "+2–5%", bg: "rgba(16,185,129,0.22)", fg: "var(--green)" },
                    { label: "~0%", bg: "transparent", fg: "var(--text-muted)", border: true },
                    { label: "-2–5%", bg: "rgba(239,68,68,0.22)", fg: "var(--red)" },
                    { label: "≤-5%", bg: "rgba(239,68,68,0.40)", fg: "var(--red)" },
                  ].map(({ label, bg, fg, border }) => (
                    <span
                      key={label}
                      className="px-2 py-0.5 rounded font-mono"
                      style={{ background: bg, color: fg, border: border ? "1px solid var(--border)" : undefined }}
                    >
                      {label}
                    </span>
                  ))}
                </div>
              </div>
            )}
          </div>
        </>
      )}
    </div>
  );
}
