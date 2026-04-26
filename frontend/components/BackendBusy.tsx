"use client";

import { Clock, RefreshCw } from "lucide-react";

/**
 * Shown when an initial page-load API call times out or fails.
 * Replaces the loading spinner so the user isn't left waiting forever.
 */
export function BackendBusy({ onRetry }: { onRetry: () => void }) {
  return (
    <div
      className="card flex flex-col items-center justify-center gap-4 text-center"
      style={{ minHeight: 220, padding: "3rem 2rem" }}
    >
      <Clock size={26} style={{ color: "var(--text-muted)" }} />

      <div>
        <p className="text-sm font-medium" style={{ color: "var(--text-primary)" }}>
          Backend busy or unavailable
        </p>
        <p className="text-xs mt-2 leading-relaxed" style={{ color: "var(--text-secondary)", maxWidth: 340 }}>
          The backend may be running a backtest in the background.
          It will be available again when the backtest finishes.
        </p>
      </div>

      <button
        onClick={onRetry}
        className="flex items-center gap-2 text-xs px-4 py-2 rounded font-medium"
        style={{
          background: "var(--bg-elevated)",
          border: "1px solid var(--border)",
          color: "var(--text-secondary)",
        }}
      >
        <RefreshCw size={12} />
        Retry
      </button>
    </div>
  );
}
