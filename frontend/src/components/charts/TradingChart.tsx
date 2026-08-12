import {
  forwardRef,
  useCallback,
  useEffect,
  useImperativeHandle,
  useRef,
  useState,
} from "react";
import { CandlestickSeries, LineSeries } from "lightweight-charts";
import type { IChartApi, ISeriesApi, UTCTimestamp } from "lightweight-charts";
import { apiUrl } from "../../config";
import type {
  ChartInterval,
  ChartVisualType,
  KlinesResponse,
  Market,
} from "../../types";
import { ChartCore } from "./ChartCore";
import { chartColors } from "./chartTheme";
import { priceFormatFromSample } from "./priceFormat";

export interface TradingChartRef {
  chart: IChartApi | null;
  series: ISeriesApi<"Candlestick"> | ISeriesApi<"Line"> | null;
}

export interface TradingChartProps {
  symbol: string;
  market: Market;
  interval: ChartInterval;
  visual: ChartVisualType;
  className?: string;
  /** Fires after a series is (re)created with fresh data. */
  onChartReady?: (
    chart: IChartApi,
    series: ISeriesApi<"Candlestick"> | ISeriesApi<"Line">,
  ) => void;
}

/**
 * Declarative klines chart (candlestick or line) on top of ChartCore.
 *
 * This is the canonical price chart — it replaces the ChartWidget/PriceChart
 * duplication (each of which kept its own createChart + fetch + priceFormat).
 * The ref exposes the same `{ chart, series }` contract ChartWidget did, so
 * callers that attach price lines / overlays need no changes.
 */
export const TradingChart = forwardRef<TradingChartRef, TradingChartProps>(
  function TradingChart(
    { symbol, market, interval, visual, className, onChartReady },
    ref,
  ) {
    const chartRef = useRef<IChartApi | null>(null);
    const seriesRef = useRef<
      ISeriesApi<"Candlestick"> | ISeriesApi<"Line"> | null
    >(null);
    const onReadyRef = useRef(onChartReady);
    onReadyRef.current = onChartReady;

    const [chartReady, setChartReady] = useState(false);
    const [loading, setLoading] = useState(false);
    const [err, setErr] = useState<string | null>(null);

    useImperativeHandle(
      ref,
      () => ({
        get chart() {
          return chartRef.current;
        },
        get series() {
          return seriesRef.current;
        },
      }),
      [],
    );

    const handleChartReady = useCallback((chart: IChartApi) => {
      chartRef.current = chart;
      setChartReady(true);
    }, []);

    // Load klines and (re)create the series whenever the chart becomes ready or
    // symbol/interval/visual change.
    useEffect(() => {
      const chart = chartRef.current;
      if (!chart || !chartReady) return;

      let cancelled = false;
      setLoading(true);
      setErr(null);

      // Drop any previous series before creating a new one.
      if (seriesRef.current) {
        try {
          chart.removeSeries(seriesRef.current);
        } catch {
          /* chart may already be disposed */
        }
        seriesRef.current = null;
      }

      const klinesMarket = market === "cross" ? "spot" : market;
      const q = new URLSearchParams({ market: klinesMarket, symbol, interval });

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
              color: chartColors.accent,
              lineWidth: 2,
              priceFormat: priceFmt,
              priceLineVisible: true,
              lastValueVisible: true,
              crosshairMarkerVisible: true,
              crosshairMarkerRadius: 4,
            });
            series.setData(
              raw.map((c) => ({
                time: c.time as UTCTimestamp,
                value: c.close,
              })),
            );
            seriesRef.current = series;
          } else {
            const series = chart.addSeries(CandlestickSeries, {
              upColor: chartColors.up,
              downColor: chartColors.down,
              borderVisible: false,
              wickUpColor: chartColors.up,
              wickDownColor: chartColors.down,
              priceFormat: priceFmt,
            });
            series.setData(
              raw.map((c) => ({
                time: c.time as UTCTimestamp,
                open: c.open,
                high: c.high,
                low: c.low,
                close: c.close,
              })),
            );
            seriesRef.current = series;
          }

          chart.timeScale().fitContent();
          chart.priceScale("right").applyOptions({
            autoScale: true,
            scaleMargins: { top: 0.12, bottom: 0.12 },
          });
          if (seriesRef.current) {
            onReadyRef.current?.(chart, seriesRef.current);
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
            /* chart may already be disposed */
          }
          seriesRef.current = null;
        }
      };
    }, [chartReady, symbol, market, interval, visual]);

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
          onChartReady={handleChartReady}
          className={className ?? "h-full w-full min-h-[280px]"}
        />
      </div>
    );
  },
);
