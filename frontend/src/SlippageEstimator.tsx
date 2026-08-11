import { useCallback, useState } from "react";
import { BookOpen, ChevronDown, ChevronUp } from "lucide-react";
import { apiUrl } from "./config";

interface SlippageResult {
  ok: boolean;
  symbol: string;
  side: string;
  notional_usdt: number;
  best_price: number;
  vwap_price: number;
  slippage_bps: number;
  filled_notional_usdt: number;
  unfilled_notional_usdt: number;
  fully_filled: boolean;
  levels_consumed: number;
  mid: number;
  error?: string;
}

export function SlippageEstimator({
  symbol: externalSymbol,
  market: externalMarket,
}: {
  symbol?: string;
  market?: string;
} = {}) {
  const [open, setOpen] = useState(false);
  const [symbol, setSymbol] = useState(externalSymbol ?? "BTCUSDT");
  const [market, setMarket] = useState(externalMarket ?? "spot");
  const [exchange] = useState("mexc");
  const [notional, setNotional] = useState(1000);
  const [side, setSide] = useState<"buy" | "sell">("buy");
  const [result, setResult] = useState<SlippageResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const estimate = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const q = new URLSearchParams({
        symbol,
        market,
        exchange,
        notional_usdt: String(notional),
        side,
      });
      const r = await fetch(apiUrl(`/api/slippage-estimate?${q}`));
      const d: SlippageResult = await r.json();
      if (d.ok) {
        setResult(d);
      } else {
        setError(d.error ?? "Ошибка");
        setResult(null);
      }
    } catch (e) {
      setError(String(e));
      setResult(null);
    } finally {
      setLoading(false);
    }
  }, [symbol, market, exchange, notional, side]);

  return (
    <div className="rounded-xl border border-line bg-surface-elevated">
      <button
        type="button"
        onClick={() => setOpen(!open)}
        className="flex w-full items-center gap-2 px-4 py-3 text-left text-sm font-medium text-ink transition hover:bg-accent/5"
      >
        <BookOpen className="h-4 w-4 text-accent" />
        <span className="flex-1">L2 Slippage Estimator</span>
        {open ? <ChevronUp className="h-4 w-4" /> : <ChevronDown className="h-4 w-4" />}
      </button>
      {open && (
        <div className="border-t border-line px-4 py-3 space-y-3">
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-5">
            <label className="flex flex-col gap-1 text-xs text-ink-muted">
              Символ
              <input
                value={symbol}
                onChange={(e) => setSymbol(e.target.value.toUpperCase())}
                className="rounded-lg border border-line bg-surface px-2 py-1.5 font-mono text-sm text-ink"
              />
            </label>
            <label className="flex flex-col gap-1 text-xs text-ink-muted">
              Рынок
              <select
                value={market}
                onChange={(e) => setMarket(e.target.value)}
                className="rounded-lg border border-line bg-surface px-2 py-1.5 text-sm text-ink"
              >
                <option value="spot">Spot</option>
                <option value="futures">Futures</option>
              </select>
            </label>
            <label className="flex flex-col gap-1 text-xs text-ink-muted">
              Сторона
              <select
                value={side}
                onChange={(e) => setSide(e.target.value as "buy" | "sell")}
                className="rounded-lg border border-line bg-surface px-2 py-1.5 text-sm text-ink"
              >
                <option value="buy">Buy (Ask)</option>
                <option value="sell">Sell (Bid)</option>
              </select>
            </label>
            <label className="flex flex-col gap-1 text-xs text-ink-muted">
              Размер (USDT)
              <input
                type="number"
                value={notional}
                onChange={(e) => setNotional(Number(e.target.value) || 0)}
                className="rounded-lg border border-line bg-surface px-2 py-1.5 font-mono text-sm text-ink"
              />
            </label>
            <div className="flex items-end">
              <button
                type="button"
                onClick={estimate}
                disabled={loading}
                className="w-full rounded-lg bg-accent px-4 py-2 text-sm font-medium text-white transition hover:bg-accent/90 disabled:opacity-50"
              >
                {loading ? "Оценка…" : "Оценить"}
              </button>
            </div>
          </div>
          {error && (
            <div className="rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-xs text-red-600 dark:text-red-400">
              {error}
            </div>
          )}
          {result && (
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
              <div className="rounded-lg bg-surface p-3">
                <div className="text-[10px] text-ink-muted uppercase">Best Price</div>
                <div className="font-mono text-sm font-bold text-ink">{result.best_price}</div>
              </div>
              <div className="rounded-lg bg-surface p-3">
                <div className="text-[10px] text-ink-muted uppercase">VWAP Price</div>
                <div className="font-mono text-sm font-bold text-ink">{result.vwap_price}</div>
              </div>
              <div className="rounded-lg bg-surface p-3">
                <div className="text-[10px] text-ink-muted uppercase">Slippage</div>
                <div className={`font-mono text-sm font-bold ${
                  result.slippage_bps <= 1 ? "text-emerald-500"
                  : result.slippage_bps <= 5 ? "text-amber-500"
                  : "text-rose-500"
                }`}>
                  {result.slippage_bps.toFixed(2)} bps
                </div>
              </div>
              <div className="rounded-lg bg-surface p-3">
                <div className="text-[10px] text-ink-muted uppercase">Fill</div>
                <div className={`font-mono text-sm font-bold ${result.fully_filled ? "text-emerald-500" : "text-amber-500"}`}>
                  {result.fully_filled ? "100%" : `${((result.filled_notional_usdt / result.notional_usdt) * 100).toFixed(0)}%`}
                </div>
                <div className="text-[10px] text-ink-muted">
                  {result.filled_notional_usdt} / {result.notional_usdt} USDT
                </div>
              </div>
              <div className="col-span-2 rounded-lg bg-surface p-3">
                <div className="text-[10px] text-ink-muted uppercase">Уровней стакана</div>
                <div className="font-mono text-sm text-ink">{result.levels_consumed}</div>
              </div>
              <div className="col-span-2 rounded-lg bg-surface p-3">
                <div className="text-[10px] text-ink-muted uppercase">Стоимость проскальзывания</div>
                <div className="font-mono text-sm font-bold text-ink">
                  {((result.vwap_price - result.best_price) * (result.side === "buy" ? 1 : -1) * (result.notional_usdt / result.best_price)).toFixed(4)} USDT
                </div>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
