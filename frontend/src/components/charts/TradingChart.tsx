import {
  forwardRef,
  useCallback,
  useEffect,
  useImperativeHandle,
  useRef,
  useState,
} from "react";
import {
  AreaSeries,
  CandlestickSeries,
  HistogramSeries,
} from "lightweight-charts";
import type {
  IChartApi,
  ISeriesApi,
  MouseEventParams,
  UTCTimestamp,
} from "lightweight-charts";
import { apiUrl } from "../../config";
import type {
  ChartInterval,
  ChartVisualType,
  KlineCandle,
  KlinesResponse,
  Market,
} from "../../types";
import { ChartCore } from "./ChartCore";
import { accentAlpha, chartColors } from "./chartTheme";
import { priceFormatFromSample } from "./priceFormat";

/** Price/area/candlestick series types the chart can expose to callers. */
export type PriceSeries =
  | ISeriesApi<"Candlestick">
  | ISeriesApi<"Line">
  | ISeriesApi<"Area">;

export interface TradingChartRef {
  chart: IChartApi | null;
  series: PriceSeries | null;
}

export interface TradingChartProps {
  symbol: string;
  market: Market;
  interval: ChartInterval;
  visual: ChartVisualType;
  className?: string;
  /** Hide the volume histogram (defaults to shown). */
  hideVolume?: boolean;
  /** Fires after a series is (re)created with fresh data. */
  onChartReady?: (chart: IChartApi, series: PriceSeries) => void;
}

const VOL_UP = "rgba(38, 166, 154, 0.45)";
const VOL_DOWN = "rgba(239, 83, 80, 0.45)";

const volFmt = new Intl.NumberFormat("en-US", {
  notation: "compact",
  maximumFractionDigits: 2,
});

function fmtPrice(v: number): string {
  if (!Number.isFinite(v)) return "—";
  const abs = Math.abs(v);
  const digits = abs >= 1000 ? 2 : abs >= 1 ? 4 : abs >= 0.01 ? 6 : 8;
  return v.toLocaleString("en-US", {
    minimumFractionDigits: 2,
    maximumFractionDigits: digits,
  });
}

/**
 * Declarative klines chart (candlestick or line) on top of ChartCore.
 *
 * Renders the price series plus a volume histogram pinned to the bottom, and a
 * floating OHLC tooltip that follows the crosshair. The ref exposes the same
 * `{ chart, series }` contract callers use to attach price lines / overlays.
 */
