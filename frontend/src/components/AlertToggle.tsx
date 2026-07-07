import { useState } from "react";
import { Bell, BellRing } from "lucide-react";

const STORAGE_KEY = "spread_alert_config";

interface AlertConfig {
  enabled: boolean;
  threshold_bps: number;
  symbols: string[];
}

function loadConfig(): AlertConfig {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return { enabled: false, threshold_bps: 20, symbols: [] };
    return { enabled: false, threshold_bps: 20, symbols: [], ...JSON.parse(raw) };
  } catch {
    return { enabled: false, threshold_bps: 20, symbols: [] };
  }
}

export function AlertToggle() {
  const [config, setConfig] = useState<AlertConfig>(loadConfig);
  const [showPanel, setShowPanel] = useState(false);

  const toggle = () => {
    const next = { ...config, enabled: !config.enabled };
    setConfig(next);
    localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
  };

  const Icon = config.enabled ? BellRing : Bell;

  return (
    <div className="relative">
      <button
        type="button"
        className={`flex h-7 w-7 items-center justify-center rounded-md transition ${
          config.enabled
            ? "text-amber-500 hover:bg-amber-500/10"
            : "text-ink-muted hover:bg-accent/10"
        }`}
        title={config.enabled ? "Алерты включены" : "Алерты выключены"}
        onClick={() => setShowPanel(!showPanel)}
      >
        <Icon className="h-4 w-4" />
      </button>
      {showPanel && (
        <div className="absolute right-0 top-8 z-50 w-64 rounded-lg border border-line bg-surface p-3 shadow-lg">
          <div className="mb-2 flex items-center justify-between">
            <span className="text-xs font-semibold text-ink">Звуковые алерты</span>
            <button
              type="button"
              className="text-xs text-ink-muted hover:text-ink"
              onClick={() => setShowPanel(false)}
            >
              ✕
            </button>
          </div>
          <label className="mb-2 flex items-center justify-between text-xs text-ink-muted">
            <span>Включить</span>
            <input
              type="checkbox"
              checked={config.enabled}
              onChange={toggle}
              className="h-4 w-4 rounded border-line text-amber-500"
            />
          </label>
          <label className="mb-1 block text-xs text-ink-muted">
            Порог спреда (bps)
          </label>
          <input
            type="number"
            step="1"
            value={config.threshold_bps}
            onChange={(e) => {
              const next = { ...config, threshold_bps: parseFloat(e.target.value) || 20 };
              setConfig(next);
              localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
            }}
            className="mb-2 w-full rounded-md border border-line bg-bg px-2 py-1 text-sm text-ink"
          />
          <label className="mb-1 block text-xs text-ink-muted">
            Символы (через запятую)
          </label>
          <input
            type="text"
            value={config.symbols.join(", ")}
            onChange={(e) => {
              const syms = e.target.value
                .split(",")
                .map((s) => s.trim().toUpperCase())
                .filter(Boolean);
              const next = { ...config, symbols: syms };
              setConfig(next);
              localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
            }}
            placeholder="BTCUSDT, ETHUSDT"
            className="w-full rounded-md border border-line bg-bg px-2 py-1 text-sm text-ink"
          />
        </div>
      )}
    </div>
  );
}
