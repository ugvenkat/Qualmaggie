"use client";

import { useEffect, useState } from "react";
import { Loader2, Save } from "lucide-react";
import { getSettings, saveSettings } from "@/lib/api";
import { BackendBusy } from "@/components/BackendBusy";

// ─── Types ────────────────────────────────────────────────────────────────────

type SettingsMap = Record<string, unknown>;

type FieldDef = {
  key: string;
  label: string;
  type: "number" | "boolean" | "string";
  unit?: string;
  hint?: string;
};

type GroupDef = {
  title: string;
  fields: FieldDef[];
};

// ─── Field definitions ────────────────────────────────────────────────────────

const GROUPS: GroupDef[] = [
  {
    title: "Portfolio",
    fields: [
      { key: "PortfolioSize", label: "Portfolio Size", type: "number", unit: "$" },
      { key: "RiskPerTrade", label: "Risk Per Trade", type: "number", unit: "%" },
      { key: "MaxOpenPositions", label: "Max Open Positions", type: "number" },
      { key: "MaxCapitalDeployedPct", label: "Max Capital Deployed", type: "number", unit: "%" },
    ],
  },
  {
    title: "Risk Management",
    fields: [
      { key: "StopLossADRMultiplier", label: "Stop Loss ADR Multiplier", type: "number", hint: "e.g. 1.5 = 1.5× ADR" },
      { key: "PartialSellDays", label: "Partial Sell Days", type: "number", hint: "Sell 50% at day N" },
      { key: "PartialSellMinProfitPct", label: "Partial Sell Min Profit", type: "number", unit: "%" },
      { key: "MaxHoldDays", label: "Max Hold Days (base)", type: "number" },
      { key: "MaxHoldExtendedDays", label: "Max Hold Days (extended)", type: "number" },
      { key: "MaxHoldExtendPct", label: "Extend Threshold", type: "number", unit: "%", hint: "Gain % to trigger extended hold" },
      { key: "MaxHoldBypassPct", label: "Max Hold Bypass", type: "number", unit: "%", hint: "Gain % to bypass MaxHold entirely" },
    ],
  },
  {
    title: "Trail & Exit",
    fields: [
      { key: "TrailActivationPct", label: "Trail Activation", type: "number", unit: "%", hint: "Gain % to activate EMA trail" },
      { key: "TrailEMAPeriod", label: "Trail EMA Period", type: "number" },
      { key: "WideTrailActivationPct", label: "Wide Trail Activation", type: "number", unit: "%", hint: "Gain % to switch to wide trail" },
      { key: "WideTrailEMAPeriod", label: "Wide Trail EMA Period", type: "number" },
    ],
  },
  {
    title: "Stock Universe Filters",
    fields: [
      { key: "MinStockPrice", label: "Min Stock Price", type: "number", unit: "$" },
      { key: "MinAvgVolume", label: "Min Avg Volume", type: "number" },
      { key: "MinATRPct", label: "Min ATR", type: "number", unit: "%" },
      { key: "MaxATRPct", label: "Max ATR", type: "number", unit: "%" },
      { key: "MinInstitutionalOwnershipPct", label: "Min Institutional Ownership", type: "number", unit: "%" },
      { key: "MaxPositionsPerSector", label: "Max Positions Per Sector", type: "number" },
    ],
  },
  {
    title: "Warnings & Blacklist",
    fields: [
      { key: "EarningsHardBlock", label: "Earnings Hard Block", type: "boolean", hint: "Block entry near earnings" },
      { key: "EarningsWarningDays", label: "Earnings Warning Days", type: "number" },
      { key: "MacroEventWarningDays", label: "Macro Event Warning Days", type: "number" },
      { key: "BlacklistDays", label: "Blacklist Duration", type: "number", unit: "days" },
    ],
  },
  {
    title: "Market & Relative Strength",
    fields: [
      { key: "MarketFilterTicker", label: "Market Filter Ticker", type: "string", hint: "e.g. SPY" },
      { key: "RSLookbackDays", label: "RS Lookback", type: "number", unit: "days" },
      { key: "BreakoutVolumeFactor", label: "Breakout Volume Factor", type: "number", hint: "e.g. 1.5 = 1.5× avg volume" },
    ],
  },
  {
    title: "VCP Pattern",
    fields: [
      { key: "VCPMinContractions", label: "Min Contractions", type: "number" },
      { key: "VCPPreferredContractions", label: "Preferred Contractions", type: "number" },
      { key: "VCPTightnessFactorPct", label: "Tightness Factor", type: "number", unit: "%" },
      { key: "VCPMaxDepthPct", label: "Max Contraction Depth", type: "number", unit: "%" },
      { key: "VCPMinDaysInBase", label: "Min Days In Base", type: "number" },
      { key: "VCPMaxDaysInBase", label: "Max Days In Base", type: "number" },
    ],
  },
];

// ─── Sub-components ───────────────────────────────────────────────────────────

