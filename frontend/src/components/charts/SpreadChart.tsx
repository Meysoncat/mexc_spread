import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { LineSeries } from "lightweight-charts";
import type { IChartApi, ISeriesApi, UTCTimestamp } from "lightweight-charts";
import { apiUrl } from "../../config";
import type { SpreadTick } from "../../types";
import { ChartCore } from "./ChartCore";

type TimeRange = "1m" | "5m" | "15m" | "30m";

const TIME_RANGES: { value: TimeRange; label: string; seconds: number }[] = [
  { value: "1m", label: "1м", seconds: 60 },
  { value: "5m", label: "5м", seconds: 300 },
  { value: "15m", label: "15м", seconds: 900 },
  { value: "30m", label: "30м", seconds: 1800 },
];

export interface SpreadChartProps {
  symbol: string;
  market: "spot" | "futures";
  timeRange?: TimeRange;
  className?: string;
  mode?: "dark" | "light";
}

export function SpreadChart({
  symbol,
  timeRange = "5m",
  className,
  mode,
}: SpreadChartProps) {
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<"Line"> | null>(null);
  const eventSourceRef = useRef<EventSource | null>(null);
  const [chartReadyTick, setChartReadyTick] = useState(0);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [connected, setConnected] = useState(false);

  const handleChartReady = useCallback((chart: IChartApi) => {
    chartRef.current = chart;
    setChartReadyTick((t) => t + 1);
  }, []);

  const chartOptions = useMemo(
    () => ({
      rightPriceScale: {
        scaleMargins: { top: 0.1, bottom: 0.1 },
      },
      timeScale: {
        timeVisible: true,
        secondsVisible: true,
      },
    }),
    [],
  );

  const loadHistory = useCallback(
    (chart: IChartApi) => {
      let cancelled = false;
      setLoading(true);
      setErr(null);

      if (seriesRef.current) {
        try {
          chart.removeSeries(seriesRef.current);
        } catch {
          /* chart may already be destroyed */
        }
        seriesRef.current = null;
      }

      const range = TIME_RANGES.find((r) => r.value === timeRange);
      const sinceMs = Date.now() - (range?.seconds ?? 300) * 1000;

      fetch(
        apiUrl(
          `/api/spread/history?symbol=${encodeURIComponent(symbol)}&since_ms=${sinceMs}&max_points=2000`,
        ),
      )
        .then((r) => r.json())
        .then((data) => {
          if (cancelled) return;
          if (!data.ok || !data.ticks) {
            setErr(data.error ?? "Ошибка загрузки истории");
            return;
          }

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
        .catch((e) => {
          if (!cancelled) setErr(e instanceof Error ? e.message : String(e));
        })
        .finally(() => {
          if (!cancelled) setLoading(false);
        });

      return () => {
        cancelled = true;
        if (seriesRef.current) {
          try {
            chart.removeSeries(seriesRef.current);
          } catch {
            /* chart may already be destroyed */
          }
          seriesRef.current = null;
        }
      };
    },
    [symbol, timeRange],
  );

  useEffect(() => {
    const chart = chartRef.current;
    if (!chart) return;
    return loadHistory(chart);
  }, [chartReadyTick, loadHistory]);

  useEffect(() => {
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
        if (seriesRef.current && tick.spread_bps != null) {
          seriesRef.current.update({
            time: (tick.timestamp_ms / 1000) as UTCTimestamp,
            value: tick.spread_bps,
          });
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
  }, [symbol]);

  return (
    <div className="relative h-full w-full">
      <div className="absolute left-3 top-3 z-10 flex items-center gap-2">
        {loading && (
          <span className="text-sm text-ink-muted">Загрузка…</span>
        )}
        {err && (
          <span className="rounded bg-surface-elevated/95 px-2 py-1 text-sm text-red-600 dark:text-red-400">
            {err}
          </span>
        )}
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
      </div>
      <ChartCore
        mode={mode}
        onChartReady={handleChartReady}
        className={className ?? "h-full w-full min-h-[280px]"}
        options={chartOptions}
      />
    </div>
  );
}
