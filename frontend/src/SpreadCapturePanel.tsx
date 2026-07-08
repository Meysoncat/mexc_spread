import { useCallback, useEffect, useRef, useState } from "react";
import {
  Activity,
  History,
  Pause,
  Play,
  RotateCcw,
  Settings,
  Zap,
} from "lucide-react";
import { apiUrl } from "./config";

interface CaptureSettings {
  symbol: string;
  market: string;
  mode: "monitor" | "paper" | "live";
  entry_threshold_bps: number;
  exit_threshold_bps: number;
  order_notional_usdt: number;
  max_hold_sec: number;
  taker_fee_bps: number;
  enabled: boolean;
  kill_switch: boolean;
  loop_interval_sec: number;
  max_trades_per_hour: number;
  sound_alert: boolean;
  max_tick_age_ms: number;
  fill_rate_per_sec: number;
  adverse_selection_ratio: number;
  realistic_fills: boolean;
}

interface CapturePosition {
  state: "idle" | "pending_buy" | "holding" | "pending_sell";
  entry_price: number;
  entry_qty: number;
  entry_time_ms: number;
  entry_spread_bps: number;
}

interface CaptureStatsData {
  total_trades: number;
  winning_trades: number;
  losing_trades: number;
  total_pnl_usdt: number;
  total_fees_usdt: number;
  net_pnl_usdt: number;
  avg_hold_sec: number;
  avg_spread_captured_bps: number;
  max_pnl_usdt: number;
  min_pnl_usdt: number;
  trades_this_hour: number;
}

interface PnlData {
  state: string;
  symbol: string;
  entry_price: number;
  current_ask: number;
  current_bid: number;
  current_spread_bps: number | null;
  qty: number;
  notional_usdt: number;
  unrealized_gross_pnl_usdt: number;
  unrealized_fees_usdt: number;
  unrealized_net_pnl_usdt: number;
  hold_sec: number;
  entry_spread_bps: number;
}

interface TradeRecord {
  symbol: string;
  mode: string;
  entry_price: number;
  exit_price: number;
  qty: number;
  entry_spread_bps: number;
  exit_spread_bps: number;
  entry_time_iso: string;
  exit_time_iso: string;
  hold_sec: number;
  gross_pnl_usdt: number;
  fees_usdt: number;
  adverse_cost_usdt: number;
  net_pnl_usdt: number;
  net_pnl_bps: number;
}

function fmt(n: number | null | undefined, digits = 2): string {
  if (n == null) return "—";
  return n.toFixed(digits);
}

function fmtTime(sec: number): string {
  if (sec < 60) return `${sec.toFixed(0)}с`;
  if (sec < 3600) return `${(sec / 60).toFixed(1)}м`;
  return `${(sec / 3600).toFixed(1)}ч`;
}

