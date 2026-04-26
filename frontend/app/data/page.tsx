"use client";

import { useEffect, useRef, useState } from "react";
import { RefreshCw, Loader2, Upload } from "lucide-react";
import { updatePrices, updateEarnings, importFolder, importCsvFile, getDataStatus, resetBacktests, hardReset } from "@/lib/api";
import { BackendBusy } from "@/components/BackendBusy";

// ─── Types ────────────────────────────────────────────────────────────────────

interface TickerStatus {
  symbol: string;
  sector: string;
  is_active: boolean;
  row_count: number;
  earliest_date: string | null;
  last_date: string | null;
}

// ─── ActionButton ─────────────────────────────────────────────────────────────

function ActionButton({
  label,
  description,
  onRun,
}: {
  label: string;
  description: string;
  onRun: () => Promise<unknown>;
}) {
  const [status, setStatus] = useState<"idle" | "running" | "done" | "error">("idle");
  const [message, setMessage] = useState("");

  const handle = async () => {
    setStatus("running");
    setMessage("");
    try {
      const res = await onRun();
      setStatus("done");
      const data = (res as { data: unknown }).data;
      setMessage(JSON.stringify(data, null, 2));
    } catch (e: unknown) {
      setStatus("error");
      const err = e as { message?: string };
      setMessage(err.message ?? "Unknown error");
    }
  };

  return (
    <div className="card flex flex-col gap-3">
      <div>
        <div className="text-sm font-medium">{label}</div>
        <div className="text-xs mt-0.5" style={{ color: "var(--text-secondary)" }}>
          {description}
        </div>
      </div>
      <div className="flex items-center gap-3">
        <button
          onClick={handle}
          disabled={status === "running"}
          className="text-xs px-3 py-1.5 rounded font-medium transition-opacity"
          style={{
            background: "var(--accent)",
            color: "#fff",
            opacity: status === "running" ? 0.5 : 1,
          }}
        >
          {status === "running" ? "Running…" : "Run"}
        </button>
        {status === "done" && (
          <span className="text-xs" style={{ color: "var(--green)" }}>Done</span>
        )}
        {status === "error" && (
          <span className="text-xs" style={{ color: "var(--red)" }}>Error</span>
        )}
      </div>
      {message && (
        <pre
          className="text-xs rounded p-2 overflow-x-auto"
          style={{
            background: "var(--bg-elevated)",
            color: status === "error" ? "var(--red)" : "var(--text-secondary)",
            maxHeight: 140,
          }}
        >
          {message}
        </pre>
      )}
    </div>
  );
}

// ─── CSV Import Button ────────────────────────────────────────────────────────

function CsvImportButton({ onDone }: { onDone?: () => void }) {
  const fileRef = useRef<HTMLInputElement>(null);
  const [status, setStatus] = useState<"idle" | "running" | "done" | "error">("idle");
  const [message, setMessage] = useState("");

  const handleFile = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    e.target.value = "";
    setStatus("running");
    setMessage("");
    try {
      const res = await importCsvFile(file);
      setStatus("done");
      const data = res.data as { total_rows_inserted: number; rows_by_symbol: Record<string, number> };
      const detail = Object.entries(data.rows_by_symbol)
        .map(([sym, n]) => `${sym}: ${n} rows`)
        .join(", ");
      setMessage(`${data.total_rows_inserted} rows inserted — ${detail}`);
      onDone?.();
    } catch (e: unknown) {
      setStatus("error");
      const err = e as { message?: string };
      setMessage(err.message ?? "Upload failed");
    }
  };

  return (
    <div className="card flex flex-col gap-3">
      <div>
        <div className="text-sm font-medium">Import Barchart CSV</div>
        <div className="text-xs mt-0.5" style={{ color: "var(--text-secondary)" }}>
          Upload a single Barchart CSV file. Indicators are recalculated automatically after import.
        </div>
      </div>
      <div className="flex items-center gap-3">
        <input
          type="file"
          accept=".csv"
          ref={fileRef}
          style={{ display: "none" }}
          onChange={handleFile}
        />
        <button
          onClick={() => fileRef.current?.click()}
          disabled={status === "running"}
          className="text-xs px-3 py-1.5 rounded font-medium flex items-center gap-1.5 transition-opacity"
          style={{
            background: "var(--accent)",
            color: "#fff",
            opacity: status === "running" ? 0.5 : 1,
          }}
        >
          {status === "running"
            ? <><Loader2 size={11} className="animate-spin" /> Uploading…</>
            : <><Upload size={11} /> Choose File</>
          }
        </button>
        {status === "done" && (
          <span className="text-xs" style={{ color: "var(--green)" }}>Done</span>
        )}
        {status === "error" && (
          <span className="text-xs" style={{ color: "var(--red)" }}>Error</span>
        )}
      </div>
      {message && (
        <p
          className="text-xs rounded px-2 py-1.5"
          style={{
            background: "var(--bg-elevated)",
            color: status === "error" ? "var(--red)" : "var(--text-secondary)",
          }}
        >
          {message}
        </p>
      )}
    </div>
  );
}

