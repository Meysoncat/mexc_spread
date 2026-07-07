import { useEffect, useLayoutEffect, useRef, useState } from "react";
import {
  CandlestickSeries,
  ColorType,
  createChart,
} from "lightweight-charts";
import type { IChartApi, ISeriesApi, UTCTimestamp } from "lightweight-charts";
import { fetchKlinesBatched } from "./klinesBatch";
import type { Exchange, KlineCandle, Market } from "./types";

const MINI_KLINES_LIMIT = 96;

interface MiniSparklineProps {
  market: Market;
  symbol: string;
  isDark: boolean;
  exchange?: Exchange;
}

/**
 * Компактный свечной график (1h) — загрузка через batch-эндпоинт при появлении в viewport.
 * Все видимые тайлы собираются в один HTTP-запрос с кэшированием на 60s.
 */
export function MiniSparkline({ market, symbol, isDark, exchange = "mexc" }: MiniSparklineProps) {
  const hostRef = useRef<HTMLDivElement | null>(null);
  const [shouldLoad, setShouldLoad] = useState(false);

  useEffect(() => {
    const el = hostRef.current;
    if (!el) return;
    const ob = new IntersectionObserver(
      (entries) => {
        if (entries.some((e) => e.isIntersecting)) {
          setShouldLoad(true);
          ob.disconnect();
        }
      },
      { rootMargin: "160px 0px", threshold: 0.02 },
    );
    ob.observe(el);
    return () => ob.disconnect();
  }, []);

  useLayoutEffect(() => {
    if (!shouldLoad || !hostRef.current) return;
    const el = hostRef.current;
    const w = Math.max(el.clientWidth, 120);
    const h = Math.max(el.clientHeight, 72);

    const bg = isDark ? "#0f172a" : "#f1f5f9";

    const chart: IChartApi = createChart(el, {
      layout: {
        background: { type: ColorType.Solid, color: bg },
      },
      grid: {
        vertLines: { visible: false },
        horzLines: { visible: false },
      },
      rightPriceScale: {
        visible: false,
        autoScale: true,
        scaleMargins: { top: 0.18, bottom: 0.18 },
      },
      leftPriceScale: { visible: false },
      timeScale: { visible: false },
      crosshair: {
        vertLine: { visible: false, labelVisible: false },
        horzLine: { visible: false, labelVisible: false },
      },
      width: w,
      height: h,
      handleScroll: false,
      handleScale: false,
    });

    const series: ISeriesApi<"Candlestick"> = chart.addSeries(CandlestickSeries, {
      upColor: isDark ? "#26a69a" : "#16a34a",
      downColor: isDark ? "#ef5350" : "#dc2626",
      borderVisible: false,
      wickUpColor: isDark ? "#26a69a" : "#16a34a",
      wickDownColor: isDark ? "#ef5350" : "#dc2626",
      priceLineVisible: false,
      lastValueVisible: false,
    });

    let cancelled = false;

    fetchKlinesBatched(market, symbol, "1h", MINI_KLINES_LIMIT, exchange)
      .then((candles: KlineCandle[]) => {
        if (cancelled || !candles.length) return;
        const pts = candles.map((c) => ({
          time: c.time as UTCTimestamp,
          open: c.open,
          high: c.high,
          low: c.low,
          close: c.close,
        }));
        series.setData(pts);
        chart.timeScale().fitContent();
        chart.priceScale("right").applyOptions({
          autoScale: true,
          scaleMargins: { top: 0.18, bottom: 0.18 },
        });
      })
      .catch(() => {});

    const ro = new ResizeObserver(() => {
      const nw = Math.max(hostRef.current?.clientWidth ?? 0, 120);
      const nh = Math.max(hostRef.current?.clientHeight ?? 0, 72);
      chart.applyOptions({ width: nw, height: nh });
    });
    ro.observe(el);

    return () => {
      cancelled = true;
      ro.disconnect();
      chart.remove();
    };
  }, [shouldLoad, market, symbol, isDark, exchange]);

  return (
    <div
      ref={hostRef}
      className="mt-2 h-[88px] w-full shrink-0 overflow-hidden rounded-lg border border-line/70"
      aria-hidden
      onMouseDown={(e) => e.stopPropagation()}
    />
  );
}
