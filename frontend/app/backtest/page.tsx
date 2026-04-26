"use client";

import { useEffect, useMemo, useState } from "react";
import {
  ResponsiveContainer, LineChart, Line,
  XAxis, YAxis, CartesianGrid, Tooltip,
} from "recharts";
import {
  useReactTable, getCoreRowModel, getSortedRowModel,
  flexRender, createColumnHelper, SortingState,
} from "@tanstack/react-table";
import { format, parseISO } from "date-fns";
import { ChevronUp, ChevronDown, ChevronsUpDown, Loader2 } from "lucide-react";
import { runBacktest, listBacktests, getBacktest, getBacktestTrades, getBacktestSnapshots } from "@/lib/api";
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
  created_at: string;
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
  total_return_pct: number;
}

interface Trade {
  symbol: string;
  entry_date: string;
  exit_date: string | null;
  entry_price: number;
  exit_price: number | null;
  shares: number;
  pnl: number | null;
  pnl_pct: number | null;
  exit_reason: string | null;
}

interface Snapshot {
  snapshot_date: string;
  total_value: number;
}

// ─── Sub-components ───────────────────────────────────────────────────────────

function Label({ children }: { children: React.ReactNode }) {
  return (
    <label className="block text-xs font-medium mb-1" style={{ color: "var(--text-secondary)" }}>
      {children}
    </label>
  );
}

function Input({ value, onChange, type = "text", min, max, step }: {
  value: string; onChange: (v: string) => void;
  type?: string; min?: string; max?: string; step?: string;
}) {
  return (
    <input
      type={type} value={value} min={min} max={max} step={step}
      onChange={e => onChange(e.target.value)}
      className="w-full text-sm px-3 py-2 rounded outline-none"
      style={{
        background: "var(--bg-elevated)",
        border: "1px solid var(--border)",
        color: "var(--text-primary)",
      }}
    />
  );
}

function StatCard({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <div className="card flex flex-col gap-1">
      <div className="text-xs uppercase tracking-wider font-medium" style={{ color: "var(--text-muted)" }}>
        {label}
      </div>
      <div className="text-xl font-semibold" style={{ color: color ?? "var(--text-primary)" }}>
        {value}
      </div>
    </div>
  );
}

function pct(v: number | null | undefined, decimals = 1) {
  if (v == null) return "—";
  return `${(v * 100).toFixed(decimals)}%`;
}

function dollar(v: number | null | undefined) {
  if (v == null) return "—";
  return `$${v.toLocaleString("en-US", { minimumFractionDigits: 0, maximumFractionDigits: 0 })}`;
}

// ─── Equity Curve ─────────────────────────────────────────────────────────────

