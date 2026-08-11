import { useCallback, useEffect, useRef, useState } from "react";
import { CandlestickSeries } from "lightweight-charts";
import type { IChartApi, ISeriesApi, UTCTimestamp } from "lightweight-charts";
import { ChartCore } from "./components/charts/ChartCore";
import { chartColors } from "./components/charts/chartTheme";
import { fetchKlinesBatched } from "./klinesBatch";
import type { Exchange, KlineCandle, Market } from "./types";

const MINI_KLINES_LIMIT = 96;

interface MiniSparklineProps {
  market: Market;
  symbol: string;
  exchange?: Exchange;
}

/**
 * Компактный свечной график (1h) — загрузка через batch-эндпоинт при появлении
 * в viewport. Все видимые тайлы собираются в один HTTP-запрос с кэшированием 60s.
 *
 * Построен на ChartCore (тема/resize/dispose — общие); ограниченно рендерится
 * (только когда тайл попал во viewport), сохраняя lazy-load.
 */
export function MiniSparkline({
  market,
  symbol,
  exchange = "binance",
}: MiniSparklineProps) {
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

  const handleChartReady = useCallback(
    (chart: IChartApi) => {
      const series: ISeriesApi<"Candlestick"> = chart.addSeries(
        CandlestickSeries,
        {
          upColor: chartColors.up,
          downColor: chartColors.down,
          borderVisible: false,
          wickUpColor: chartColors.up,
          wickDownColor: chartColors.down,
          priceLineVisible: false,
          lastValueVisible: false,
        },
      );
      chart.priceScale("right").applyOptions({
        autoScale: true,
        scaleMargins: { top: 0.18, bottom: 0.18 },
      });

      fetchKlinesBatched(market, symbol, "1h", MINI_KLINES_LIMIT, exchange)
        .then((candles: KlineCandle[]) => {
          if (!candles.length) return;
          series.setData(
            candles.map((c) => ({
              time: c.time as UTCTimestamp,
              open: c.open,
              high: c.high,
              low: c.low,
              close: c.close,
            })),
          );
          chart.timeScale().fitContent();
        })
        .catch(() => {});
    },
    [market, symbol, exchange],
  );

  return (
    <div
      ref={hostRef}
      className="mt-2 h-[88px] w-full shrink-0 overflow-hidden rounded-lg border border-line/70"
      aria-hidden
      onMouseDown={(e) => e.stopPropagation()}
    >
      {shouldLoad && (
        <ChartCore
          onChartReady={handleChartReady}
          className="h-full w-full"
          options={{
            grid: {
              vertLines: { visible: false },
              horzLines: { visible: false },
            },
            rightPriceScale: {
              visible: false,
              scaleMargins: { top: 0.18, bottom: 0.18 },
            },
            leftPriceScale: { visible: false },
            timeScale: { visible: false },
            crosshair: {
              vertLine: { visible: false, labelVisible: false },
              horzLine: { visible: false, labelVisible: false },
            },
            handleScroll: false,
            handleScale: false,
          }}
        />
      )}
    </div>
  );
}
