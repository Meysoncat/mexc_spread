import { useCallback, useRef } from "react";
import { AreaSeries, type IChartApi, type UTCTimestamp } from "lightweight-charts";
import { ChartCore } from "./ChartCore";

export interface DepthLevel {
  price: number;
  qty: number;
}

export interface DepthChartProps {
  bids: DepthLevel[];
  asks: DepthLevel[];
  className?: string;
  mode?: "dark" | "light";
}

/**
 * Depth chart — cumulative bid/ask depth as area chart.
 * Bids (green) on the left, asks (red) on the right.
 */
export function DepthChart({ bids, asks, className, mode }: DepthChartProps) {
  const chartRef = useRef<IChartApi | null>(null);

  const handleChartReady = useCallback(
    (chart: IChartApi) => {
      chartRef.current = chart;

      if (bids.length === 0 && asks.length === 0) return;

      // Sort bids descending (highest first), asks ascending (lowest first)
      const sortedBids = [...bids]
        .sort((a, b) => b.price - a.price)
        .slice(0, 50);
      const sortedAsks = [...asks]
        .sort((a, b) => a.price - b.price)
        .slice(0, 50);

      // Compute cumulative qty
      let cumBid = 0;
      const bidData = sortedBids.map((l) => {
        cumBid += l.qty;
        return { price: l.price, cumQty: cumBid };
      });

      let cumAsk = 0;
      const askData = sortedAsks.map((l) => {
        cumAsk += l.qty;
        return { price: l.price, cumQty: cumAsk };
      });

      // Create bid area series (green)
      const bidSeries = chart.addSeries(AreaSeries, {
        lineColor: "#22c55e",
        topColor: "rgba(34, 197, 94, 0.4)",
        bottomColor: "rgba(34, 197, 94, 0.05)",
        lineWidth: 2,
        priceFormat: { type: "volume", precision: 4 },
        lastValueVisible: false,
        priceLineVisible: false,
      });

      // Create ask area series (red)
      const askSeries = chart.addSeries(AreaSeries, {
        lineColor: "#ef4444",
        topColor: "rgba(239, 68, 68, 0.4)",
        bottomColor: "rgba(239, 68, 68, 0.05)",
        lineWidth: 2,
        priceFormat: { type: "volume", precision: 4 },
        lastValueVisible: false,
        priceLineVisible: false,
      });

      // Use price as time axis (fake timestamps for lightweight-charts)
      // We need to use a numeric time that increases
      const bidLineData = bidData.map((d, i) => ({
        time: (1000000 + i) as UTCTimestamp,
        value: d.cumQty,
      }));

      const askLineData = askData.map((d, i) => ({
        time: (2000000 + i) as UTCTimestamp,
        value: d.cumQty,
      }));

      if (bidLineData.length > 0) bidSeries.setData(bidLineData);
      if (askLineData.length > 0) askSeries.setData(askLineData);

      chart.timeScale().fitContent();
    },
    [bids, asks],
  );

  return (
    <ChartCore
      mode={mode}
      onChartReady={handleChartReady}
      className={className ?? "h-full w-full min-h-[200px]"}
      options={{
        rightPriceScale: {
          scaleMargins: { top: 0.1, bottom: 0.1 },
        },
        timeScale: {
          visible: false,
        },
        crosshair: {
          horzLine: { visible: false },
          vertLine: { visible: false },
        },
      }}
    />
  );
}
