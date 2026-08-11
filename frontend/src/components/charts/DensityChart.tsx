import { useCallback, useEffect, useMemo, useState } from "react";
import { BarChart3, RefreshCw } from "lucide-react";
import { apiUrl } from "../../config";

interface WallData {
  side: string;
  price: number;
  qty: number;
  notional_usdt: number;
  ratio_to_median: number;
}

interface DensityData {
  ok: boolean;
  walls?: WallData[];
  count?: number;
  error?: string;
}

export interface DensityChartProps {
  symbol: string;
  market?: string;
  className?: string;
}

function fmt(n: number, digits = 2): string {
  if (!Number.isFinite(n)) return "—";
  return n.toLocaleString("ru-RU", { maximumFractionDigits: digits });
}

function fmtNotional(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(0)}K`;
  return fmt(n, 0);
}

export function DensityChart({ symbol, market = "spot", className }: DensityChartProps) {
  const [data, setData] = useState<DensityData | null>(null);
  const [loading, setLoading] = useState(false);
  const [multiplier, setMultiplier] = useState(5);

  const load = useCallback(async () => {
    if (!symbol) return;
    setLoading(true);
    try {
      const q = new URLSearchParams({
        symbol,
        market,
        multiplier: String(multiplier),
        min_notional: "10000",
      });
      const r = await fetch(apiUrl(`/api/density/walls?${q}`));
      const d: DensityData = await r.json();
      setData(d);
    } catch {
      setData(null);
    } finally {
      setLoading(false);
    }
  }, [symbol, market, multiplier]);

  useEffect(() => { load(); }, [load]);

  const walls = useMemo(() => {
    if (!data?.walls) return { bids: [], asks: [] };
    const bids = data.walls.filter((w) => w.side === "bid").sort((a, b) => b.price - a.price);
    const asks = data.walls.filter((w) => w.side === "ask").sort((a, b) => a.price - b.price);
    return { bids, asks };
  }, [data]);

  const maxNotional = useMemo(() => {
    if (!data?.walls) return 1;
    return Math.max(...data.walls.map((w) => w.notional_usdt), 1);
  }, [data]);

  return (
    <div className={className}>
      <div className="flex items-center gap-2 mb-3">
        <BarChart3 className="h-4 w-4 text-accent" />
        <span className="text-sm font-medium text-ink">Density Analysis</span>
        {loading && <RefreshCw className="h-3 w-3 animate-spin text-ink-muted" />}
        <label className="ml-auto flex items-center gap-1 text-xs text-ink-muted">
          Порог
          <select
            value={multiplier}
            onChange={(e) => setMultiplier(Number(e.target.value))}
            className="rounded border border-line bg-surface px-1.5 py-0.5 text-xs"
          >
            <option value={3}>3×</option>
            <option value={5}>5×</option>
            <option value={10}>10×</option>
          </select>
        </label>
        <button onClick={load} className="text-xs text-ink-muted hover:text-ink">
          <RefreshCw className="h-3.5 w-3.5" />
        </button>
      </div>

      {!data?.walls || data.walls.length === 0 ? (
        <p className="text-xs text-ink-muted py-4 text-center">
          {loading ? "Загрузка…" : "Нет стен — стакан равномерный или данные не загружены"}
        </p>
      ) : (
        <div className="space-y-1">
          {/* Ask side (красный, сверху) */}
          {walls.asks.slice(0, 8).map((w, i) => (
            <div key={`ask-${i}`} className="flex items-center gap-2">
              <span className="w-16 text-right font-mono text-[10px] text-rose-500">
                {fmt(w.price)}
              </span>
              <div className="flex-1 h-4 rounded-sm bg-rose-500/20 overflow-hidden">
                <div
                  className="h-full bg-rose-500/40 rounded-sm transition-all"
                  style={{ width: `${Math.min(100, (w.notional_usdt / maxNotional) * 100)}%` }}
                />
              </div>
              <span className="w-16 text-right font-mono text-[10px] text-ink-muted">
                ${fmtNotional(w.notional_usdt)}
              </span>
              <span className="w-8 text-right font-mono text-[10px] text-rose-400">
                {w.ratio_to_median.toFixed(0)}×
              </span>
            </div>
          ))}

          {/* Mid line */}
          <div className="border-t border-line my-1" />

          {/* Bid side (зелёный, снизу) */}
          {walls.bids.slice(0, 8).map((w, i) => (
            <div key={`bid-${i}`} className="flex items-center gap-2">
              <span className="w-16 text-right font-mono text-[10px] text-emerald-500">
                {fmt(w.price)}
              </span>
              <div className="flex-1 h-4 rounded-sm bg-emerald-500/20 overflow-hidden">
                <div
                  className="h-full bg-emerald-500/40 rounded-sm transition-all"
                  style={{ width: `${Math.min(100, (w.notional_usdt / maxNotional) * 100)}%` }}
                />
              </div>
              <span className="w-16 text-right font-mono text-[10px] text-ink-muted">
                ${fmtNotional(w.notional_usdt)}
              </span>
              <span className="w-8 text-right font-mono text-[10px] text-emerald-400">
                {w.ratio_to_median.toFixed(0)}×
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
