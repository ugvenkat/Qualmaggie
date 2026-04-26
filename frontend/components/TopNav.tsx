"use client";

import { useEffect, useState } from "react";
import { Activity, RefreshCw } from "lucide-react";
import { getMarketStatus } from "@/lib/api";

interface MarketStatus {
  is_healthy: boolean;
  close_price?: number;
  sma50?: number;
  ema10?: number;
  ema20?: number;
}

export function TopNav() {
  const [market, setMarket] = useState<MarketStatus | null>(null);
  const [loading, setLoading] = useState(false);

  const fetchMarket = async () => {
    setLoading(true);
    try {
      const res = await getMarketStatus();
      setMarket(res.data);
    } catch {
      // silently fail — backend may not be running
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { fetchMarket(); }, []);

  const healthy = market?.is_healthy;

  return (
    <header
      className="flex items-center justify-between px-5 h-12 shrink-0 z-10"
      style={{
        background: "var(--bg-surface)",
        borderBottom: "1px solid var(--border)",
      }}
    >
      {/* Brand */}
      <div className="flex items-center gap-2.5">
        <Activity size={18} style={{ color: "var(--accent)" }} strokeWidth={2.2} />
        <span className="text-sm font-semibold tracking-wide" style={{ color: "var(--text-primary)" }}>
          QUALMAGGIE
        </span>
      </div>

      {/* Market status + refresh */}
      <div className="flex items-center gap-4">
        {market !== null && (
          <div className="flex items-center gap-2">
            <span
              className="text-xs font-medium px-2 py-0.5 rounded"
              style={{
                background: healthy ? "rgba(16,185,129,0.12)" : "rgba(239,68,68,0.12)",
                color: healthy ? "var(--green)" : "var(--red)",
                border: `1px solid ${healthy ? "rgba(16,185,129,0.25)" : "rgba(239,68,68,0.25)"}`,
              }}
            >
              SPY {healthy ? "HEALTHY" : "WEAK"}
            </span>
            {market.close_price && (
              <span className="text-xs" style={{ color: "var(--text-secondary)" }}>
                ${market.close_price.toFixed(2)}
              </span>
            )}
          </div>
        )}

        <button
          onClick={fetchMarket}
          disabled={loading}
          className="flex items-center gap-1.5 text-xs px-2.5 py-1 rounded transition-opacity"
          style={{
            color: "var(--text-secondary)",
            border: "1px solid var(--border)",
            background: "var(--bg-elevated)",
            opacity: loading ? 0.5 : 1,
          }}
        >
          <RefreshCw size={12} className={loading ? "animate-spin" : ""} />
          Refresh
        </button>
      </div>
    </header>
  );
}