function EquityCurve({ snapshots }: { snapshots: Snapshot[] }) {
  const data = snapshots.map(s => ({
    date: s.snapshot_date.slice(0, 10),
    value: Math.round(s.total_value),
  }));

  const minVal = Math.min(...data.map(d => d.value));
  const maxVal = Math.max(...data.map(d => d.value));
  const pad = (maxVal - minVal) * 0.08;

  return (
    <div style={{ height: 240 }}>
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
            formatter={(v) => [dollar(Number(v)), "Portfolio"]}
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

// ─── Trades Table ─────────────────────────────────────────────────────────────

const col = createColumnHelper<Trade>();

const COLUMNS = [
  col.accessor("entry_date", {
    header: "Entry Date",
    cell: i => i.getValue()?.slice(0, 10) ?? "—",
  }),
  col.accessor("exit_date", {
    header: "Exit Date",
    cell: i => i.getValue()?.slice(0, 10) ?? "—",
  }),
  col.accessor("symbol", { header: "Symbol" }),
  col.accessor("entry_price", {
    header: "Entry $",
    cell: i => `$${Number(i.getValue()).toFixed(2)}`,
  }),
  col.accessor("exit_price", {
    header: "Exit $",
    cell: i => i.getValue() != null ? `$${Number(i.getValue()).toFixed(2)}` : "—",
  }),
  col.accessor("shares", { header: "Shares" }),
  col.accessor("pnl_pct", {
    header: "PnL %",
    cell: i => {
      const v = i.getValue();
      if (v == null) return "—";
      const p = (v * 100).toFixed(2);
      return `${Number(p) >= 0 ? "+" : ""}${p}%`;
    },
  }),
  col.accessor(
    row => row.exit_date && row.entry_date
      ? Math.round((new Date(row.exit_date).getTime() - new Date(row.entry_date).getTime()) / 86400000)
      : null,
    {
      id: "hold_days",
      header: "Hold",
      cell: i => i.getValue() != null ? `${i.getValue()}d` : "—",
    }
  ),
  col.accessor("exit_reason", {
    header: "Exit Reason",
    cell: i => i.getValue() ?? "—",
  }),
];

const PAGE_SIZE = 20;

function TradesTable({ trades }: { trades: Trade[] }) {
  const [sorting, setSorting] = useState<SortingState>([{ id: "entry_date", desc: true }]);
  const [page, setPage] = useState(0);

  const table = useReactTable({
    data: trades,
    columns: COLUMNS,
    state: { sorting },
    onSortingChange: setSorting,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
  });

  const allRows = table.getRowModel().rows;
  const pageCount = Math.ceil(allRows.length / PAGE_SIZE);
  const pageRows = allRows.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE);

  return (
    <div>
      <div style={{ overflowX: "auto" }}>
        <table className="w-full text-sm" style={{ borderCollapse: "collapse" }}>
          <thead>
            <tr style={{ borderBottom: "1px solid var(--border)" }}>
              {table.getFlatHeaders().map(header => {
                const sorted = header.column.getIsSorted();
                return (
                  <th
                    key={header.id}
                    className="text-left py-2 pr-4 text-xs font-medium uppercase tracking-wide select-none"
                    style={{
                      color: "var(--text-muted)",
                      cursor: header.column.getCanSort() ? "pointer" : "default",
                      whiteSpace: "nowrap",
                    }}
                    onClick={header.column.getToggleSortingHandler()}
                  >
                    <span className="inline-flex items-center gap-1">
                      {flexRender(header.column.columnDef.header, header.getContext())}
                      {header.column.getCanSort() && (
                        sorted === "asc" ? <ChevronUp size={11} /> :
                        sorted === "desc" ? <ChevronDown size={11} /> :
                        <ChevronsUpDown size={11} style={{ opacity: 0.4 }} />
                      )}
                    </span>
                  </th>
                );
              })}
            </tr>
          </thead>
          <tbody>
            {pageRows.map(row => {
              const pnl = row.original.pnl;
              const win = pnl != null && pnl > 0;
              const loss = pnl != null && pnl <= 0;
              return (
                <tr
                  key={row.id}
                  style={{
                    borderBottom: "1px solid var(--border-dim)",
                    background: win
                      ? "rgba(16,185,129,0.05)"
                      : loss
                      ? "rgba(239,68,68,0.05)"
                      : "transparent",
                  }}
                >
                  {row.getVisibleCells().map(cell => (
                    <td
                      key={cell.id}
                      className="py-2 pr-4"
                      style={{
                        color:
                          cell.column.id === "pnl_pct"
                            ? win ? "var(--green)" : loss ? "var(--red)" : "var(--text-primary)"
                            : "var(--text-primary)",
                        whiteSpace: "nowrap",
                      }}
                    >
                      {flexRender(cell.column.columnDef.cell, cell.getContext())}
                    </td>
                  ))}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {pageCount > 1 && (
        <div className="flex items-center justify-between mt-3">
          <span className="text-xs" style={{ color: "var(--text-muted)" }}>
            {page * PAGE_SIZE + 1}–{Math.min((page + 1) * PAGE_SIZE, allRows.length)} of {allRows.length} trades
          </span>
          <div className="flex gap-2">
            <button
              onClick={() => setPage(p => Math.max(0, p - 1))}
              disabled={page === 0}
              className="text-xs px-3 py-1 rounded"
              style={{
                background: "var(--bg-elevated)", border: "1px solid var(--border)",
                color: "var(--text-secondary)", opacity: page === 0 ? 0.4 : 1,
              }}
            >
              Prev
            </button>
            <button
              onClick={() => setPage(p => Math.min(pageCount - 1, p + 1))}
              disabled={page >= pageCount - 1}
              className="text-xs px-3 py-1 rounded"
              style={{
                background: "var(--bg-elevated)", border: "1px solid var(--border)",
                color: "var(--text-secondary)", opacity: page >= pageCount - 1 ? 0.4 : 1,
              }}
            >
              Next
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

// ─── Main Page ────────────────────────────────────────────────────────────────

export default function BacktestPage() {
  const today = new Date().toISOString().slice(0, 10);

  const [startDate, setStartDate] = useState("2021-01-01");
  const [endDate, setEndDate] = useState(today);
  const [runName, setRunName] = useState("Backtest");

  const [isRunning, setIsRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [listFailed, setListFailed] = useState(false);

  const [previousRuns, setPreviousRuns] = useState<BacktestRun[]>([]);
  const [selectedRunId, setSelectedRunId] = useState<number | null>(null);

  const [performance, setPerformance] = useState<Performance | null>(null);
  const [snapshots, setSnapshots] = useState<Snapshot[]>([]);
  const [trades, setTrades] = useState<Trade[]>([]);
  const [loadedRun, setLoadedRun] = useState<BacktestRun | null>(null);

  const fetchList = () => {
    setListFailed(false);
    listBacktests()
      .then(r => {
        const runs: BacktestRun[] = r.data;
        setPreviousRuns(runs.filter(r => r.status === "Completed").reverse());
      })
      .catch(() => setListFailed(true));
  };

  useEffect(() => { fetchList(); }, []);

  const loadResults = async (runId: number) => {
    const [runRes, tradesRes, snapshotsRes] = await Promise.all([
      getBacktest(runId),
      getBacktestTrades(runId),
      getBacktestSnapshots(runId),
    ]);
    const runData = runRes.data;
    setLoadedRun(runData.run ?? runData);
    setPerformance(runData.performance ?? null);
    setTrades(tradesRes.data ?? []);
    setSnapshots(snapshotsRes.data ?? []);
    setSelectedRunId(runId);
  };

  const handleRun = async () => {
    setIsRunning(true);
    setError(null);
    setPerformance(null);
    setSnapshots([]);
    setTrades([]);
    setLoadedRun(null);
    try {
      const res = await runBacktest({ run_name: runName, start_date: startDate, end_date: endDate });
      const newRun: BacktestRun = res.data;
      setPreviousRuns(prev => [newRun, ...prev.filter(r => r.backtest_run_id !== newRun.backtest_run_id)]);
      await loadResults(newRun.backtest_run_id);
    } catch (e: unknown) {
      const err = e as { message?: string };
      setError(err.message ?? "Backtest failed");
    } finally {
      setIsRunning(false);
    }
  };

  const handleSelectRun = async (e: React.ChangeEvent<HTMLSelectElement>) => {
    const id = Number(e.target.value);
    if (!id) return;
    try {
      await loadResults(id);
    } catch {
      setError("Failed to load run");
    }
  };

  const returnPct = useMemo(() => {
    if (!loadedRun?.initial_capital || !loadedRun?.final_capital) return null;
    const init = parseFloat(String(loadedRun.initial_capital));
    const fin = parseFloat(String(loadedRun.final_capital));
    return (fin - init) / init;
  }, [loadedRun]);

  const hasResults = performance !== null;

  return (
    <div style={{ maxWidth: 1200 }}>
      <div className="mb-5">
        <h1 className="text-xl font-semibold">Backtest</h1>
        <p className="text-sm mt-1" style={{ color: "var(--text-secondary)" }}>
          Run historical simulations and analyse results
        </p>
      </div>

      <div className="flex gap-5 items-start">

        {/* ── Left: Settings Panel ── */}
        <div className="card flex flex-col gap-4 shrink-0" style={{ width: 240 }}>
          <div className="text-xs font-semibold uppercase tracking-wider" style={{ color: "var(--text-muted)" }}>
            New Run
          </div>

          <div>
            <Label>Run Name</Label>
            <Input value={runName} onChange={setRunName} />
          </div>
          <div>
            <Label>Start Date</Label>
            <Input type="date" value={startDate} onChange={setStartDate} max={endDate} />
          </div>
          <div>
            <Label>End Date</Label>
            <Input type="date" value={endDate} onChange={setEndDate} min={startDate} max={today} />
          </div>

          <div className="text-xs py-2 px-2 rounded" style={{
            background: "var(--bg-elevated)", color: "var(--text-muted)",
            border: "1px solid var(--border-dim)",
          }}>
            Capital &amp; risk % come from <span style={{ color: "var(--text-secondary)" }}>settings.json</span>
          </div>

          <button
            onClick={handleRun}
            disabled={isRunning}
            className="flex items-center justify-center gap-2 w-full py-2.5 rounded font-semibold text-sm transition-opacity"
            style={{
              background: isRunning ? "var(--bg-elevated)" : "var(--green)",
              color: isRunning ? "var(--text-muted)" : "#fff",
              border: isRunning ? "1px solid var(--border)" : "none",
            }}
          >
            {isRunning && <Loader2 size={14} className="animate-spin" />}
            {isRunning ? "Running…" : "Run Backtest"}
          </button>


          {error && (
            <p className="text-xs" style={{ color: "var(--red)" }}>{error}</p>
          )}

          {/* Previous runs */}
          {previousRuns.length > 0 && (
            <div style={{ borderTop: "1px solid var(--border)", paddingTop: "1rem" }}>
              <Label>Load Previous Run</Label>
              <select
                onChange={handleSelectRun}
                value={selectedRunId ?? ""}
                className="w-full text-sm px-2 py-2 rounded outline-none"
                style={{
                  background: "var(--bg-elevated)",
                  border: "1px solid var(--border)",
                  color: "var(--text-primary)",
                }}
              >
                <option value="">Select run…</option>
                {previousRuns.map(r => {
                  const init = r.initial_capital ? parseFloat(String(r.initial_capital)) : 0;
                  const fin = r.final_capital ? parseFloat(String(r.final_capital)) : 0;
                  const ret = init && fin
                    ? (((fin - init) / init) * 100).toFixed(1)
                    : null;
                  return (
                    <option key={r.backtest_run_id} value={r.backtest_run_id}>
                      #{r.backtest_run_id} {r.run_name} {ret != null ? `(${Number(ret) >= 0 ? "+" : ""}${ret}%)` : ""}
                    </option>
                  );
                })}
              </select>
            </div>
          )}
        </div>

        {/* ── Right: Results ── */}
        <div className="flex-1 min-w-0 flex flex-col gap-5">
          {!hasResults && !isRunning && listFailed && (
            <BackendBusy onRetry={fetchList} />
          )}

          {!hasResults && !isRunning && !listFailed && (
            <div className="card flex items-center justify-center" style={{ height: 200 }}>
              <p className="text-sm" style={{ color: "var(--text-muted)" }}>
                Run a backtest or load a previous run to see results.
              </p>
            </div>
          )}

          {isRunning && (
            <div className="card flex flex-col items-center justify-center gap-3" style={{ height: 200 }}>
              <Loader2 size={28} className="animate-spin" style={{ color: "var(--green)" }} />
              <p className="text-sm" style={{ color: "var(--text-secondary)" }}>
                Running simulation… this may take a minute
              </p>
            </div>
          )}

          {hasResults && !isRunning && (
            <>
              {/* Run header */}
              {loadedRun && (
                <div className="flex items-center justify-between">
                  <div>
                    <span className="text-sm font-semibold">{loadedRun.run_name}</span>
                    <span className="text-xs ml-2" style={{ color: "var(--text-muted)" }}>
                      {loadedRun.start_date?.slice(0, 10)} → {loadedRun.end_date?.slice(0, 10)}
                      {" · "}{dollar(loadedRun.initial_capital)} → {dollar(loadedRun.final_capital ?? undefined)}
                    </span>
                  </div>
                  <span
                    className="text-sm font-semibold"
                    style={{ color: (returnPct ?? 0) >= 0 ? "var(--green)" : "var(--red)" }}
                  >
                    {returnPct != null ? `${returnPct >= 0 ? "+" : ""}${(returnPct * 100).toFixed(2)}%` : "—"}
                  </span>
                </div>
              )}

              {/* Summary cards */}
              <div className="grid grid-cols-3 gap-3">
                <StatCard
                  label="Total Return"
                  value={pct(returnPct)}
                  color={(returnPct ?? 0) >= 0 ? "var(--green)" : "var(--red)"}
                />
                <StatCard
                  label="Win Rate"
                  value={performance ? `${(parseFloat(String(performance.win_rate)) * 100).toFixed(1)}%` : "—"}
                  color="var(--text-primary)"
                />
                <StatCard
                  label="Profit Factor"
                  value={performance ? parseFloat(String(performance.profit_factor)).toFixed(2) : "—"}
                  color={performance && parseFloat(String(performance.profit_factor)) >= 1 ? "var(--green)" : "var(--red)"}
                />
                <StatCard
                  label="Max Drawdown"
                  value={performance ? `-${(parseFloat(String(performance.max_drawdown_pct)) * 100).toFixed(1)}%` : "—"}
                  color="var(--red)"
                />
                <StatCard
                  label="Avg Win"
                  value={performance ? `+${(parseFloat(String(performance.avg_win_pct)) * 100).toFixed(2)}%` : "—"}
                  color="var(--green)"
                />
                <StatCard
                  label="Avg Loss"
                  value={performance ? `${(parseFloat(String(performance.avg_loss_pct)) * 100).toFixed(2)}%` : "—"}
                  color="var(--red)"
                />
              </div>

              {/* Equity curve */}
              {snapshots.length > 0 && (
                <div className="card">
                  <div className="text-xs font-semibold uppercase tracking-wider mb-4"
                    style={{ color: "var(--text-muted)" }}>
                    Equity Curve
                  </div>
                  <EquityCurve snapshots={snapshots} />
                </div>
              )}

              {/* Trades table */}
              {trades.length > 0 && (
                <div className="card">
                  <div className="flex items-center justify-between mb-4">
                    <div className="text-xs font-semibold uppercase tracking-wider"
                      style={{ color: "var(--text-muted)" }}>
                      Trades
                    </div>
                    <div className="text-xs" style={{ color: "var(--text-muted)" }}>
                      {performance?.winning_trades ?? 0}W / {performance?.losing_trades ?? 0}L
                      {" · "}{trades.length} total
                    </div>
                  </div>
                  <TradesTable trades={trades} />
                </div>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}
