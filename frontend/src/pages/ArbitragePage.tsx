import { useCallback, useEffect, useRef, useState } from "react";
import { ArrowLeftRight, Pause, Play, Shield } from "lucide-react";
import { apiUrl } from "../config";
import { Skeleton, SkeletonCard, SkeletonTableRows } from "../components/ui/Skeleton";

function fmt(n: number | null | undefined, d = 2): string {
  if (n == null) return "—";
  return n.toFixed(d);
}
function fmtTime(s: number): string {
  if (s < 60) return `${s.toFixed(0)}с`;
  if (s < 3600) return `${(s / 60).toFixed(1)}м`;
  return `${(s / 3600).toFixed(1)}ч`;
}

// ─── Page Component ────────────────────────────────────────────────────────────

export function ArbitragePage() {
  const [status, setStatus] = useState<any>(null);
  const [trades, setTrades] = useState<any[]>([]);
  const [tab, setTab] = useState<"status" | "trades" | "settings">("status");
  const [fetchFailed, setFetchFailed] = useState(false);
  const pollRef = useRef<number>(0);

  const fetchStatus = useCallback(async () => {
    try {
      const r = await fetch(apiUrl("/api/arbitrage/status"));
      if (r.ok) {
        const d = await r.json();
        if (d.ok) { setStatus(d); setFetchFailed(false); return; }
      }
      setFetchFailed(true);
    } catch { setFetchFailed(true); }
  }, []);
  const fetchTrades = useCallback(async () => {
    try {
      const r = await fetch(apiUrl("/api/arbitrage/trades?limit=30"));
      if (r.ok) { const d = await r.json(); if (d.ok) setTrades(d.trades ?? []); }
    } catch {}
  }, []);

  // Load data on mount and poll
  useEffect(() => {
    fetchStatus(); fetchTrades();
    pollRef.current = window.setInterval(() => { fetchStatus(); if (tab === "trades") fetchTrades(); }, 3000);
    return () => window.clearInterval(pollRef.current);
  }, [tab, fetchStatus, fetchTrades]);

  const doStart = () => fetch(apiUrl("/api/arbitrage/start"), { method: "POST" }).then(fetchStatus);
  const doStop = () => fetch(apiUrl("/api/arbitrage/stop"), { method: "POST" }).then(fetchStatus);
  const doKill = (v: boolean) => fetch(apiUrl(`/api/arbitrage/kill-switch?enabled=${v}`), { method: "POST" }).then(fetchStatus);
  const updateSetting = (patch: any) => fetch(apiUrl("/api/arbitrage/settings"), { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(patch) }).then(fetchStatus);

  const running = status?.running ?? false;
  const stats = status?.stats;
  const settings = status?.settings;
  const positions = status?.open_positions ?? [];
  const initialLoading = status === null;

  return (
    <div className="flex h-full flex-col overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between border-b border-line px-5 py-3">
        <div className="flex items-center gap-3">
          <ArrowLeftRight className="h-5 w-5 text-emerald-500" />
          <h2 className="text-lg font-semibold text-ink">Cross-Exchange Arbitrage</h2>
          <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${running ? "bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400" : "bg-gray-100 text-gray-600 dark:bg-gray-800 dark:text-gray-400"}`}>
            {running ? "Running" : "Stopped"}
          </span>
          {settings && <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${settings.mode === "live" ? "bg-red-100 text-red-700" : "bg-blue-100 text-blue-700"}`}>{settings.mode}</span>}
        </div>
        <div className="flex items-center gap-2">
          {!running ? (
            <button onClick={doStart} className="flex items-center gap-1 rounded-lg bg-green-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-green-700"><Play className="h-3.5 w-3.5" /> Старт</button>
          ) : (
            <button onClick={doStop} className="flex items-center gap-1 rounded-lg bg-red-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-red-700"><Pause className="h-3.5 w-3.5" /> Стоп</button>
          )}
          <button onClick={() => doKill(!settings?.kill_switch)} className={`flex items-center gap-1 rounded-lg px-3 py-1.5 text-xs font-medium ${settings?.kill_switch ? "bg-red-100 text-red-700" : "bg-gray-100 text-gray-600"}`}>
            <Shield className="h-3.5 w-3.5" /> Kill: {settings?.kill_switch ? "ON" : "OFF"}
          </button>
        </div>
      </div>

      {/* Ошибка связи с бэкендом (0.6) */}
      {fetchFailed && initialLoading && (
        <div className="mx-5 mt-3 flex items-center gap-3 rounded-xl border border-red-500/40 bg-red-500/10 px-4 py-3 text-sm text-red-700 dark:text-red-300">
          <span className="flex-1">
            Не удалось загрузить статус арбитражного движка: бэкенд не
            отвечает на /api/arbitrage/status. Проверьте, что сервер запущен.
          </span>
          <button
            type="button"
            onClick={() => { fetchStatus(); fetchTrades(); }}
            className="shrink-0 rounded-lg border border-red-500/40 bg-red-500/10 px-3 py-1.5 text-xs font-medium text-red-700 transition hover:bg-red-500/20 dark:text-red-300"
          >
            Повторить
          </button>
        </div>
      )}

      {/* Stats bar */}
      {initialLoading && !fetchFailed && (
        <div className="border-b border-line px-5 py-2 flex items-center gap-6">
          {Array.from({ length: 5 }, (_, i) => (
            <Skeleton key={i} className="h-3 w-16" />
          ))}
        </div>
      )}
      {stats && stats.total_trades > 0 && (
        <div className="border-b border-line px-5 py-2 flex items-center gap-6 text-xs">
          <span className="text-ink-muted">Сделок: <span className="font-mono font-medium text-ink">{stats.total_trades}</span></span>
          <span className="text-ink-muted">Win: <span className="font-mono text-green-600">{stats.winning_trades}</span></span>
          <span className="text-ink-muted">Loss: <span className="font-mono text-red-600">{stats.losing_trades}</span></span>
          <span className="text-ink-muted">Net PNL: <span className={`font-mono font-medium ${stats.net_pnl_usdt >= 0 ? "text-green-600" : "text-red-600"}`}>{stats.net_pnl_usdt >= 0 ? "+" : ""}{fmt(stats.net_pnl_usdt, 4)} $</span></span>
          <span className="text-ink-muted">Avg hold: <span className="font-mono">{fmtTime(stats.avg_hold_sec)}</span></span>
        </div>
      )}

      {/* Open positions */}
      {positions.length > 0 && (
        <div className="border-b border-line px-5 py-2 bg-emerald-50 dark:bg-emerald-900/10">
          <p className="text-xs font-semibold text-ink-muted mb-1">Открытые позиции ({positions.length})</p>
          {positions.map((p: any, i: number) => (
            <div key={i} className="flex items-center gap-4 text-xs font-mono">
              <span className="font-medium">{p.symbol}</span>
              <span className="text-green-600">Buy {p.buy_exchange} @ {fmt(p.buy_price, 4)}</span>
              <span className="text-red-600">Sell {p.sell_exchange} @ {fmt(p.sell_price, 4)}</span>
              <span className="text-ink-muted">Basis: {fmt(p.entry_basis_bps)} bps</span>
            </div>
          ))}
        </div>
      )}

      {/* Tabs */}
      <div className="flex border-b border-line">
        {(["status", "trades", "settings"] as const).map((t) => (
          <button key={t} onClick={() => setTab(t)} className={`px-4 py-2 text-sm font-medium transition ${tab === t ? "border-b-2 border-emerald-500 text-emerald-600" : "text-ink-muted hover:text-ink"}`}>
            {t === "status" ? "Статус" : t === "trades" ? "Сделки" : "Настройки"}
          </button>
        ))}
      </div>

      {/* Content */}
      <div className="flex-1 overflow-y-auto p-5">
        {tab === "status" && !settings && (
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {Array.from({ length: 6 }, (_, i) => (
              <SkeletonCard key={i} />
            ))}
          </div>
        )}
        {tab === "status" && settings && (
          <div className="space-y-3 text-sm">
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
              <div className="rounded-lg bg-surface p-3"><span className="text-xs text-ink-muted">Символы</span><div className="font-mono text-xs mt-1">{settings.symbols?.join(", ")}</div></div>
              <div className="rounded-lg bg-surface p-3"><span className="text-xs text-ink-muted">Порог входа</span><div className="font-mono">{settings.entry_threshold_bps} bps</div></div>
              <div className="rounded-lg bg-surface p-3"><span className="text-xs text-ink-muted">Порог выхода</span><div className="font-mono">{settings.exit_threshold_bps} bps</div></div>
              <div className="rounded-lg bg-surface p-3"><span className="text-xs text-ink-muted">Макс. позиция</span><div className="font-mono">{settings.max_position_notional_usdt} USDT</div></div>
              <div className="rounded-lg bg-surface p-3"><span className="text-xs text-ink-muted">Макс. одновременных</span><div className="font-mono">{settings.max_concurrent_trades}</div></div>
              <div className="rounded-lg bg-surface p-3"><span className="text-xs text-ink-muted">Макс. удержание</span><div className="font-mono">{fmtTime(settings.max_hold_sec)}</div></div>
            </div>
          </div>
        )}

        {tab === "trades" && (
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead><tr className="border-b border-line text-left text-ink-muted">
                <th className="px-2 py-1.5">Время</th><th className="px-2 py-1.5">Символ</th>
                <th className="px-2 py-1.5">Направление</th><th className="px-2 py-1.5">Basis вход</th>
                <th className="px-2 py-1.5">Basis выход</th><th className="px-2 py-1.5">Net PNL</th>
                <th className="px-2 py-1.5">Удерж.</th><th className="px-2 py-1.5">Причина</th>
              </tr></thead>
              <tbody>
                {initialLoading && !fetchFailed && <SkeletonTableRows rows={6} colSpan={8} />}
                {!initialLoading && trades.length === 0 && (
                  <tr>
                    <td colSpan={8} className="px-2 py-8 text-center text-sm text-ink-muted">
                      Сделок пока нет.{" "}
                      {running
                        ? "Движок работает и ждёт, когда базис превысит порог входа — сделки появятся здесь автоматически."
                        : "Движок остановлен — нажмите «Старт» вверху, чтобы начать торговать (режим paper безопасен: сделки виртуальные)."}
                    </td>
                  </tr>
                )}
                {[...trades].reverse().map((t, i) => (
                  <tr key={i} className="border-b border-line/50 hover:bg-accent/5">
                    <td className="px-2 py-1.5 font-mono text-ink-muted">{t.close_time_iso ? new Date(t.close_time_iso).toLocaleTimeString() : "—"}</td>
                    <td className="px-2 py-1.5 font-mono font-medium">{t.symbol}</td>
                    <td className="px-2 py-1.5 text-xs">Buy {t.buy_exchange} → Sell {t.sell_exchange}</td>
                    <td className="px-2 py-1.5 font-mono">{fmt(t.entry_basis_bps)}</td>
                    <td className="px-2 py-1.5 font-mono">{fmt(t.exit_basis_bps)}</td>
                    <td className={`px-2 py-1.5 font-mono font-medium ${t.net_pnl_usdt >= 0 ? "text-green-600" : "text-red-600"}`}>{t.net_pnl_usdt >= 0 ? "+" : ""}{fmt(t.net_pnl_usdt, 4)}</td>
                    <td className="px-2 py-1.5 font-mono">{fmtTime(t.hold_sec)}</td>
                    <td className="px-2 py-1.5 text-ink-muted">{t.close_reason}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {tab === "settings" && settings && (
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 max-w-2xl">
            <label className="block"><span className="text-xs text-ink-muted">Символы (через запятую)</span>
              <input type="text" defaultValue={settings.symbols?.join(",")} onBlur={(e) => updateSetting({ symbols: e.target.value })}
                className="mt-0.5 w-full rounded-lg border border-line bg-surface px-3 py-2 text-sm font-mono text-ink" /></label>
            <label className="block"><span className="text-xs text-ink-muted">Режим</span>
              <select defaultValue={settings.mode} onChange={(e) => updateSetting({ mode: e.target.value })}
                className="mt-0.5 w-full rounded-lg border border-line bg-surface px-3 py-2 text-sm text-ink">
                <option value="paper">Paper</option><option value="live">Live</option></select></label>
            <label className="block"><span className="text-xs text-ink-muted">Порог входа (bps)</span>
              <input type="number" step="1" defaultValue={settings.entry_threshold_bps} onBlur={(e) => updateSetting({ entry_threshold_bps: parseFloat(e.target.value) })}
                className="mt-0.5 w-full rounded-lg border border-line bg-surface px-3 py-2 text-sm font-mono text-ink" /></label>
            <label className="block"><span className="text-xs text-ink-muted">Порог выхода (bps)</span>
              <input type="number" step="1" defaultValue={settings.exit_threshold_bps} onBlur={(e) => updateSetting({ exit_threshold_bps: parseFloat(e.target.value) })}
                className="mt-0.5 w-full rounded-lg border border-line bg-surface px-3 py-2 text-sm font-mono text-ink" /></label>
            <label className="block"><span className="text-xs text-ink-muted">Макс. позиция (USDT)</span>
              <input type="number" step="50" defaultValue={settings.max_position_notional_usdt} onBlur={(e) => updateSetting({ max_position_notional_usdt: parseFloat(e.target.value) })}
                className="mt-0.5 w-full rounded-lg border border-line bg-surface px-3 py-2 text-sm font-mono text-ink" /></label>
            <label className="block"><span className="text-xs text-ink-muted">Макс. одновременных</span>
              <input type="number" step="1" defaultValue={settings.max_concurrent_trades} onBlur={(e) => updateSetting({ max_concurrent_trades: parseInt(e.target.value) })}
                className="mt-0.5 w-full rounded-lg border border-line bg-surface px-3 py-2 text-sm font-mono text-ink" /></label>
          </div>
        )}
      </div>
    </div>
  );
}
