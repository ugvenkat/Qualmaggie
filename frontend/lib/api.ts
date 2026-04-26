import axios from "axios";

// Base instance — long timeout for action endpoints (scan, import, backtest run).
const api = axios.create({
  baseURL: process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000",
  headers: { "Content-Type": "application/json" },
  timeout: 300000,
});

export default api;

// Shorthand so per-call GET overrides are tidy.
const GET10 = { timeout: 10000 };

// --- Market ---
export const getMarketStatus = () => api.get("/api/market/status", GET10);

// --- Scanner ---
// Scan can take up to 2 minutes on a large universe — keep long timeout.
export const runScan = () => api.post("/api/scan/run");

// --- Positions ---
export const getOpenPositions  = () => api.get("/api/positions",         GET10);
export const getPositionsHistory = () => api.get("/api/positions/history", GET10);
export const getTrades         = () => api.get("/api/trades",             GET10);

// --- Data ---
// Action POSTs keep the long default timeout; the status GET is fast.
export const updatePrices  = () => api.post("/api/data/update");
export const updateEarnings = () => api.post("/api/data/update-earnings");
export const importFolder  = () => api.post("/api/data/import-folder");
export const importCsvFile = (file: File) => {
  const form = new FormData();
  form.append("file", file);
  return api.post("/api/data/import-csv", form, {
    headers: { "Content-Type": "multipart/form-data" },
  });
};
export const getDataStatus = () => api.get("/api/data/status", GET10);

// --- Performance ---
export const getPerformance = () => api.get("/api/performance",           GET10);
export const getSnapshots   = () => api.get("/api/performance/snapshots", GET10);

// --- Settings ---
export const getSettings    = () => api.get("/api/settings",  GET10);
export const saveSettings   = (payload: Record<string, unknown>) =>
  api.post("/api/settings", payload);

// --- Backtest ---
// runBacktest can take 30+ minutes — use the full 300 s default.
export const runBacktest = (payload: {
  run_name: string;
  start_date: string;
  end_date: string;
}) => api.post("/api/backtest/run", payload);

export const listBacktests       = () => api.get("/api/backtest",                 GET10);
export const getBacktest         = (id: number) => api.get(`/api/backtest/${id}`, GET10);
export const getBacktestTrades   = (id: number) => api.get(`/api/backtest/${id}/trades`,    GET10);
export const getBacktestSnapshots = (id: number) => api.get(`/api/backtest/${id}/snapshots`, GET10);