function FieldInput({
  def,
  value,
  onChange,
}: {
  def: FieldDef;
  value: unknown;
  onChange: (key: string, v: unknown) => void;
}) {
  if (def.type === "boolean") {
    return (
      <label className="flex items-center gap-2 cursor-pointer">
        <input
          type="checkbox"
          checked={Boolean(value)}
          onChange={e => onChange(def.key, e.target.checked)}
          className="w-4 h-4 rounded"
          style={{ accentColor: "var(--green)" }}
        />
        <span className="text-sm" style={{ color: "var(--text-secondary)" }}>
          {value ? "Enabled" : "Disabled"}
        </span>
      </label>
    );
  }

  return (
    <div className="flex items-center gap-2">
      <input
        type={def.type === "number" ? "number" : "text"}
        value={String(value ?? "")}
        step={def.type === "number" ? "any" : undefined}
        onChange={e => {
          if (def.type === "number") {
            const n = parseFloat(e.target.value);
            onChange(def.key, isNaN(n) ? value : n);
          } else {
            onChange(def.key, e.target.value);
          }
        }}
        className="text-sm px-3 py-1.5 rounded outline-none"
        style={{
          background: "var(--bg-elevated)",
          border: "1px solid var(--border)",
          color: "var(--text-primary)",
          width: 160,
        }}
      />
      {def.unit && (
        <span className="text-xs" style={{ color: "var(--text-muted)" }}>{def.unit}</span>
      )}
    </div>
  );
}

function SettingsGroup({
  group,
  values,
  onChange,
}: {
  group: GroupDef;
  values: SettingsMap;
  onChange: (key: string, v: unknown) => void;
}) {
  return (
    <div className="card">
      <div className="text-xs font-semibold uppercase tracking-wider mb-4" style={{ color: "var(--text-muted)" }}>
        {group.title}
      </div>
      <div className="flex flex-col gap-3">
        {group.fields.map(def => (
          <div key={def.key} className="flex items-center justify-between gap-4">
            <div className="flex flex-col gap-0.5" style={{ minWidth: 0 }}>
              <span className="text-sm" style={{ color: "var(--text-primary)" }}>{def.label}</span>
              {def.hint && (
                <span className="text-xs" style={{ color: "var(--text-muted)" }}>{def.hint}</span>
              )}
            </div>
            <FieldInput def={def} value={values[def.key] ?? ""} onChange={onChange} />
          </div>
        ))}
      </div>
    </div>
  );
}

// ─── Main Page ────────────────────────────────────────────────────────────────

export default function SettingsPage() {
  const [values, setValues] = useState<SettingsMap>({});
  const [loading, setLoading] = useState(true);
  const [loadFailed, setLoadFailed] = useState(false);
  const [saving, setSaving] = useState(false);
  const [savedMsg, setSavedMsg] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const loadSettings = () => {
    setLoading(true);
    setLoadFailed(false);
    getSettings()
      .then(r => setValues(r.data as SettingsMap))
      .catch(() => setLoadFailed(true))
      .finally(() => setLoading(false));
  };

  useEffect(() => { loadSettings(); }, []);

  const handleChange = (key: string, v: unknown) => {
    setValues(prev => ({ ...prev, [key]: v }));
    setSavedMsg(null);
  };

  const handleSave = async () => {
    setSaving(true);
    setError(null);
    setSavedMsg(null);
    try {
      await saveSettings(values);
      setSavedMsg("Saved. Restart the backend to apply changes to running processes.");
    } catch {
      setError("Failed to save settings");
    } finally {
      setSaving(false);
    }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center" style={{ height: 200 }}>
        <Loader2 size={20} className="animate-spin" style={{ color: "var(--text-muted)" }} />
      </div>
    );
  }

  if (loadFailed) {
    return (
      <div style={{ maxWidth: 700 }}>
        <div className="mb-6">
          <h1 className="text-xl font-semibold">Settings</h1>
        </div>
        <BackendBusy onRetry={loadSettings} />
      </div>
    );
  }

  const SaveButton = ({ bottom }: { bottom?: boolean }) => (
    <button
      onClick={handleSave}
      disabled={saving}
      className="flex items-center gap-2 px-4 py-2 rounded font-semibold text-sm shrink-0 transition-opacity"
      style={{
        background: saving ? "var(--bg-elevated)" : "var(--green)",
        color: saving ? "var(--text-muted)" : "#fff",
        border: saving ? "1px solid var(--border)" : "none",
        opacity: saving ? 0.7 : 1,
        marginTop: bottom ? "1.5rem" : undefined,
        alignSelf: "flex-end",
      }}
    >
      {saving ? <Loader2 size={14} className="animate-spin" /> : <Save size={14} />}
      {saving ? "Saving…" : bottom ? "Save Settings" : "Save"}
    </button>
  );

  return (
    <div style={{ maxWidth: 680 }}>
      <div className="mb-6 flex items-start justify-between gap-4">
        <div>
          <h1 className="text-xl font-semibold">Settings</h1>
          <p className="text-sm mt-1" style={{ color: "var(--text-secondary)" }}>
            Trading parameters — written to settings.json on save
          </p>
        </div>
        <SaveButton />
      </div>

      {error && (
        <p className="text-sm mb-4" style={{ color: "var(--red)" }}>{error}</p>
      )}
      {savedMsg && (
        <p
          className="text-sm mb-4 px-3 py-2 rounded"
          style={{
            background: "rgba(16,185,129,0.08)",
            color: "var(--green)",
            border: "1px solid rgba(16,185,129,0.20)",
          }}
        >
          {savedMsg}
        </p>
      )}

      <div className="flex flex-col gap-5">
        {GROUPS.map(group => (
          <SettingsGroup key={group.title} group={group} values={values} onChange={handleChange} />
        ))}
      </div>

      <div className="mt-6 flex justify-end">
        <SaveButton bottom />
      </div>
    </div>
  );
}
