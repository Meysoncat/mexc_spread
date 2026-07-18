import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { LineSeries } from "lightweight-charts";
import type { IChartApi, ISeriesApi, UTCTimestamp } from "lightweight-charts";
import { apiUrl } from "../../config";
import { ChartCore } from "./ChartCore";

export interface BasisChartProps {
  symbol: string;
  exchangeCombo: string;
  entryThresholdBps?: number;
  exitThresholdBps?: number;
  className?: string;
  mode?: "dark" | "light";
}

type Interval = "1h" | "4h" | "24h" | "7d";

const INTERVAL_HOURS: Record<Interval, number> = {
  "1h": 1,
  "4h": 4,
  "24h": 24,
  "7d": 168,
};

export function BasisChart({
  symbol,
  exchangeCombo,
  entryThresholdBps = 30,
  exitThresholdBps = 5,
  className,
  mode,
}: BasisChartProps) {
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<"Line"> | null>(null);
  const [chartReadyTick, setChartReadyTick] = useState(0);
  const [interval, setInterval] = useState<Interval>("24h");
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const handleChartReady = useCallback((chart: IChartApi) => {
    chartRef.current = chart;
    setChartReadyTick((t) => t + 1);
  }, []);

  const chartOptions = useMemo(
    () => ({
      timeScale: {
        timeVisible: true,
        secondsVisible: false,
      },
      crosshair: {
        mode: 0,
      },
    }),
    [],
  );

  const loadData = useCallback(
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

      const hours = INTERVAL_HOURS[interval];
      const since = new Date(Date.now() - hours * 3600 * 1000).toISOString();

      fetch(
        apiUrl(
          `/api/futures-arb/basis-history?symbol=${encodeURIComponent(symbol)}&exchange_combo=${encodeURIComponent(exchangeCombo)}&since=${encodeURIComponent(since)}&limit=2000`,
        ),
      )
        .then((r) => r.json())
        .then((d) => {
          if (cancelled) return;
          if (!d.ok) {
            setErr(d.error ?? "Ошибка загрузки");
            return;
          }

          const rows: { timestamp: string; basis_bps: number }[] = d.rows ?? [];
          const data = rows
            .sort(
              (a: { timestamp: string }, b: { timestamp: string }) =>
                a.timestamp.localeCompare(b.timestamp),
            )
            .map(
              (row: { timestamp: string; basis_bps: number }) => ({
                time: (new Date(row.timestamp).getTime() / 1000) as UTCTimestamp,
                value: row.basis_bps,
              }),
            );

          const series = chart.addSeries(LineSeries, {
            color: "#4fc3f7",
            lineWidth: 2,
            priceFormat: {
              type: "custom",
              formatter: (v: number) => v.toFixed(1) + " bps",
            },
          });

          series.setData(data);
          seriesRef.current = series;

          series.createPriceLine({
            price: entryThresholdBps,
            color: "#4caf50",
            lineWidth: 1,
            lineStyle: 2,
            axisLabelVisible: true,
            title: "Entry",
          });
          series.createPriceLine({
            price: exitThresholdBps,
            color: "#ff9800",
            lineWidth: 1,
            lineStyle: 2,
            axisLabelVisible: true,
            title: "Exit",
          });

          chart.timeScale().fitContent();
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
    [symbol, exchangeCombo, interval, entryThresholdBps, exitThresholdBps],
  );

  useEffect(() => {
    const chart = chartRef.current;
    if (!chart) return;
    return loadData(chart);
  }, [chartReadyTick, loadData]);

  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2">
        <span className="text-xs text-ink-muted">Интервал:</span>
        {(["1h", "4h", "24h", "7d"] as Interval[]).map((iv) => (
          <button
            key={iv}
            onClick={() => setInterval(iv)}
            className={`rounded px-2 py-0.5 text-xs font-medium transition ${
              interval === iv
                ? "bg-accent text-white"
                : "text-ink-muted hover:bg-accent/10 hover:text-ink"
            }`}
          >
            {iv}
          </button>
        ))}
        {loading && (
          <span className="ml-2 text-xs text-ink-muted">Loading…</span>
        )}
        {err && (
          <span className="ml-2 text-xs text-red-600 dark:text-red-400">
            {err}
          </span>
        )}
      </div>
      <ChartCore
        mode={mode}
        onChartReady={handleChartReady}
        className={className ?? "w-full h-[300px] rounded-lg border border-line"}
        options={chartOptions}
      />
      <div className="flex gap-4 text-xs text-ink-muted">
        <span className="flex items-center gap-1">
          <span className="inline-block h-0.5 w-3 bg-cyan-400" /> Basis (bps)
        </span>
        <span className="flex items-center gap-1">
          <span className="inline-block h-0.5 w-3 border-b border-dashed border-green-500" />{" "}
          Entry threshold
        </span>
        <span className="flex items-center gap-1">
          <span className="inline-block h-0.5 w-3 border-b border-dashed border-orange-400" />{" "}
          Exit threshold
        </span>
      </div>
    </div>
  );
}
