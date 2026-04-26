"use client";

import { useEffect, useState } from "react";
import { Activity, Briefcase, TrendingUp, TrendingDown } from "lucide-react";
import { getMarketStatus, getOpenPositions, getTrades } from "@/lib/api";
import { BackendBusy } from "@/components/BackendBusy";

function StatCard({
  label, value, sub, positive, icon,
}: {
  label: string; value: string; sub?: string; positive?: boolean | null; icon: React.ReactNode;
}) {
  const color =
    positive === true ? "var(--green)" :
    positive === false ? "var(--red)" :
    "var(--text-primary)";
  return (
    <div className="card flex flex-col gap-3">
      <div className="flex items-center justify-between">
        <span className="text-xs font-medium uppercase tracking-wider" style={{ color: "var(--text-muted)" }}>{label}</span>
        <span style={{ color: "var(--text-muted)" }}>{icon}</span>
      </div>
      <div>
        <div className="text-2xl font-semibold" style={{ color }}>{value}</div>
        {sub && <div className="text-xs mt-1" style={{ color: "var(--text-secondary)" }}>{sub}</div>}
      </div>
    </div>
  );
}

export default function DashboardPage() {
  const [market, setMarket] = useState<Record<string, unknown> | null>(null);
  const [positions, setPositions] = useState<Array<Record<string, unknown>>>([]);
  const [trades, setTrades] = useState<Array<Record<string, unknown>>>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);

  const fetchAll = async () => {
    setLoading(true);
    setError(false);
    try {
      const [marketRes, posRes, tradesRes] = await Promise.all([
        getMarketStatus(),
        getOpenPositions(),
        getTrades(),
      ]);
      setMarket(marketRes.data);
      setPositions(posRes.data ?? []);
      setTrades(tradesRes.data ?? []);
    } catch {
      setError(true);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { fetchAll(); }, []);

  const healthy = market?.is_healthy as boolean | undefined;
  const winners = trades.filter(t => (t.pnl as number) > 0);
  const winRate = trades.length > 0 ? `${((winners.length / trades.length) * 100).toFixed(0)}%` : "—";

  return (
    <div style={{ maxWidth: 1100 }}>
      <div className="mb-6">
        <h1 className="text-xl font-semibold">Dashboard</h1>
        <p className="text-sm mt-1" style={{ color: "var(--text-secondary)" }}>
          Live market overview and portfolio summary
        </p>
      </div>

      {loading && (
        <p className="text-sm" style={{ color: "var(--text-muted)" }}>Loading…</p>
      )}

      {!loading && error && (
        <BackendBusy onRetry={fetchAll} />
      )}

      {!loading && !error && (
        <>
          <div className="grid grid-cols-4 gap-4 mb-6">
            <StatCard label="Market" value={market ? (healthy ? "Healthy" : "Weak") : "—"}
              sub="SPY regime" positive={healthy ?? null} icon={<Activity size={15} />} />
            <StatCard label="Open Positions" value={String(positions.length)}
              sub="Active trades" positive={null} icon={<Briefcase size={15} />} />
            <StatCard label="Win Rate" value={winRate}
              sub={`${winners.length} / ${trades.length} trades`}
              positive={winners.length >= trades.length / 2} icon={<TrendingUp size={15} />} />
            <StatCard label="Closed Trades" value={String(trades.length)}
              sub="All-time live" positive={null} icon={<TrendingDown size={15} />} />
          </div>

          <div className="card">
            <h2 className="text-sm font-semibold mb-4">Open Positions</h2>
            {positions.length === 0 ? (
              <p className="text-sm" style={{ color: "var(--text-muted)" }}>No open positions.</p>
            ) : (
              <table className="w-full text-sm">
                <thead>
                  <tr style={{ color: "var(--text-muted)", borderBottom: "1px solid var(--border)" }}>
                    {["Symbol", "Sector", "Entry Date", "Entry $", "Stop $", "Shares"].map(h => (
                      <th key={h} className="text-left pb-2 pr-4 font-medium text-xs uppercase tracking-wide">{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {positions.map((p, i) => (
                    <tr key={i} style={{ borderBottom: "1px solid var(--border-dim)" }}>
                      <td className="py-2 pr-4 font-medium">{p.symbol as string}</td>
                      <td className="py-2 pr-4" style={{ color: "var(--text-secondary)" }}>{p.sector as string}</td>
                      <td className="py-2 pr-4" style={{ color: "var(--text-secondary)" }}>{p.entry_date as string}</td>
                      <td className="py-2 pr-4">${Number(p.entry_price).toFixed(2)}</td>
                      <td className="py-2 pr-4" style={{ color: "var(--red)" }}>${Number(p.current_stop).toFixed(2)}</td>
                      <td className="py-2">{p.shares as number}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </>
      )}
    </div>
  );
}