export const TradingChart = forwardRef<TradingChartRef, TradingChartProps>(
  function TradingChart(
    { symbol, market, interval, visual, className, hideVolume, onChartReady },
    ref,
  ) {
    const chartRef = useRef<IChartApi | null>(null);
    const seriesRef = useRef<PriceSeries | null>(null);
    const volumeSeriesRef = useRef<ISeriesApi<"Histogram"> | null>(null);
    // time (epoch seconds) -> candle, for O(1) crosshair tooltip lookups.
    const candlesRef = useRef<Map<number, KlineCandle>>(new Map());
    const onReadyRef = useRef(onChartReady);
    useEffect(() => {
      onReadyRef.current = onChartReady;
    }, [onChartReady]);

    const [chartReady, setChartReady] = useState(false);
    const [loading, setLoading] = useState(false);
    const [err, setErr] = useState<string | null>(null);
    const [tip, setTip] = useState<{
      x: number;
      y: number;
      c: KlineCandle;
    } | null>(null);

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
      setTip(null);

      // Drop any previous series before creating new ones.
      const dropSeries = () => {
        for (const s of [seriesRef.current, volumeSeriesRef.current]) {
          if (!s) continue;
          try {
            chart.removeSeries(s);
          } catch {
            /* chart may already be disposed */
          }
        }
        seriesRef.current = null;
        volumeSeriesRef.current = null;
      };
      dropSeries();

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

          // Index candles for the crosshair tooltip.
          const map = new Map<number, KlineCandle>();
          for (const c of raw) map.set(c.time, c);
          candlesRef.current = map;

          if (visual === "line") {
            const series = chart.addSeries(AreaSeries, {
              lineColor: chartColors.accent,
              topColor: accentAlpha(0.28),
              bottomColor: accentAlpha(0.02),
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

          // Volume histogram on an overlay scale pinned to the bottom.
          if (!hideVolume) {
            const vol = chart.addSeries(HistogramSeries, {
              priceFormat: { type: "volume" },
              priceScaleId: "volume",
              lastValueVisible: false,
              priceLineVisible: false,
            });
            vol.setData(
              raw.map((c) => ({
                time: c.time as UTCTimestamp,
                value: c.volume,
                color: c.close >= c.open ? VOL_UP : VOL_DOWN,
              })),
            );
            vol.priceScale().applyOptions({
              scaleMargins: { top: 0.8, bottom: 0 },
            });
            volumeSeriesRef.current = vol;
          }

          chart.timeScale().fitContent();
          chart.priceScale("right").applyOptions({
            autoScale: true,
            scaleMargins: { top: 0.1, bottom: hideVolume ? 0.12 : 0.26 },
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
        dropSeries();
      };
    }, [chartReady, symbol, market, interval, visual, hideVolume]);

    // Floating OHLC tooltip that follows the crosshair.
    useEffect(() => {
      const chart = chartRef.current;
      if (!chart || !chartReady) return;

      const handler = (param: MouseEventParams) => {
        const pt = param.point;
        if (
          !param.time ||
          !pt ||
          pt.x < 0 ||
          pt.y < 0
        ) {
          setTip(null);
          return;
        }
        const c = candlesRef.current.get(Number(param.time));
        if (!c) {
          setTip(null);
          return;
        }
        setTip({ x: pt.x, y: pt.y, c });
      };

      chart.subscribeCrosshairMove(handler);
      return () => chart.unsubscribeCrosshairMove(handler);
    }, [chartReady]);

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
        {tip && <OhlcTooltip {...tip} visual={visual} />}
        <ChartCore
          onChartReady={handleChartReady}
          className={className ?? "h-full w-full min-h-[280px]"}
        />
      </div>
    );
  },
);

/** Compact OHLC/volume readout anchored near the crosshair. */
function OhlcTooltip({
  x,
  y,
  c,
  visual,
}: {
  x: number;
  y: number;
  c: KlineCandle;
  visual: ChartVisualType;
}) {
  const changePct = c.open > 0 ? ((c.close - c.open) / c.open) * 100 : 0;
  const up = c.close >= c.open;
  // Keep the card on-screen: flip to the left of the cursor past the midpoint.
  const flip = x > 220;
  return (
    <div
      className="pointer-events-none absolute z-20 min-w-[9rem] rounded-lg border border-line bg-surface-elevated/95 px-2.5 py-2 font-mono text-[11px] leading-tight shadow-panel-dark backdrop-blur"
      style={{
        left: flip ? x - 152 : x + 16,
        top: Math.max(8, y - 12),
      }}
    >
      {visual === "candle" ? (
        <div className="grid grid-cols-2 gap-x-3 gap-y-0.5">
          <span className="text-ink-muted">O</span>
          <span className="text-right text-ink">{fmtPrice(c.open)}</span>
          <span className="text-ink-muted">H</span>
          <span className="text-right text-ink">{fmtPrice(c.high)}</span>
          <span className="text-ink-muted">L</span>
          <span className="text-right text-ink">{fmtPrice(c.low)}</span>
          <span className="text-ink-muted">C</span>
          <span className="text-right text-ink">{fmtPrice(c.close)}</span>
        </div>
      ) : (
        <div className="flex items-center justify-between gap-3">
          <span className="text-ink-muted">Цена</span>
          <span className="text-ink">{fmtPrice(c.close)}</span>
        </div>
      )}
      <div className="mt-1 flex items-center justify-between gap-3 border-t border-line/60 pt-1">
        <span className={up ? "text-emerald-500" : "text-red-500"}>
          {up ? "+" : ""}
          {changePct.toFixed(2)}%
        </span>
        <span className="text-ink-muted">Vol {volFmt.format(c.volume)}</span>
      </div>
    </div>
  );
}
