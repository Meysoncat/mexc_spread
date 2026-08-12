import { useCallback, useState } from "react";
import { Play, BarChart3, TrendingUp, TrendingDown, Target } from "lucide-react";
import { apiUrl } from "../config";

interface BacktestTrade {
  entry_time: string;
  exit_time: string;
  entry_spread_bps: number;
  exit_spread_bps: number;
  hold_sec: number;
  gross_pnl_bps: number;
  net_pnl_bps: number;
  net_pnl_usdt: number;
  exit_reason: string;
}

interface BacktestResult {
  ok: boolean;
  error?: string;
  settings?: Record<string, unknown>;
  summary?: {
    total_snapshots: number;
    total_trades: number;
    winning_trades: number;
    losing_trades: number;
    win_rate: number;
    total_pnl_usdt: number;
    total_pnl_bps: number;
    avg_pnl_bps: number;
    max_drawdown_usdt: number;
    max_consecutive_losses: number;
    avg_hold_sec: number;
  };
  trades?: BacktestTrade[];
}

function fmt(n: number, digits = 2): string {
  return n.toLocaleString("ru-RU", { maximumFractionDigits: digits });
}

export function BacktestPage() {
  const [symbol, setSymbol] = useState("BTCUSDT");
  const [market, setMarket] = useState("futures");
  const [entryBps, setEntryBps] = useState(30);
  const [exitBps, setExitBps] = useState(5);
  const [notional, setNotional] = useState(1000);
  const [maxHold, setMaxHold] = useState(300);
  const [result, setResult] = useState<BacktestResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const run = useCallback(async () => {
    setLoading(true);
    setError(null);
    setResult(null);
    try {
      const q = new URLSearchParams({
        symbol,
        market,
        entry_bps: String(entryBps),
        exit_bps: String(exitBps),
        notional: String(notional),
        max_hold_sec: String(maxHold),
      });
      const r = await fetch(apiUrl(`/api/backtest?${q}`));
      const d: BacktestResult = await r.json();
      if (d.ok) {
        setResult(d);
      } else {
        setError(d.error ?? "Ошибка");
      }
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  }, [symbol, market, entryBps, exitBps, notional, maxHold]);

  const s = result?.summary;

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-4 p-3 md:p-6">
      <div>
        <h1 className="text-lg font-semibold text-ink">Backtest Engine</h1>
        <p className="mt-1 text-xs text-ink-muted">
          Тестирование стратегии захвата спреда на исторических данных из SQLite.
        </p>
      </div>

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-6">
        <label className="flex flex-col gap-1 text-xs text-ink-muted">
          Символ
          <input value={symbol} onChange={(e) => setSymbol(e.target.value.toUpperCase())}
            className="rounded-lg border border-line bg-surface px-2 py-1.5 font-mono text-sm text-ink" />
        </label>
        <label className="flex flex-col gap-1 text-xs text-ink-muted">
          Рынок
          <select value={market} onChange={(e) => setMarket(e.target.value)}
            className="rounded-lg border border-line bg-surface px-2 py-1.5 text-sm text-ink">
            <option value="spot">Spot</option>
            <option value="futures">Futures</option>
          </select>
        </label>
        <label className="flex flex-col gap-1 text-xs text-ink-muted">
          Вход (bps)
          <input type="number" value={entryBps} onChange={(e) => setEntryBps(Number(e.target.value) || 0)}
            className="rounded-lg border border-line bg-surface px-2 py-1.5 font-mono text-sm text-ink" />
        </label>
        <label className="flex flex-col gap-1 text-xs text-ink-muted">
          Выход (bps)
          <input type="number" value={exitBps} onChange={(e) => setExitBps(Number(e.target.value) || 0)}
            className="rounded-lg border border-line bg-surface px-2 py-1.5 font-mono text-sm text-ink" />
        </label>
        <label className="flex flex-col gap-1 text-xs text-ink-muted">
          Размер (USDT)
          <input type="number" value={notional} onChange={(e) => setNotional(Number(e.target.value) || 0)}
            className="rounded-lg border border-line bg-surface px-2 py-1.5 font-mono text-sm text-ink" />
        </label>
        <label className="flex flex-col gap-1 text-xs text-ink-muted">
          Макс. удерж. (сек)
          <input type="number" value={maxHold} onChange={(e) => setMaxHold(Number(e.target.value) || 0)}
            className="rounded-lg border border-line bg-surface px-2 py-1.5 font-mono text-sm text-ink" />
        </label>
      </div>

      <button onClick={run} disabled={loading}
        className="inline-flex w-fit items-center gap-2 rounded-lg bg-accent px-4 py-2 text-sm font-medium text-accent-foreground transition hover:bg-accent/90 disabled:opacity-50">
        <Play className="h-4 w-4" />
        {loading ? "Запуск…" : "Запустить бэктест"}
      </button>

      {error && (
        <div className="rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-sm text-red-600 dark:text-red-400">
          {error}
        </div>
      )}

      {s && (
        <>
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            <div className="rounded-lg bg-surface p-3">
              <div className="flex items-center gap-1.5 text-xs text-ink-muted"><Target className="h-3.5 w-3.5" /> Сделок</div>
              <div className="mt-1 font-mono text-lg font-bold text-ink">{s.total_trades}</div>
              <div className="text-[10px] text-ink-muted">из {s.total_snapshots} снимков</div>
            </div>
            <div className="rounded-lg bg-surface p-3">
              <div className="flex items-center gap-1.5 text-xs text-ink-muted"><TrendingUp className="h-3.5 w-3.5" /> Win Rate</div>
              <div className={`mt-1 font-mono text-lg font-bold ${s.win_rate >= 0.5 ? "text-emerald-500" : "text-rose-500"}`}>
                {(s.win_rate * 100).toFixed(1)}%
              </div>
              <div className="text-[10px] text-ink-muted">{s.winning_trades}W / {s.losing_trades}L</div>
            </div>
            <div className="rounded-lg bg-surface p-3">
              <div className="flex items-center gap-1.5 text-xs text-ink-muted"><BarChart3 className="h-3.5 w-3.5" /> PnL</div>
              <div className={`mt-1 font-mono text-lg font-bold ${s.total_pnl_usdt >= 0 ? "text-emerald-500" : "text-rose-500"}`}>
                {fmt(s.total_pnl_usdt)} $
              </div>
              <div className="text-[10px] text-ink-muted">{fmt(s.total_pnl_bps)} bps</div>
            </div>
            <div className="rounded-lg bg-surface p-3">
              <div className="flex items-center gap-1.5 text-xs text-ink-muted"><TrendingDown className="h-3.5 w-3.5" /> Max DD</div>
              <div className="mt-1 font-mono text-lg font-bold text-rose-500">{fmt(s.max_drawdown_usdt)} $</div>
              <div className="text-[10px] text-ink-muted">серия: {s.max_consecutive_losses}</div>
            </div>
          </div>

          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
            <div className="rounded-lg bg-surface p-2 text-xs">
              <span className="text-ink-muted">Средний PnL:</span>{" "}
              <span className="font-mono">{fmt(s.avg_pnl_bps)} bps</span>
            </div>
            <div className="rounded-lg bg-surface p-2 text-xs">
              <span className="text-ink-muted">Среднее удержание:</span>{" "}
              <span className="font-mono">{fmt(s.avg_hold_sec, 0)} сек</span>
            </div>
          </div>

          {result.trades && result.trades.length > 0 && (
            <div className="overflow-auto rounded-xl border border-line bg-surface-elevated">
              <table className="w-full text-xs">
                <thead className="sticky top-0 bg-surface-elevated text-[10px] uppercase tracking-wide text-ink-muted">
                  <tr>
                    <th className="px-2 py-1.5 text-left">Вход</th>
                    <th className="px-2 py-1.5 text-left">Выход</th>
                    <th className="px-2 py-1.5 text-right">Вход bps</th>
                    <th className="px-2 py-1.5 text-right">Выход bps</th>
                    <th className="px-2 py-1.5 text-right">Удерж.</th>
                    <th className="px-2 py-1.5 text-right">PnL bps</th>
                    <th className="px-2 py-1.5 text-right">PnL $</th>
                    <th className="px-2 py-1.5 text-center">Причина</th>
                  </tr>
                </thead>
                <tbody>
                  {result.trades.map((t, i) => (
                    <tr key={i} className="border-b border-line/40 hover:bg-accent/5">
                      <td className="px-2 py-1.5 font-mono text-ink-muted">{t.entry_time.slice(11, 19)}</td>
                      <td className="px-2 py-1.5 font-mono text-ink-muted">{t.exit_time.slice(11, 19)}</td>
                      <td className="px-2 py-1.5 text-right font-mono">{fmt(t.entry_spread_bps)}</td>
                      <td className="px-2 py-1.5 text-right font-mono">{fmt(t.exit_spread_bps)}</td>
                      <td className="px-2 py-1.5 text-right font-mono">{fmt(t.hold_sec, 0)}s</td>
                      <td className={`px-2 py-1.5 text-right font-mono font-semibold ${t.net_pnl_bps >= 0 ? "text-emerald-500" : "text-rose-500"}`}>
                        {fmt(t.net_pnl_bps)}
                      </td>
                      <td className={`px-2 py-1.5 text-right font-mono font-semibold ${t.net_pnl_usdt >= 0 ? "text-emerald-500" : "text-rose-500"}`}>
                        {fmt(t.net_pnl_usdt)}
                      </td>
                      <td className="px-2 py-1.5 text-center text-ink-muted">{t.exit_reason}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}
    </div>
  );
}
