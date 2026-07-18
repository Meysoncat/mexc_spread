import { useCallback, useEffect, useRef, useState } from "react";
import {
  CandlestickSeries,
  LineSeries,
} from "lightweight-charts";
import type { IChartApi, ISeriesApi, UTCTimestamp } from "lightweight-charts";
import { apiUrl } from "../../config";
import type { ChartInterval, ChartVisualType, KlinesResponse, Market } from "../../types";
import { ChartCore } from "./ChartCore";

export interface PriceChartProps {
  symbol: string;
  market: Market;
  interval: ChartInterval;
  visual: ChartVisualType;
  className?: string;
  mode?: "dark" | "light";
}

function priceFormatFromSample(sample: number) {
  const p = Math.abs(sample);
  if (!Number.isFinite(p) || p === 0) {
    return { type: "price" as const, precision: 4, minMove: 0.0001 };
  }
  if (p >= 10_000) return { type: "price" as const, precision: 2, minMove: 0.01 };
  if (p >= 100) return { type: "price" as const, precision: 2, minMove: 0.01 };
  if (p >= 1) return { type: "price" as const, precision: 4, minMove: 0.0001 };
  if (p >= 0.01) return { type: "price" as const, precision: 6, minMove: 1e-6 };
  return { type: "price" as const, precision: 8, minMove: 1e-8 };
}

export function PriceChart({
  symbol,
  market,
  interval,
  visual,
  className,
  mode,
}: PriceChartProps) {
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<
    ISeriesApi<"Candlestick"> | ISeriesApi<"Line"> | null
  >(null);
  const [chartReadyTick, setChartReadyTick] = useState(0);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const handleChartReady = useCallback((chart: IChartApi) => {
    chartRef.current = chart;
    setChartReadyTick((t) => t + 1);
  }, []);

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

      const klinesMarket = market === "cross" ? "spot" : market;
      const q = new URLSearchParams({
        market: klinesMarket,
        symbol,
        interval,
      });

      fetch(apiUrl(`/api/klines?${q}`))
        .then((r) => r.json() as Promise<KlinesResponse>)
        .then((data) => {
          if (cancelled) return;
          if (!data.ok) {
            setErr(data.error ?? "Ошибка загрузки");
            return;
          }
          const raw = data.candles ?? [];
          if (raw.length === 0) {
            setErr("Нет свечей");
            return;
          }
          const lastClose = raw[raw.length - 1]?.close ?? raw[0].close;
          const priceFmt = priceFormatFromSample(lastClose);

          if (visual === "line") {
            const series = chart.addSeries(LineSeries, {
              color: "#26a69a",
              lineWidth: 2,
              priceFormat: priceFmt,
              priceLineVisible: true,
              lastValueVisible: true,
            });
            const lineData = raw.map((c) => ({
              time: c.time as UTCTimestamp,
              value: c.close,
            }));
            series.setData(lineData);
            seriesRef.current = series;
          } else {
            const series = chart.addSeries(CandlestickSeries, {
              upColor: "#26a69a",
              downColor: "#ef5350",
              borderVisible: false,
              wickUpColor: "#26a69a",
              wickDownColor: "#ef5350",
              priceFormat: priceFmt,
            });
            const candleData = raw.map((c) => ({
              time: c.time as UTCTimestamp,
              open: c.open,
              high: c.high,
              low: c.low,
              close: c.close,
            }));
            series.setData(candleData);
            seriesRef.current = series;
          }

          chart.timeScale().fitContent();
          chart.priceScale("right").applyOptions({
            autoScale: true,
            scaleMargins: { top: 0.12, bottom: 0.12 },
          });
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
    [symbol, market, interval, visual],
  );

  useEffect(() => {
    const chart = chartRef.current;
    if (!chart) return;
    return loadData(chart);
  }, [chartReadyTick, loadData]);

  return (
    <div className="relative h-full w-full">
      {err && (
        <p className="absolute left-3 top-3 z-10 max-w-[90%] rounded bg-surface-elevated/95 px-2 py-1 text-sm text-red-600 dark:text-red-400">
          {err}
        </p>
      )}
      {loading && (
        <p className="absolute left-3 top-3 z-10 text-sm text-ink-muted">
          Загрузка…
        </p>
      )}
      <ChartCore
        mode={mode}
        onChartReady={handleChartReady}
        className={className ?? "h-full w-full min-h-[280px]"}
      />
    </div>
  );
}
