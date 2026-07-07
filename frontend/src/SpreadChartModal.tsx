import { useEffect, useLayoutEffect, useRef, useState, useCallback } from "react";
import {
  ColorType,
  createChart,
  LineSeries,
  type IChartApi,
  type ISeriesApi,
  type UTCTimestamp,
} from "lightweight-charts";
import { X, Activity, TrendingUp, TrendingDown, BarChart3 } from "lucide-react";
import { apiUrl } from "./config";
import type { SpreadTick, SpreadStats } from "./types";

interface SpreadChartModalProps {
  open: boolean;
  onClose: () => void;
  symbol: string | null;
  market: "spot" | "futures";
  isDark: boolean;
}

type TimeRange = "1m" | "5m" | "15m" | "30m";

const TIME_RANGES: { value: TimeRange; label: string; seconds: number }[] = [
  { value: "1m", label: "1м", seconds: 60 },
  { value: "5m", label: "5м", seconds: 300 },
  { value: "15m", label: "15м", seconds: 900 },
  { value: "30m", label: "30м", seconds: 1800 },
];

function formatBps(v: number | null | undefined): string {
  if (v == null) return "—";
  return v.toFixed(2);
}

function formatPrice(v: number | null | undefined): string {
  if (v == null) return "—";
  if (v >= 1000) return v.toFixed(2);
  if (v >= 1) return v.toFixed(4);
  if (v >= 0.01) return v.toFixed(6);
  return v.toFixed(8);
}