// ─── Reset Backtests Button ───────────────────────────────────────────────────

function ResetBacktestsButton() {
  const [status, setStatus] = useState<"idle" | "running" | "done" | "error">("idle");
  const [message, setMessage] = useState("");

  const handle = async () => {
    const ok = window.confirm(
      "This will delete all backtest history, trades, open positions, and portfolio snapshots.\n\n" +
      "Price data and tickers are kept.\n\nAre you sure?"
    );
    if (!ok) return;
    setStatus("running");
    setMessage("");
    try {
      const res = await resetBacktests();
      setStatus("done");
      setMessage(JSON.stringify((res as { data: unknown }).data, null, 2));
    } catch (e: unknown) {
      setStatus("error");
      setMessage((e as { message?: string }).message ?? "Unknown error");
    }
  };

  return (
    <div className="card flex flex-col gap-3">
      <div>
        <div className="text-sm font-medium">Reset Backtests</div>
        <div className="text-xs mt-0.5" style={{ color: "var(--text-secondary)" }}>
          Delete all backtest history, trades, positions, and snapshots. Price data and tickers are preserved.
        </div>
      </div>
      <div className="flex items-center gap-3">
        <button
          onClick={handle}
          disabled={status === "running"}
          className="text-xs px-3 py-1.5 rounded font-medium transition-opacity"
          style={{
            background: "var(--yellow)",
            color: "#000",
            opacity: status === "running" ? 0.5 : 1,
          }}
        >
          {status === "running" ? "Resetting…" : "Reset Backtests"}
        </button>
        {status === "done" && <span className="text-xs" style={{ color: "var(--green)" }}>Done</span>}
        {status === "error" && <span className="text-xs" style={{ color: "var(--red)" }}>Error</span>}
      </div>
      {message && (
        <pre
          className="text-xs rounded p-2 overflow-x-auto"
          style={{
            background: "var(--bg-elevated)",
            color: status === "error" ? "var(--red)" : "var(--text-secondary)",
            maxHeight: 140,
          }}
        >
          {message}
        </pre>
      )}
    </div>
  );
}

// ─── Hard Reset Button ────────────────────────────────────────────────────────

