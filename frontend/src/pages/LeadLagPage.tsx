import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Activity,
  ArrowDown,
  ArrowUp,
  Clock,
  Radio,
  TrendingUp,
  Wifi,
  WifiOff,
} from "lucide-react";
import { apiUrl } from "../config";
import { SkeletonCard } from "../components/ui/Skeleton";

// ─── Types ─────────────────────────────────────────────────────────────────────

type SignalDirection = "long" | "short";
type SignalStatus = "active" | "resolved" | "expired";

interface LeadLagSignal {
  id: string;
  symbol: string;
  leader_exchange: string;
  lagger_exchange: string;
  direction: SignalDirection;
  z_score: number;
  entry_spread_bps: number;
  leader_mid_at_signal: number;
  lagger_mid_at_signal: number;
  estimated_lag_ms: number;
  status: SignalStatus;
  created_at: string;
  resolved_at: string | null;
  actual_lag_ms: number | null;
  exit_spread_bps: number | null;
  theoretical_pnl_bps: number | null;
}

interface LagEstimate {
  symbol: string;
  leader_exchange: string;
  lagger_exchange: string;
  lag_ms: number;
  correlation: number;
  confidence: number;
  sample_count: number;
  updated_at: string;
}

interface LeadLagStats {
  window_hours: number;
  total_signals: number;
  resolved_signals: number;
  expired_signals: number;
  win_rate: number;
  avg_lag_ms: number;
  median_lag_ms: number;
  avg_theoretical_pnl_bps: number;
  total_theoretical_pnl_bps: number;
  signals_per_hour: number;
  top_symbols: string[];
}

interface ConnectionInfo {
  connected: boolean;
  last_message_ms: number;
}

interface LeadLagStatus {
  running: boolean;
  connections: Record<string, ConnectionInfo>;
  symbols_monitored: string[];
  active_signals_count: number;
  uptime_sec: number;
}

interface ExchangePrice {
  exchange: string;
  mid: number;
  timestamp_ms: number;
}

// ─── Helpers ───────────────────────────────────────────────────────────────────

function fmt(n: number | null | undefined, digits: number): string {
  if (n == null || Number.isNaN(n)) return "—";
  return n.toLocaleString("ru-RU", {
    minimumFractionDigits: 0,
    maximumFractionDigits: digits,
  });
}

function ageSeconds(createdAt: string): number {
  const created = new Date(createdAt).getTime();
  if (Number.isNaN(created)) return 0;
  return Math.max(0, Math.floor((Date.now() - created) / 1000));
}

/** Color for lag value: green (low) → yellow (mid) → red (high) */
function lagColor(lagMs: number, maxMs: number): string {
  const ratio = Math.min(1, Math.max(0, lagMs / Math.max(maxMs, 1)));
  if (ratio < 0.33) return "bg-emerald-500/70";
  if (ratio < 0.66) return "bg-amber-500/70";
  return "bg-red-500/70";
}

// ─── Sub-components ────────────────────────────────────────────────────────────

function ConnectionStatus({
  status,
}: {
  status: LeadLagStatus | null;
}) {
  if (!status) return null;

  const now = Date.now();

  return (
    <div className="flex flex-wrap items-center gap-2">
      {Object.entries(status.connections).map(([exchange, info]) => {
        const ageSec = info.last_message_ms > 0
          ? (now - info.last_message_ms) / 1000
          : Infinity;

        let state: "connected" | "stale" | "disconnected";
        let colorClass: string;
        let Icon: typeof Wifi;

        if (!info.connected || ageSec > 30) {
          state = "disconnected";
          colorClass = "text-red-500";
          Icon = WifiOff;
        } else if (ageSec > 5) {
          state = "stale";
          colorClass = "text-amber-500";
          Icon = Wifi;
        } else {
          state = "connected";
          colorClass = "text-emerald-500";
          Icon = Wifi;
        }

        return (
          <div
            key={exchange}
            className={`flex items-center gap-1.5 rounded-md border border-line px-2 py-1 text-xs font-medium ${colorClass}`}
            title={`${exchange}: ${state} (${ageSec === Infinity ? "no data" : `${ageSec.toFixed(1)}s ago`})`}
          >
            <Icon className="h-3.5 w-3.5" />
            <span className="capitalize">{exchange}</span>
          </div>
        );
      })}
    </div>
  );
}