export function SpreadChartModal({
  open,
  onClose,
  symbol,
  market: _market,
  isDark,
}: SpreadChartModalProps) {
  const chartContainerRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<"Line"> | null>(null);
  const eventSourceRef = useRef<EventSource | null>(null);

  const [timeRange, setTimeRange] = useState<TimeRange>("5m");
  const [stats, setStats] = useState<SpreadStats | null>(null);
  const [connected, setConnected] = useState(false);
  const [lastTick, setLastTick] = useState<SpreadTick | null>(null);
  const [entryThresholdBps, setEntryThresholdBps] = useState(5.0);
  const [exitThresholdBps, setExitThresholdBps] = useState(1.0);
  const [alertEnabled, setAlertEnabled] = useState(false);
  const [alertFired, setAlertFired] = useState(false);

  const alertEnabledRef = useRef(alertEnabled);
  alertEnabledRef.current = alertEnabled;
  const entryThresholdRef = useRef(entryThresholdBps);
  entryThresholdRef.current = entryThresholdBps;

  // Fetch stats periodically
  const fetchStats = useCallback(async () => {
    if (!symbol) return;
    const range = TIME_RANGES.find((r) => r.value === timeRange);
    const periodSec = range?.seconds ?? 300;
    try {
      const r = await fetch(
        apiUrl(
          `/api/spread/stats?symbol=${encodeURIComponent(symbol)}&period_sec=${periodSec}&threshold_bps=${entryThresholdBps}`,
        ),
      );
      if (r.ok) {
        const data = await r.json();
        if (data.ok && data.stats) {
          setStats(data.stats);
        }
      }
    } catch {
      /* ignore */
    }
  }, [symbol, timeRange, entryThresholdBps]);

  // Load initial history and setup chart
  useLayoutEffect(() => {
    if (!open || !symbol) return;
    const el = chartContainerRef.current;
    if (!el) return;

    const bg = isDark ? "#1e293b" : "#ffffff";
    const fg = isDark ? "#e2e8f0" : "#0f172a";
    const grid = isDark ? "#334155" : "#e2e8f0";

    const chart = createChart(el, {
      layout: {
        background: { type: ColorType.Solid, color: bg },
        textColor: fg,
      },
      grid: {
        vertLines: { color: grid },
        horzLines: { color: grid },
      },
      rightPriceScale: {
        borderColor: grid,
        autoScale: true,
        scaleMargins: { top: 0.1, bottom: 0.1 },
      },
      timeScale: {
        borderColor: grid,
        timeVisible: true,
        secondsVisible: true,
      },
      width: el.clientWidth,
      height: el.clientHeight,
    });
    chartRef.current = chart;

    const series = chart.addSeries(LineSeries, {
      color: "#f59e0b",
      lineWidth: 2,
      priceFormat: {
        type: "price",
        precision: 2,
        minMove: 0.01,
      },
      priceLineVisible: true,
      lastValueVisible: true,
      title: "Spread (bps)",
    });
    seriesRef.current = series;

    const ro = new ResizeObserver(() => {
      if (!chartContainerRef.current) return;
      chart.applyOptions({
        width: chartContainerRef.current.clientWidth,
        height: chartContainerRef.current.clientHeight,
      });
    });
    ro.observe(el);

    // Load history
    const range = TIME_RANGES.find((r) => r.value === timeRange);
    const sinceMs = Date.now() - (range?.seconds ?? 300) * 1000;

    fetch(
      apiUrl(
        `/api/spread/history?symbol=${encodeURIComponent(symbol)}&since_ms=${sinceMs}&max_points=2000`,
      ),
    )
      .then((r) => r.json())
      .then((data) => {
        if (!data.ok || !data.ticks) return;
        const lineData = data.ticks
          .filter((t: SpreadTick) => t.spread_bps != null)
          .map((t: SpreadTick) => ({
            time: (t.timestamp_ms / 1000) as UTCTimestamp,
            value: t.spread_bps as number,
          }));
        if (lineData.length > 0) {
          series.setData(lineData);
          chart.timeScale().fitContent();
        }
      })
      .catch(() => {});

    return () => {
      ro.disconnect();
      chart.remove();
      chartRef.current = null;
      seriesRef.current = null;
    };
  }, [open, symbol, isDark, timeRange]);

  // SSE connection for real-time updates
  useEffect(() => {
    if (!open || !symbol) return;

    const url = apiUrl(
      `/api/spread/stream?symbol=${encodeURIComponent(symbol)}`,
    );
    const es = new EventSource(url);
    eventSourceRef.current = es;

    es.onopen = () => setConnected(true);
    es.onerror = () => setConnected(false);

    es.onmessage = (event) => {
      try {
        const tick: SpreadTick = JSON.parse(event.data);
        setLastTick(tick);

        // Update chart
        if (seriesRef.current && tick.spread_bps != null) {
          seriesRef.current.update({
            time: (tick.timestamp_ms / 1000) as UTCTimestamp,
            value: tick.spread_bps,
          });
        }

        // Alert check
        if (
          alertEnabledRef.current &&
          tick.spread_bps != null &&
          tick.spread_bps >= entryThresholdRef.current
        ) {
          setAlertFired(true);
          try {
            // Звуковой сигнал
            const ctx = new AudioContext();
            const osc = ctx.createOscillator();
            const gain = ctx.createGain();
            osc.connect(gain);
            gain.connect(ctx.destination);
            osc.frequency.value = 880;
            gain.gain.value = 0.3;
            osc.start();
            osc.stop(ctx.currentTime + 0.15);
          } catch {
            /* audio not available */
          }
        } else {
          setAlertFired(false);
        }
      } catch {
        /* ignore parse errors */
      }
    };

    return () => {
      es.close();
      eventSourceRef.current = null;
      setConnected(false);
    };
  }, [open, symbol]);

  // Fetch stats on interval
  useEffect(() => {
    if (!open || !symbol) return;
    fetchStats();
    const id = window.setInterval(fetchStats, 5000);
    return () => window.clearInterval(id);
  }, [open, symbol, fetchStats]);

  // Escape to close
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open || !symbol) return null;

  return (
    <div
      className="fixed inset-0 z-[70] flex items-center justify-center bg-black/50 p-4"
      role="dialog"
      aria-modal="true"
      aria-labelledby="spread-chart-title"
      onClick={onClose}
    >
      <div
        className="flex max-h-[92vh] w-full max-w-6xl flex-col rounded-2xl border border-line bg-surface-elevated shadow-xl dark:shadow-panel-dark"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-line px-4 py-3">
          <div className="flex items-center gap-3">
            <h2
              id="spread-chart-title"
              className="text-lg font-semibold text-ink"
            >
              <Activity className="inline h-5 w-5 mr-1 text-amber-500" />
              Спред {symbol}
            </h2>
            <span
              className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium ${
                connected
                  ? "bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400"
                  : "bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400"
              }`}
            >
              <span
                className={`h-1.5 w-1.5 rounded-full ${connected ? "bg-green-500" : "bg-red-500"}`}
              />
              {connected ? "Live" : "Offline"}
            </span>
            {alertFired && (
              <span className="animate-pulse rounded-full bg-amber-100 px-2 py-0.5 text-xs font-bold text-amber-700 dark:bg-amber-900/30 dark:text-amber-400">
                ⚡ СИГНАЛ
              </span>
            )}
          </div>
          <div className="flex items-center gap-2">
            {TIME_RANGES.map((r) => (
              <button
                key={r.value}
                onClick={() => setTimeRange(r.value)}
                className={`rounded-lg px-2.5 py-1 text-xs font-medium transition ${
                  timeRange === r.value
                    ? "bg-amber-500 text-white"
                    : "bg-surface text-ink-muted hover:bg-surface-hover border border-line"
                }`}
              >
                {r.label}
              </button>
            ))}
            <button
              type="button"
              onClick={onClose}
              className="rounded-lg border border-line p-2 text-ink transition hover:bg-surface"
              aria-label="Закрыть"
            >
              <X className="h-5 w-5" />
            </button>
          </div>
        </div>

        {/* Body */}
        <div className="flex flex-1 overflow-hidden">
          {/* Chart area */}
          <div className="flex-1 p-2">
            <div
              ref={chartContainerRef}
              className="h-[min(50vh,450px)] w-full min-h-[280px]"
            />
          </div>

          {/* Side panel */}
          <div className="w-72 shrink-0 border-l border-line overflow-y-auto p-3 space-y-4">
            {/* Current values */}
            <div className="space-y-2">
              <h3 className="text-xs font-semibold uppercase text-ink-muted tracking-wide">
                Текущие значения
              </h3>
              <div className="grid grid-cols-2 gap-2 text-sm">
                <div className="rounded-lg bg-surface p-2">
                  <div className="text-xs text-ink-muted">Bid</div>
                  <div className="font-mono font-medium text-green-600 dark:text-green-400">
                    {formatPrice(lastTick?.bid)}
                  </div>
                </div>
                <div className="rounded-lg bg-surface p-2">
                  <div className="text-xs text-ink-muted">Ask</div>
                  <div className="font-mono font-medium text-red-600 dark:text-red-400">
                    {formatPrice(lastTick?.ask)}
                  </div>
                </div>
                <div className="rounded-lg bg-surface p-2 col-span-2">
                  <div className="text-xs text-ink-muted">Спред (bps)</div>
                  <div className="font-mono text-lg font-bold text-amber-600 dark:text-amber-400">
                    {formatBps(lastTick?.spread_bps)}
                  </div>
                </div>
              </div>
            </div>

            {/* Stats */}
            {stats && (
              <div className="space-y-2">
                <h3 className="text-xs font-semibold uppercase text-ink-muted tracking-wide">
                  <BarChart3 className="inline h-3.5 w-3.5 mr-1" />
                  Статистика ({TIME_RANGES.find((r) => r.value === timeRange)?.label})
                </h3>
                <div className="space-y-1 text-xs">
                  <div className="flex justify-between">
                    <span className="text-ink-muted">Средний</span>
                    <span className="font-mono">{formatBps(stats.avg_spread_bps)} bps</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-ink-muted flex items-center gap-1">
                      <TrendingDown className="h-3 w-3 text-green-500" /> Мин
                    </span>
                    <span className="font-mono">{formatBps(stats.min_spread_bps)} bps</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-ink-muted flex items-center gap-1">
                      <TrendingUp className="h-3 w-3 text-red-500" /> Макс
                    </span>
                    <span className="font-mono">{formatBps(stats.max_spread_bps)} bps</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-ink-muted">Std Dev</span>
                    <span className="font-mono">{formatBps(stats.std_spread_bps)} bps</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-ink-muted">Тиков</span>
                    <span className="font-mono">{stats.ticks_count}</span>
                  </div>
                  {stats.pct_above_threshold != null && (
                    <div className="flex justify-between">
                      <span className="text-ink-muted">
                        Выше порога
                      </span>
                      <span className="font-mono font-medium text-amber-600 dark:text-amber-400">
                        {stats.pct_above_threshold.toFixed(1)}%
                      </span>
                    </div>
                  )}
                </div>
              </div>
            )}

            {/* Thresholds */}
            <div className="space-y-2">
              <h3 className="text-xs font-semibold uppercase text-ink-muted tracking-wide">
                Пороги (bps)
              </h3>
              <div className="space-y-2">
                <label className="block">
                  <span className="text-xs text-ink-muted">Вход (≥)</span>
                  <input
                    type="number"
                    step="0.5"
                    min="0"
                    value={entryThresholdBps}
                    onChange={(e) =>
                      setEntryThresholdBps(Math.max(0, parseFloat(e.target.value) || 0))
                    }
                    className="mt-0.5 w-full rounded-lg border border-line bg-surface px-2 py-1.5 text-sm font-mono text-ink outline-none focus:ring-2 focus:ring-amber-500"
                  />
                </label>
                <label className="block">
                  <span className="text-xs text-ink-muted">Выход (≤)</span>
                  <input
                    type="number"
                    step="0.5"
                    min="0"
                    value={exitThresholdBps}
                    onChange={(e) =>
                      setExitThresholdBps(Math.max(0, parseFloat(e.target.value) || 0))
                    }
                    className="mt-0.5 w-full rounded-lg border border-line bg-surface px-2 py-1.5 text-sm font-mono text-ink outline-none focus:ring-2 focus:ring-amber-500"
                  />
                </label>
              </div>
            </div>

            {/* Alert toggle */}
            <div className="space-y-2">
              <label className="flex items-center gap-2 cursor-pointer">
                <input
                  type="checkbox"
                  checked={alertEnabled}
                  onChange={(e) => setAlertEnabled(e.target.checked)}
                  className="h-4 w-4 rounded border-line text-amber-500 focus:ring-amber-500"
                />
                <span className="text-sm text-ink">
                  🔔 Звуковой алерт при входе
                </span>
              </label>
              <p className="text-xs text-ink-muted">
                Сигнал при spread ≥ порога входа
              </p>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