function HardResetButton() {
  const [phase, setPhase] = useState<"idle" | "confirming" | "running" | "done" | "error">("idle");
  const [confirmText, setConfirmText] = useState("");
  const [message, setMessage] = useState("");

  const canSubmit = confirmText === "CONFIRM" && phase === "confirming";

  const doReset = async () => {
    setPhase("running");
    try {
      const res = await hardReset("CONFIRM");
      setPhase("done");
      setMessage(JSON.stringify((res as { data: unknown }).data, null, 2));
    } catch (e: unknown) {
      setPhase("error");
      setMessage((e as { message?: string }).message ?? "Unknown error");
    }
  };

  return (
    <div
      className="card flex flex-col gap-3"
      style={{ borderColor: "rgba(239,68,68,0.4)", borderWidth: 1, borderStyle: "solid" }}
    >
      <div>
        <div className="text-sm font-medium" style={{ color: "var(--red)" }}>Hard Reset</div>
        <div className="text-xs mt-0.5" style={{ color: "var(--text-secondary)" }}>
          WARNING: Deletes ALL data including price history and tickers. Requires full re-import afterward.
        </div>
      </div>

      {phase === "idle" && (
        <button
          onClick={() => setPhase("confirming")}
          className="text-xs px-3 py-1.5 rounded font-medium self-start"
          style={{ background: "var(--red)", color: "#fff" }}
        >
          Hard Reset…
        </button>
      )}

      {phase === "confirming" && (
        <div className="flex flex-col gap-2">
          <p className="text-xs" style={{ color: "var(--red)" }}>
            Type <strong>CONFIRM</strong> to proceed. This cannot be undone.
          </p>
          <input
            type="text"
            value={confirmText}
            onChange={(e) => setConfirmText(e.target.value)}
            placeholder="Type CONFIRM"
            autoFocus
            className="text-xs px-2 py-1.5 rounded font-mono"
            style={{
              background: "var(--bg-elevated)",
              border: "1px solid var(--border)",
              color: "var(--text-primary)",
              width: 160,
              outline: "none",
            }}
          />
          <div className="flex gap-2">
            <button
              onClick={doReset}
              disabled={!canSubmit}
              className="text-xs px-3 py-1.5 rounded font-medium transition-opacity"
              style={{ background: "var(--red)", color: "#fff", opacity: canSubmit ? 1 : 0.35 }}
            >
              Confirm Delete All
            </button>
            <button
              onClick={() => { setPhase("idle"); setConfirmText(""); }}
              className="text-xs px-3 py-1.5 rounded font-medium"
              style={{
                background: "var(--bg-elevated)",
                border: "1px solid var(--border)",
                color: "var(--text-secondary)",
              }}
            >
              Cancel
            </button>
          </div>
        </div>
      )}

      {phase === "running" && (
        <span className="text-xs" style={{ color: "var(--text-muted)" }}>Deleting all data…</span>
      )}
      {phase === "done" && (
        <span className="text-xs" style={{ color: "var(--green)" }}>Done — all data deleted</span>
      )}
      {phase === "error" && (
        <span className="text-xs" style={{ color: "var(--red)" }}>Error</span>
      )}

      {message && (
        <pre
          className="text-xs rounded p-2 overflow-x-auto"
          style={{
            background: "var(--bg-elevated)",
            color: phase === "error" ? "var(--red)" : "var(--text-secondary)",
            maxHeight: 140,
          }}
        >
          {message}
        </pre>
      )}
    </div>
  );
}

// ─── Database Status Table ────────────────────────────────────────────────────