function SignalFeed({ signals }: { signals: LeadLagSignal[] }) {
  if (signals.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center py-8 text-ink-muted">
        <Radio className="mb-2 h-8 w-8 opacity-40" />
        <p className="text-sm">Нет активных сигналов</p>
      </div>
    );
  }

  return (
    <div className="overflow-auto scroll-thin">
      <table className="w-full text-left text-xs">
        <thead>
          <tr className="border-b border-line text-ink-muted">
            <th className="px-2 py-1.5 font-medium">Symbol</th>
            <th className="px-2 py-1.5 font-medium">Dir</th>
            <th className="px-2 py-1.5 font-medium">Z-score</th>
            <th className="px-2 py-1.5 font-medium">Spread (bps)</th>
            <th className="px-2 py-1.5 font-medium">Lag (ms)</th>
            <th className="px-2 py-1.5 font-medium">Age (s)</th>
          </tr>
        </thead>
        <tbody>
          {signals.map((s) => (
            <tr
              key={s.id}
              className="border-b border-line/50 transition hover:bg-accent/5"
            >
              <td className="px-2 py-1.5 font-mono font-medium text-ink">
                {s.symbol}
              </td>
              <td className="px-2 py-1.5">
                <span
                  className={`inline-flex items-center gap-0.5 font-medium ${
                    s.direction === "long"
                      ? "text-emerald-500"
                      : "text-red-500"
                  }`}
                >
                  {s.direction === "long" ? (
                    <ArrowUp className="h-3 w-3" />
                  ) : (
                    <ArrowDown className="h-3 w-3" />
                  )}
                  {s.direction}
                </span>
              </td>
              <td className="px-2 py-1.5 font-mono text-accent">
                {fmt(s.z_score, 2)}
              </td>
              <td className="px-2 py-1.5 font-mono">
                {fmt(s.entry_spread_bps, 2)}
              </td>
              <td className="px-2 py-1.5 font-mono">
                {fmt(s.estimated_lag_ms, 0)}
              </td>
              <td className="px-2 py-1.5 font-mono text-ink-muted">
                {ageSeconds(s.created_at)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function LagHeatmap({ estimates }: { estimates: LagEstimate[] }) {
  if (estimates.length === 0) {
    return (
      <div className="flex items-center justify-center py-6 text-sm text-ink-muted">
        Нет данных lag-оценок
      </div>
    );
  }

  // Build matrix: exchanges (rows) × symbols (columns)
  const exchanges = [...new Set(estimates.map((e) => e.lagger_exchange))].sort();
  const symbols = [...new Set(estimates.map((e) => e.symbol))].sort();
  const maxLag = Math.max(...estimates.map((e) => e.lag_ms), 1);

  const lagMap = new Map<string, number>();
  for (const est of estimates) {
    lagMap.set(`${est.lagger_exchange}:${est.symbol}`, est.lag_ms);
  }

  return (
    <div className="overflow-auto scroll-thin">
      <table className="w-full text-xs">
        <thead>
          <tr className="border-b border-line">
            <th className="px-2 py-1.5 text-left font-medium text-ink-muted">
              Exchange
            </th>
            {symbols.map((sym) => (
              <th
                key={sym}
                className="px-2 py-1.5 text-center font-medium text-ink-muted"
              >
                {sym}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {exchanges.map((exch) => (
            <tr key={exch} className="border-b border-line/50">
              <td className="px-2 py-1.5 font-medium capitalize text-ink">
                {exch}
              </td>
              {symbols.map((sym) => {
                const lag = lagMap.get(`${exch}:${sym}`);
                return (
                  <td key={sym} className="px-1 py-1">
                    {lag != null ? (
                      <div
                        className={`mx-auto flex h-7 w-full max-w-[60px] items-center justify-center rounded text-[10px] font-bold text-white ${lagColor(lag, maxLag)}`}
                        title={`${exch} / ${sym}: ${lag.toFixed(0)} ms`}
                      >
                        {lag.toFixed(0)}
                      </div>
                    ) : (
                      <div className="mx-auto flex h-7 w-full max-w-[60px] items-center justify-center rounded bg-line/40 text-[10px] text-ink-muted">
                        —
                      </div>
                    )}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function StatsPanel({
  stats,
  windowHours,
  onWindowChange,
}: {
  stats: LeadLagStats | null;
  windowHours: number;
  onWindowChange: (h: number) => void;
}) {
  const periods = [1, 6, 24] as const;

  return (
    <div>
      {/* Period selector */}
      <div className="mb-3 flex items-center gap-1">
        {periods.map((p) => (
          <button
            key={p}
            type="button"
            onClick={() => onWindowChange(p)}
            className={`rounded-md px-2.5 py-1 text-xs font-medium transition ${
              windowHours === p
                ? "bg-accent/15 text-accent"
                : "text-ink-muted hover:bg-accent/5 hover:text-ink"
            }`}
          >
            {p}h
          </button>
        ))}
      </div>

      {/* Stats grid */}
      {stats ? (
        <div className="grid grid-cols-2 gap-3">
          <StatCard
            label="Win Rate"
            value={`${(stats.win_rate * 100).toFixed(1)}%`}
            accent={stats.win_rate > 0.5}
          />
          <StatCard
            label="Avg Lag"
            value={`${fmt(stats.avg_lag_ms, 0)} ms`}
          />
          <StatCard
            label="Total PnL"
            value={`${fmt(stats.total_theoretical_pnl_bps, 1)} bps`}
            accent={stats.total_theoretical_pnl_bps > 0}
          />
          <StatCard
            label="Signals"
            value={String(stats.total_signals)}
          />
        </div>
      ) : (
        <div className="grid grid-cols-2 gap-3">
          <SkeletonCard />
          <SkeletonCard />
          <SkeletonCard />
          <SkeletonCard />
        </div>
      )}
    </div>
  );
}

function StatCard({
  label,
  value,
  accent,
}: {
  label: string;
  value: string;
  accent?: boolean;
}) {
  return (
    <div className="rounded-lg border border-line bg-surface-elevated px-3 py-2">
      <p className="text-[10px] font-medium uppercase tracking-wide text-ink-muted">
        {label}
      </p>
      <p
        className={`mt-0.5 text-lg font-bold ${
          accent ? "text-emerald-500" : "text-ink"
        }`}
      >
        {value}
      </p>
    </div>
  );
}

function PriceComparisonChart({
  prices,
  symbol,
}: {
  prices: ExchangePrice[];
  symbol: string;
}) {
  if (prices.length === 0) {
    return (
      <div className="flex items-center justify-center py-6 text-sm text-ink-muted">
        Выберите символ для сравнения цен
      </div>
    );
  }

  // Simple bar-style comparison of current mid prices across exchanges
  const maxPrice = Math.max(...prices.map((p) => p.mid));
  const minPrice = Math.min(...prices.map((p) => p.mid));
  const range = maxPrice - minPrice || 1;

  return (
    <div>
      <p className="mb-2 text-xs font-medium text-ink-muted">
        Mid-цены: {symbol}
      </p>
      <div className="space-y-2">
        {prices.map((p) => {
          const pct = range > 0 ? ((p.mid - minPrice) / range) * 100 : 50;
          return (
            <div key={p.exchange} className="flex items-center gap-2">
              <span className="w-16 shrink-0 text-xs font-medium capitalize text-ink">
                {p.exchange}
              </span>
              <div className="relative h-5 flex-1 overflow-hidden rounded bg-line/40">
                <div
                  className="absolute inset-y-0 left-0 rounded bg-accent/60 transition-all"
                  style={{ width: `${Math.max(5, pct)}%` }}
                />
              </div>
              <span className="w-24 shrink-0 text-right font-mono text-xs text-ink">
                {fmt(p.mid, 4)}
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ─── Main Page ─────────────────────────────────────────────────────────────────

export function LeadLagPage() {
  const [status, setStatus] = useState<LeadLagStatus | null>(null);
  const [signals, setSignals] = useState<LeadLagSignal[]>([]);
  const [stats, setStats] = useState<LeadLagStats | null>(null);
  const [estimates, setEstimates] = useState<LagEstimate[]>([]);
  const [prices, setPrices] = useState<ExchangePrice[]>([]);
  const [selectedSymbol, setSelectedSymbol] = useState<string>("");
  const [windowHours, setWindowHours] = useState(24);
  const [error, setError] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);

  const abortRef = useRef<AbortController | null>(null);
  const lastSuccessRef = useRef<number>(Date.now());

  // ─── Fetch helpers ─────────────────────────────────────────────────────────

  /** Create a combined signal that aborts on parent signal OR 10s timeout */
  function withTimeout(parentSignal?: AbortSignal): AbortSignal {
    if (typeof AbortSignal.any === "function") {
      return AbortSignal.any([
        ...(parentSignal ? [parentSignal] : []),
        AbortSignal.timeout(10_000),
      ]);
    }
    // Fallback for environments without AbortSignal.any
    return parentSignal ?? new AbortController().signal;
  }

  const fetchStatus = useCallback(async (signal?: AbortSignal) => {
    try {
      const r = await fetch(apiUrl("/api/lead-lag/status"), {
        signal: withTimeout(signal),
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const data: LeadLagStatus = await r.json();
      setStatus(data);
      setError(null);
      lastSuccessRef.current = Date.now();
      return data;
    } catch (e: any) {
      if (e.name !== "AbortError") {
        if (e.name === "TimeoutError" || Date.now() - lastSuccessRef.current > 10_000) {
          setError("Ошибка соединения с backend");
        } else {
          setError("Ошибка соединения с backend");
        }
      }
      return null;
    }
  }, []);

  const fetchSignals = useCallback(async (signal?: AbortSignal) => {
    try {
      const r = await fetch(
        apiUrl("/api/lead-lag/signals?active=true&limit=50"),
        { signal: withTimeout(signal) },
      );
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const data: LeadLagSignal[] = await r.json();
      setSignals(data);
      lastSuccessRef.current = Date.now();
    } catch (e: any) {
      if (e.name !== "AbortError") setError("Ошибка загрузки сигналов");
    }
  }, []);

  const fetchStats = useCallback(
    async (signal?: AbortSignal) => {
      try {
        const r = await fetch(
          apiUrl(`/api/lead-lag/stats?window_hours=${windowHours}`),
          { signal: withTimeout(signal) },
        );
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        const data: LeadLagStats = await r.json();
        setStats(data);
        lastSuccessRef.current = Date.now();
      } catch (e: any) {
        if (e.name !== "AbortError") setError("Ошибка загрузки статистики");
      }
    },
    [windowHours],
  );

  const fetchEstimates = useCallback(async (signal?: AbortSignal) => {
    try {
      const r = await fetch(apiUrl("/api/lead-lag/lag-estimates"), {
        signal: withTimeout(signal),
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const data: LagEstimate[] = await r.json();
      setEstimates(data);
      lastSuccessRef.current = Date.now();
    } catch (e: any) {
      if (e.name !== "AbortError") {
        /* silent */
      }
    }
  }, []);

  const fetchPrices = useCallback(
    async (signal?: AbortSignal) => {
      if (!selectedSymbol) {
        setPrices([]);
        return;
      }
      try {
        const r = await fetch(
          apiUrl(`/api/lead-lag/prices?symbol=${encodeURIComponent(selectedSymbol)}`),
          { signal: withTimeout(signal) },
        );
        if (!r.ok) {
          setPrices([]);
          return;
        }
        const data = await r.json();
        // API returns { symbol, prices: { exchange: mid_price } }
        const pricesObj = data.prices;
        let arr: ExchangePrice[] = [];
        if (pricesObj && typeof pricesObj === "object" && !Array.isArray(pricesObj)) {
          arr = Object.entries(pricesObj).map(([exchange, mid]) => ({
            exchange,
            mid: mid as number,
            timestamp_ms: Date.now(),
          }));
        } else if (Array.isArray(pricesObj)) {
          arr = pricesObj;
        }
        setPrices(arr);
        lastSuccessRef.current = Date.now();
      } catch (e: any) {
        if (e.name !== "AbortError") setPrices([]);
      }
    },
    [selectedSymbol],
  );

  // ─── Polling ───────────────────────────────────────────────────────────────

  useEffect(() => {
    const ctrl = new AbortController();
    abortRef.current = ctrl;

    // Initial fetch
    fetchStatus(ctrl.signal);
    fetchSignals(ctrl.signal);
    fetchEstimates(ctrl.signal);
    fetchStats(ctrl.signal);
    fetchPrices(ctrl.signal);

    // Poll signals + status every 3s
    const fastInterval = setInterval(() => {
      fetchStatus(ctrl.signal);
      fetchSignals(ctrl.signal);
      fetchEstimates(ctrl.signal);
      fetchPrices(ctrl.signal);
    }, 3000);

    // Poll stats every 10s
    const slowInterval = setInterval(() => {
      fetchStats(ctrl.signal);
    }, 10000);

    return () => {
      ctrl.abort();
      clearInterval(fastInterval);
      clearInterval(slowInterval);
    };
  }, [fetchStatus, fetchSignals, fetchEstimates, fetchStats, fetchPrices]);

  // Re-fetch stats when window changes
  useEffect(() => {
    const ctrl = new AbortController();
    fetchStats(ctrl.signal);
    return () => ctrl.abort();
  }, [fetchStats]);

  // ─── Start engine ──────────────────────────────────────────────────────────

  const handleStart = useCallback(async () => {
    setStarting(true);
    try {
      const r = await fetch(apiUrl("/api/lead-lag/start"), { method: "POST" });
      if (r.ok) {
        await fetchStatus();
      }
    } catch {
      setError("Не удалось запустить движок");
    } finally {
      setStarting(false);
    }
  }, [fetchStatus]);

  // ─── Symbol selector ───────────────────────────────────────────────────────

  const availableSymbols = useMemo(() => {
    return status?.symbols_monitored ?? [];
  }, [status]);

  // Auto-select first symbol if none selected
  useEffect(() => {
    if (!selectedSymbol && availableSymbols.length > 0) {
      setSelectedSymbol(availableSymbols[0]);
    }
  }, [availableSymbols, selectedSymbol]);

  // ─── Render ────────────────────────────────────────────────────────────────

  const isRunning = status?.running ?? false;

  return (
    <div className="flex h-full flex-col overflow-hidden">
      {/* Header */}
      <header className="flex shrink-0 items-center justify-between border-b border-line px-4 py-3">
        <div className="flex items-center gap-3">
          <Activity className="h-5 w-5 text-accent" />
          <h1 className="text-base font-bold text-ink">Lead-Lag Arbitrage</h1>
          {isRunning && (
            <span className="rounded-full bg-emerald-500/15 px-2 py-0.5 text-[10px] font-bold uppercase text-emerald-600 dark:text-emerald-400">
              Running
            </span>
          )}
        </div>
        <ConnectionStatus status={status} />
      </header>

      {/* Error banner */}
      {error && (
        <div className="shrink-0 border-b border-red-500/30 bg-red-500/10 px-4 py-2 text-xs font-medium text-red-600 dark:text-red-400">
          {error}
        </div>
      )}

      {/* Engine not running */}
      {status && !isRunning && (
        <div className="flex shrink-0 items-center gap-3 border-b border-amber-500/30 bg-amber-500/10 px-4 py-3">
          <Clock className="h-4 w-4 text-amber-600 dark:text-amber-400" />
          <span className="text-sm text-amber-700 dark:text-amber-300">
            Движок Lead-Lag не запущен
          </span>
          <button
            type="button"
            onClick={handleStart}
            disabled={starting}
            className="ml-auto rounded-md bg-accent px-3 py-1 text-xs font-medium text-white transition hover:bg-accent/80 disabled:opacity-50"
          >
            {starting ? "Запуск..." : "Запустить"}
          </button>
        </div>
      )}

      {/* Main content */}
      <div className="flex-1 overflow-auto scroll-thin p-4">
        <div className="grid gap-4 lg:grid-cols-3">
          {/* Left column: Signal Feed */}
          <div className="lg:col-span-2">
            <section className="rounded-xl border border-line bg-surface-elevated p-4">
              <div className="mb-3 flex items-center gap-2">
                <TrendingUp className="h-4 w-4 text-accent" />
                <h2 className="text-sm font-bold text-ink">
                  Активные сигналы
                </h2>
                <span className="ml-auto text-xs text-ink-muted">
                  {signals.length} / 50
                </span>
              </div>
              <SignalFeed signals={signals} />
            </section>

            {/* Lag Heatmap */}
            <section className="mt-4 rounded-xl border border-line bg-surface-elevated p-4">
              <h2 className="mb-3 text-sm font-bold text-ink">
                Lag Heatmap (ms)
              </h2>
              <LagHeatmap estimates={estimates} />
            </section>

            {/* Price Comparison */}
            <section className="mt-4 rounded-xl border border-line bg-surface-elevated p-4">
              <div className="mb-3 flex items-center gap-2">
                <h2 className="text-sm font-bold text-ink">
                  Сравнение цен
                </h2>
                {availableSymbols.length > 0 && (
                  <select
                    value={selectedSymbol}
                    onChange={(e) => setSelectedSymbol(e.target.value)}
                    className="ml-auto rounded-md border border-line bg-surface px-2 py-1 text-xs text-ink"
                  >
                    {availableSymbols.map((sym) => (
                      <option key={sym} value={sym}>
                        {sym}
                      </option>
                    ))}
                  </select>
                )}
              </div>
              <PriceComparisonChart prices={prices} symbol={selectedSymbol} />
            </section>
          </div>

          {/* Right column: Stats */}
          <div>
            <section className="rounded-xl border border-line bg-surface-elevated p-4">
              <h2 className="mb-3 text-sm font-bold text-ink">Статистика</h2>
              <StatsPanel
                stats={stats}
                windowHours={windowHours}
                onWindowChange={setWindowHours}
              />
            </section>
          </div>
        </div>
      </div>
    </div>
  );
}