export function SpreadCapturePanel({ open = true, onClose, pageMode = false }: { open?: boolean; onClose?: () => void; pageMode?: boolean }) {
  const [settings, setSettings] = useState<CaptureSettings | null>(null);
  const [position, setPosition] = useState<CapturePosition | null>(null);
  const [stats, setStats] = useState<CaptureStatsData | null>(null);
  const [pnl, setPnl] = useState<PnlData | null>(null);
  const [trades, setTrades] = useState<TradeRecord[]>([]);
  const [running, setRunning] = useState(false);
  const [tab, setTab] = useState<"settings" | "trades" | "log">("settings");
  const [events, setEvents] = useState<any[]>([]);

  const pollRef = useRef<number>(0);

  const fetchStatus = useCallback(async () => {
    try {
      const r = await fetch(apiUrl("/api/capture/status"));
      if (!r.ok) return;
      const data = await r.json();
      if (!data.ok) return;
      setSettings(data.settings);
      setPosition(data.position);
      setStats(data.stats);
      setRunning(data.running);
    } catch { /* ignore */ }
  }, []);

  const fetchPnl = useCallback(async () => {
    try {
      const r = await fetch(apiUrl("/api/capture/pnl"));
      if (!r.ok) return;
      const data = await r.json();
      setPnl(data.ok ? data.pnl : null);
    } catch { /* ignore */ }
  }, []);

  const fetchTrades = useCallback(async () => {
    try {
      const r = await fetch(apiUrl("/api/capture/trades?limit=20"));
      if (!r.ok) return;
      const data = await r.json();
      if (data.ok) setTrades(data.trades ?? []);
    } catch { /* ignore */ }
  }, []);

  const fetchEvents = useCallback(async () => {
    try {
      const r = await fetch(apiUrl("/api/capture/events?limit=30"));
      if (!r.ok) return;
      const data = await r.json();
      if (data.ok) setEvents(data.events ?? []);
    } catch { /* ignore */ }
  }, []);

  useEffect(() => {
    if (!open && !pageMode) return;
    fetchStatus();
    fetchPnl();
    fetchTrades();
    fetchEvents();
    pollRef.current = window.setInterval(() => {
      fetchStatus();
      fetchPnl();
      if (tab === "trades") fetchTrades();
      if (tab === "log") fetchEvents();
    }, 2000);
    return () => window.clearInterval(pollRef.current);
  }, [open, pageMode, tab, fetchStatus, fetchPnl, fetchTrades, fetchEvents]);

  useEffect(() => {
    if (!open || pageMode) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose?.(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose, pageMode]);

  const updateSetting = async (patch: Record<string, any>) => {
    try {
      const r = await fetch(apiUrl("/api/capture/settings"), {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(patch),
      });
      if (r.ok) {
        const data = await r.json();
        if (data.ok) {
          setSettings(data.settings);
          setPosition(data.position);
          setStats(data.stats);
          setRunning(data.running);
        }
      }
    } catch { /* ignore */ }
  };

  const doStart = async () => {
    await fetch(apiUrl("/api/capture/start"), { method: "POST" });
    fetchStatus();
  };

  const doStop = async () => {
    await fetch(apiUrl("/api/capture/stop"), { method: "POST" });
    fetchStatus();
  };

  const doResetPosition = async () => {
    await fetch(apiUrl("/api/capture/reset-position"), { method: "POST" });
    fetchStatus();
    fetchPnl();
  };

  const doResetStats = async () => {
    await fetch(apiUrl("/api/capture/reset-stats"), { method: "POST" });
    fetchStatus();
    fetchTrades();
  };

  if (!open && !pageMode) return null;

  const content = (
    <>
      {/* Header */}
      <div className="flex items-center justify-between border-b border-line px-5 py-3">
        <div className="flex items-center gap-3">
          <Zap className="h-5 w-5 text-amber-500" />
          <h2 className="text-lg font-semibold text-ink">Spread Capture</h2>
          <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${
            running
              ? "bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400"
              : "bg-gray-100 text-gray-600 dark:bg-gray-800 dark:text-gray-400"
          }`}>
            {running ? "Running" : "Stopped"}
          </span>
          {settings && (
            <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${
              settings.mode === "live"
                ? "bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400"
                : settings.mode === "paper"
                  ? "bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400"
                  : "bg-gray-100 text-gray-600 dark:bg-gray-800 dark:text-gray-400"
            }`}>
              {settings.mode}
            </span>
          )}
        </div>
        <div className="flex items-center gap-2">
          {!running ? (
            <button onClick={doStart} className="flex items-center gap-1 rounded-lg bg-green-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-green-700">
              <Play className="h-3.5 w-3.5" /> Старт
            </button>
          ) : (
            <button onClick={doStop} className="flex items-center gap-1 rounded-lg bg-red-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-red-700">
              <Pause className="h-3.5 w-3.5" /> Стоп
            </button>
          )}
          {!pageMode && (
            <button onClick={onClose} className="rounded-lg border border-line p-2 text-ink hover:bg-surface">
              ✕
            </button>
          )}
        </div>
      </div>

        {/* Position & PNL bar */}
        {position && position.state !== "idle" && pnl && (
          <div className="border-b border-line bg-amber-50 dark:bg-amber-900/10 px-5 py-3">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-4 text-sm">
                <span className="font-medium text-ink">
                  📊 Позиция: {settings?.symbol}
                </span>
                <span className="text-ink-muted">
                  Вход: <span className="font-mono">{fmt(pnl.entry_price, 6)}</span>
                </span>
                <span className="text-ink-muted">
                  Кол-во: <span className="font-mono">{fmt(pnl.qty, 6)}</span>
                </span>
                <span className="text-ink-muted">
                  Удержание: <span className="font-mono">{fmtTime(pnl.hold_sec)}</span>
                </span>
              </div>
              <div className="flex items-center gap-3">
                <span className={`text-lg font-bold font-mono ${
                  pnl.unrealized_net_pnl_usdt >= 0 ? "text-green-600 dark:text-green-400" : "text-red-600 dark:text-red-400"
                }`}>
                  {pnl.unrealized_net_pnl_usdt >= 0 ? "+" : ""}{fmt(pnl.unrealized_net_pnl_usdt, 4)} $
                </span>
                <button onClick={doResetPosition} className="rounded-md p-1 text-ink-muted hover:text-red-500" title="Сбросить позицию">
                  <RotateCcw className="h-4 w-4" />
                </button>
              </div>
            </div>
          </div>
        )}

        {/* Stats summary */}
        {stats && stats.total_trades > 0 && (
          <div className="border-b border-line px-5 py-2 flex items-center gap-6 text-xs">
            <span className="text-ink-muted">Сделок: <span className="font-mono font-medium text-ink">{stats.total_trades}</span></span>
            <span className="text-ink-muted">Win: <span className="font-mono text-green-600">{stats.winning_trades}</span></span>
            <span className="text-ink-muted">Loss: <span className="font-mono text-red-600">{stats.losing_trades}</span></span>
            <span className="text-ink-muted">Net PNL: <span className={`font-mono font-medium ${stats.net_pnl_usdt >= 0 ? "text-green-600" : "text-red-600"}`}>{stats.net_pnl_usdt >= 0 ? "+" : ""}{fmt(stats.net_pnl_usdt, 4)} $</span></span>
            <span className="text-ink-muted">Avg hold: <span className="font-mono">{fmtTime(stats.avg_hold_sec)}</span></span>
            <span className="text-ink-muted">Avg bps: <span className="font-mono">{fmt(stats.avg_spread_captured_bps)}</span></span>
            <button onClick={doResetStats} className="ml-auto rounded-md p-1 text-ink-muted hover:text-red-500" title="Сбросить статистику">
              <RotateCcw className="h-3.5 w-3.5" />
            </button>
          </div>
        )}

        {/* Tabs */}
        <div className="flex border-b border-line">
          {(["settings", "trades", "log"] as const).map((t) => (
            <button
              key={t}
              onClick={() => setTab(t)}
              className={`px-4 py-2 text-sm font-medium transition ${
                tab === t
                  ? "border-b-2 border-amber-500 text-amber-600 dark:text-amber-400"
                  : "text-ink-muted hover:text-ink"
              }`}
            >
              {t === "settings" && <Settings className="inline h-3.5 w-3.5 mr-1" />}
              {t === "trades" && <History className="inline h-3.5 w-3.5 mr-1" />}
              {t === "log" && <Activity className="inline h-3.5 w-3.5 mr-1" />}
              {t === "settings" ? "Параметры" : t === "trades" ? "Сделки" : "Лог"}
            </button>
          ))}
        </div>

        {/* Tab content */}
        <div className="flex-1 overflow-y-auto p-5">
          {tab === "settings" && settings && (
            <>
            <div className="grid grid-cols-2 gap-4 max-w-2xl">
              <label className="block">
                <span className="text-xs text-ink-muted">Тикер</span>
                <input
                  type="text"
                  value={settings.symbol}
                  onChange={(e) => updateSetting({ symbol: e.target.value })}
                  className="mt-0.5 w-full rounded-lg border border-line bg-surface px-3 py-2 font-mono text-sm text-ink outline-none focus:ring-2 focus:ring-amber-500"
                />
              </label>
              <label className="block">
                <span className="text-xs text-ink-muted">Режим</span>
                <select
                  value={settings.mode}
                  onChange={(e) => updateSetting({ mode: e.target.value })}
                  className="mt-0.5 w-full rounded-lg border border-line bg-surface px-3 py-2 text-sm text-ink outline-none focus:ring-2 focus:ring-amber-500"
                >
                  <option value="monitor">Monitor (только сигналы)</option>
                  <option value="paper">Paper (симуляция)</option>
                  <option value="live">Live (реальные ордера)</option>
                </select>
              </label>
              <label className="block">
                <span className="text-xs text-ink-muted">Порог входа (bps)</span>
                <input
                  type="number"
                  step="0.5"
                  value={settings.entry_threshold_bps}
                  onChange={(e) => updateSetting({ entry_threshold_bps: parseFloat(e.target.value) || 0 })}
                  className="mt-0.5 w-full rounded-lg border border-line bg-surface px-3 py-2 font-mono text-sm text-ink outline-none focus:ring-2 focus:ring-amber-500"
                />
              </label>
              <label className="block">
                <span className="text-xs text-ink-muted">Порог выхода (bps)</span>
                <input
                  type="number"
                  step="0.5"
                  value={settings.exit_threshold_bps}
                  onChange={(e) => updateSetting({ exit_threshold_bps: parseFloat(e.target.value) || 0 })}
                  className="mt-0.5 w-full rounded-lg border border-line bg-surface px-3 py-2 font-mono text-sm text-ink outline-none focus:ring-2 focus:ring-amber-500"
                />
              </label>
              <label className="block">
                <span className="text-xs text-ink-muted">Размер ордера (USDT)</span>
                <input
                  type="number"
                  step="10"
                  value={settings.order_notional_usdt}
                  onChange={(e) => updateSetting({ order_notional_usdt: parseFloat(e.target.value) || 50 })}
                  className="mt-0.5 w-full rounded-lg border border-line bg-surface px-3 py-2 font-mono text-sm text-ink outline-none focus:ring-2 focus:ring-amber-500"
                />
              </label>
              <label className="block">
                <span className="text-xs text-ink-muted">Макс. удержание (сек)</span>
                <input
                  type="number"
                  step="10"
                  value={settings.max_hold_sec}
                  onChange={(e) => updateSetting({ max_hold_sec: parseFloat(e.target.value) || 300 })}
                  className="mt-0.5 w-full rounded-lg border border-line bg-surface px-3 py-2 font-mono text-sm text-ink outline-none focus:ring-2 focus:ring-amber-500"
                />
              </label>
              <label className="block">
                <span className="text-xs text-ink-muted">Комиссия taker (bps, одна сторона)</span>
                <input
                  type="number"
                  step="0.1"
                  value={settings.taker_fee_bps}
                  onChange={(e) => updateSetting({ taker_fee_bps: parseFloat(e.target.value) || 0 })}
                  className="mt-0.5 w-full rounded-lg border border-line bg-surface px-3 py-2 font-mono text-sm text-ink outline-none focus:ring-2 focus:ring-amber-500"
                />
              </label>
              <label className="block">
                <span className="text-xs text-ink-muted">Макс. сделок в час</span>
                <input
                  type="number"
                  step="1"
                  value={settings.max_trades_per_hour}
                  onChange={(e) => updateSetting({ max_trades_per_hour: parseInt(e.target.value) || 60 })}
                  className="mt-0.5 w-full rounded-lg border border-line bg-surface px-3 py-2 font-mono text-sm text-ink outline-none focus:ring-2 focus:ring-amber-500"
                />
              </label>
              <div className="col-span-2 flex items-center gap-6 pt-2">
                <label className="flex items-center gap-2 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={settings.enabled}
                    onChange={(e) => updateSetting({ enabled: e.target.checked })}
                    className="h-4 w-4 rounded border-line text-amber-500 focus:ring-amber-500"
                  />
                  <span className="text-sm text-ink">Включён</span>
                </label>
                <label className="flex items-center gap-2 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={settings.kill_switch}
                    onChange={(e) => updateSetting({ kill_switch: e.target.checked })}
                    className="h-4 w-4 rounded border-line text-red-500 focus:ring-red-500"
                  />
                  <span className="text-sm text-ink">Kill Switch</span>
                </label>
                <label className="flex items-center gap-2 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={settings.sound_alert}
                    onChange={(e) => updateSetting({ sound_alert: e.target.checked })}
                    className="h-4 w-4 rounded border-line text-amber-500 focus:ring-amber-500"
                  />
                  <span className="text-sm text-ink">🔔 Звук</span>
                </label>
              </div>
            </div>

            {/* Execution Model Settings */}
            {settings.mode === "paper" && (
              <div className="space-y-3 border-t border-line pt-3">
                <div className="text-xs font-semibold text-ink-muted">
                  Модель исполнения (paper)
                </div>
                <label className="flex items-center gap-2 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={settings.realistic_fills}
                    onChange={(e) => updateSetting({ realistic_fills: e.target.checked })}
                    className="h-4 w-4 rounded border-line text-amber-500 focus:ring-amber-500"
                  />
                  <span className="text-sm text-ink">Реалистичные fills</span>
                </label>
                {settings.realistic_fills && (
                  <div className="grid grid-cols-2 gap-3">
                    <div>
                      <label className="text-xs text-ink-muted">Fill rate (/сек)</label>
                      <input
                        type="number"
                        step="0.01"
                        value={settings.fill_rate_per_sec}
                        onChange={(e) => updateSetting({ fill_rate_per_sec: parseFloat(e.target.value) || 0.1 })}
                        className="w-full rounded-md border border-line bg-bg px-2 py-1 text-sm text-ink"
                      />
                    </div>
                    <div>
                      <label className="text-xs text-ink-muted">Adverse selection</label>
                      <input
                        type="number"
                        step="0.05"
                        min="0"
                        max="1"
                        value={settings.adverse_selection_ratio}
                        onChange={(e) => updateSetting({ adverse_selection_ratio: parseFloat(e.target.value) || 0 })}
                        className="w-full rounded-md border border-line bg-bg px-2 py-1 text-sm text-ink"
                      />
                    </div>
                    <div>
                      <label className="text-xs text-ink-muted">Max tick age (мс)</label>
                      <input
                        type="number"
                        step="500"
                        value={settings.max_tick_age_ms}
                        onChange={(e) => updateSetting({ max_tick_age_ms: parseFloat(e.target.value) || 5000 })}
                        className="w-full rounded-md border border-line bg-bg px-2 py-1 text-sm text-ink"
                      />
                    </div>
                  </div>
                )}
              </div>
            )}
            </>
          )}

          {tab === "trades" && (
            <div className="space-y-2">
              {trades.length === 0 ? (
                <p className="text-sm text-ink-muted py-8 text-center">Нет сделок</p>
              ) : (
                <table className="w-full text-xs">
                  <thead>
                    <tr className="border-b border-line text-left text-ink-muted">
                      <th className="px-2 py-1.5">Время</th>
                      <th className="px-2 py-1.5">Символ</th>
                      <th className="px-2 py-1.5">Вход</th>
                      <th className="px-2 py-1.5">Выход</th>
                      <th className="px-2 py-1.5">Удерж.</th>
                      <th className="px-2 py-1.5">Gross PNL</th>
                      <th className="px-2 py-1.5">Fees</th>
                      <th className="px-2 py-1.5">Net PNL</th>
                    </tr>
                  </thead>
                  <tbody>
                    {[...trades].reverse().map((t, i) => (
                      <tr key={i} className="border-b border-line/50 hover:bg-accent/5">
                        <td className="px-2 py-1.5 font-mono text-ink-muted">
                          {new Date(t.exit_time_iso).toLocaleTimeString()}
                        </td>
                        <td className="px-2 py-1.5 font-mono">{t.symbol}</td>
                        <td className="px-2 py-1.5 font-mono">{fmt(t.entry_price, 6)}</td>
                        <td className="px-2 py-1.5 font-mono">{fmt(t.exit_price, 6)}</td>
                        <td className="px-2 py-1.5 font-mono">{fmtTime(t.hold_sec)}</td>
                        <td className="px-2 py-1.5 font-mono">{fmt(t.gross_pnl_usdt, 4)}</td>
                        <td className="px-2 py-1.5 font-mono text-ink-muted">{fmt(t.fees_usdt, 4)}</td>
                        <td className={`px-2 py-1.5 font-mono font-medium ${t.net_pnl_usdt >= 0 ? "text-green-600" : "text-red-600"}`}>
                          {t.net_pnl_usdt >= 0 ? "+" : ""}{fmt(t.net_pnl_usdt, 4)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>
          )}

          {tab === "log" && (
            <div className="space-y-1 font-mono text-xs max-h-[400px] overflow-y-auto">
              {events.length === 0 ? (
                <p className="text-sm text-ink-muted py-8 text-center font-sans">Нет событий</p>
              ) : (
                [...events].reverse().map((ev, i) => (
                  <div key={i} className="flex gap-2 py-0.5 border-b border-line/30">
                    <span className="text-ink-muted shrink-0">
                      {ev.ts ? new Date(ev.ts).toLocaleTimeString() : "—"}
                    </span>
                    <span className={`font-medium ${
                      ev.type === "error" ? "text-red-500" :
                      ev.type === "position_opened" ? "text-green-600" :
                      ev.type === "position_closed" ? "text-blue-600" :
                      ev.type === "entry_signal" ? "text-amber-600" :
                      "text-ink"
                    }`}>
                      {ev.type}
                    </span>
                    <span className="text-ink-muted truncate">
                      {ev.type === "position_opened" && `bid=${fmt(ev.entry_price, 6)} qty=${fmt(ev.qty, 6)} spread=${fmt(ev.spread_bps)}bps`}
                      {ev.type === "position_closed" && `pnl=${fmt(ev.net_pnl_usdt, 4)}$ hold=${fmtTime(ev.hold_sec)} reason=${ev.reason}`}
                      {ev.type === "entry_signal" && `spread=${fmt(ev.spread_bps)}bps bid=${fmt(ev.bid, 6)} ask=${fmt(ev.ask, 6)}`}
                      {ev.type === "error" && ev.message}
                      {ev.type === "settings_updated" && JSON.stringify(ev.patch)}
                    </span>
                  </div>
                ))
              )}
            </div>
          )}
        </div>
      </>
    );

  if (pageMode) {
    return (
      <div className="flex w-full flex-col rounded-2xl border border-line bg-surface-elevated shadow-xl dark:shadow-panel-dark overflow-hidden">
        {content}
      </div>
    );
  }

  return (
    <div
      className="fixed inset-0 z-[80] flex items-center justify-center bg-black/50 p-4"
      role="dialog"
      aria-modal="true"
      onClick={onClose}
    >
      <div
        className="flex max-h-[90vh] w-full max-w-4xl flex-col rounded-2xl border border-line bg-surface-elevated shadow-xl dark:shadow-panel-dark overflow-hidden"
        onClick={(e) => e.stopPropagation()}
      >
        {content}
      </div>
    </div>
  );
}