function StatusTable({
  tickers,
  loading,
  onRefresh,
}: {
  tickers: TickerStatus[];
  loading: boolean;
  onRefresh: () => void;
}) {
  const total = tickers.reduce((s, t) => s + t.row_count, 0);
  const active = tickers.filter(t => t.is_active).length;

  return (
    <div className="card">
      <div className="flex items-center justify-between mb-4">
        <div>
          <div className="text-xs font-semibold uppercase tracking-wider" style={{ color: "var(--text-muted)" }}>
            Database Status
          </div>
          {tickers.length > 0 && !loading && (
            <div className="text-xs mt-0.5" style={{ color: "var(--text-muted)" }}>
              {active} active tickers · {total.toLocaleString()} total rows
            </div>
          )}
        </div>
        <button
          onClick={onRefresh}
          disabled={loading}
          className="flex items-center gap-1.5 text-xs px-2.5 py-1.5 rounded transition-opacity"
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

      {loading && tickers.length === 0 && (
        <div className="flex items-center justify-center py-8">
          <Loader2 size={20} className="animate-spin" style={{ color: "var(--text-muted)" }} />
        </div>
      )}

      {tickers.length > 0 && (
        <div style={{ overflowX: "auto" }}>
          <table className="w-full text-sm" style={{ borderCollapse: "collapse" }}>
            <thead>
              <tr style={{ borderBottom: "1px solid var(--border)" }}>
                {["Symbol", "Sector", "Rows", "Earliest", "Latest", "Status"].map(h => (
                  <th
                    key={h}
                    className="text-left py-2 pr-6 text-xs font-medium uppercase tracking-wide"
                    style={{ color: "var(--text-muted)", whiteSpace: "nowrap" }}
                  >
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {tickers.map(t => {
                const isStale = t.last_date
                  ? (new Date().getTime() - new Date(t.last_date).getTime()) / 86400000 > 5
                  : true;

                return (
                  <tr
                    key={t.symbol}
                    style={{ borderBottom: "1px solid var(--border-dim)" }}
                  >
                    <td className="py-2 pr-6 font-mono font-semibold" style={{ color: "var(--text-primary)" }}>
                      {t.symbol}
                    </td>
                    <td className="py-2 pr-6 text-xs" style={{ color: "var(--text-secondary)" }}>
                      {t.sector || "—"}
                    </td>
                    <td className="py-2 pr-6 font-mono text-xs" style={{ color: "var(--text-primary)" }}>
                      {t.row_count.toLocaleString()}
                    </td>
                    <td className="py-2 pr-6 font-mono text-xs" style={{ color: "var(--text-muted)" }}>
                      {t.earliest_date ?? "—"}
                    </td>
                    <td
                      className="py-2 pr-6 font-mono text-xs"
                      style={{ color: isStale ? "var(--yellow)" : "var(--text-secondary)" }}
                    >
                      {t.last_date ?? "—"}
                    </td>
                    <td className="py-2 pr-6">
                      <span
                        className="text-xs px-2 py-0.5 rounded"
                        style={{
                          background: t.is_active
                            ? "rgba(16,185,129,0.12)"
                            : "rgba(100,100,100,0.12)",
                          color: t.is_active ? "var(--green)" : "var(--text-muted)",
                        }}
                      >
                        {t.is_active ? "Active" : "Inactive"}
                      </span>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

// ─── Main Page ────────────────────────────────────────────────────────────────

export default function DataPage() {
  const [tickers, setTickers] = useState<TickerStatus[]>([]);
  const [loadingStatus, setLoadingStatus] = useState(false);
  const [statusError, setStatusError] = useState(false);

  const fetchStatus = async () => {
    setLoadingStatus(true);
    setStatusError(false);
    try {
      const res = await getDataStatus();
      setTickers(res.data ?? []);
    } catch {
      setStatusError(true);
    } finally {
      setLoadingStatus(false);
    }
  };

  useEffect(() => { fetchStatus(); }, []);

  return (
    <div style={{ maxWidth: 780 }}>
      <div className="mb-6">
        <h1 className="text-xl font-semibold">Data Management</h1>
        <p className="text-sm mt-1" style={{ color: "var(--text-secondary)" }}>
          Update prices, earnings dates, and import CSV files
        </p>
      </div>

      <div className="flex flex-col gap-4 mb-6">
        <ActionButton
          label="Update All Prices (yfinance)"
          description="Pull latest OHLCV data for all active tickers and recalculate indicators."
          onRun={updatePrices}
        />
        <ActionButton
          label="Update Earnings Dates"
          description="Fetch upcoming earnings dates from yfinance for all active tickers."
          onRun={updateEarnings}
        />
        <CsvImportButton onDone={fetchStatus} />
        <ActionButton
          label="Import CSV Folder"
          description="Scan the dataInput folder and import all Barchart CSV files found."
          onRun={importFolder}
        />
      </div>

      <div className="mb-2">
        <div className="text-xs font-semibold uppercase tracking-wider mb-3" style={{ color: "var(--text-muted)" }}>
          Database Reset
        </div>
        <div className="flex flex-col gap-4">
          <ResetBacktestsButton />
          <HardResetButton />
        </div>
      </div>

      {statusError ? (
        <BackendBusy onRetry={fetchStatus} />
      ) : (
        <StatusTable
          tickers={tickers}
          loading={loadingStatus}
          onRefresh={fetchStatus}
        />
      )}
    </div>
  );
}
