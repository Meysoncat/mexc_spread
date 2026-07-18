import { useCallback, useEffect, useRef } from "react";
import { HistogramSeries } from "lightweight-charts";
import type { IChartApi, ISeriesApi } from "lightweight-charts";
import { ChartCore } from "./ChartCore";

export interface MetaScalpOrderbookLevel {
  price: number;
  size: number;
  type: string;
}

export interface MetaScalpOrderbookChartProps {
  asks: MetaScalpOrderbookLevel[];
  bids: MetaScalpOrderbookLevel[];
  className?: string;
  mode?: "dark" | "light";
}

export function MetaScalpOrderbookChart({
  asks,
  bids,
  className,
  mode,
}: MetaScalpOrderbookChartProps) {
  const chartRef = useRef<IChartApi | null>(null);
  const askSeriesRef = useRef<ISeriesApi<"Histogram"> | null>(null);
  const bidSeriesRef = useRef<ISeriesApi<"Histogram"> | null>(null);

  const handleChartReady = useCallback((chart: IChartApi) => {
    chartRef.current = chart;
  }, []);

  useEffect(() => {
    const chart = chartRef.current;
    if (!chart) return;

    // Remove old series
    if (askSeriesRef.current) {
      try { chart.removeSeries(askSeriesRef.current); } catch {}
      askSeriesRef.current = null;
    }
    if (bidSeriesRef.current) {
      try { chart.removeSeries(bidSeriesRef.current); } catch {}
      bidSeriesRef.current = null;
    }

    if (asks.length === 0 && bids.length === 0) return;

    // Sort asks ascending, bids descending for proper display
    const sortedAsks = [...asks].sort((a, b) => a.price - b.price);
    const sortedBids = [...bids].sort((a, b) => b.price - a.price);

    // Take top N levels
    const displayAsks = sortedAsks.slice(0, 20);
    const displayBids = sortedBids.slice(0, 20);

    // Create histogram series
    const askSeries = chart.addSeries(HistogramSeries, {
      color: "#ef5350",
      priceFormat: { type: "volume", precision: 4 },
      priceLineVisible: false,
      lastValueVisible: false,
    });
    askSeriesRef.current = askSeries;

    const bidSeries = chart.addSeries(HistogramSeries, {
      color: "#26a69a",
      priceFormat: { type: "volume", precision: 4 },
      priceLineVisible: false,
      lastValueVisible: false,
    });
    bidSeriesRef.current = bidSeries;

    // Set data — use price as time (x-axis) for horizontal layout
    // lightweight-charts uses time-based x-axis, so we use price as a proxy
    const askData = displayAsks.map((a, i) => ({
      time: (100000 + i) as any, // workaround: use index as time
      value: a.size,
      color: "#ef5350",
    }));
    const bidData = displayBids.map((b, i) => ({
      time: (100000 + i) as any,
      value: b.size,
      color: "#26a69a",
    }));

    askSeries.setData(askData);
    bidSeries.setData(bidData);

    chart.timeScale().fitContent();
  }, [asks, bids]);

  return (
    <div className="relative h-full w-full">
      <ChartCore
        mode={mode}
        onChartReady={handleChartReady}
        className={className ?? "h-full w-full min-h-[200px]"}
      />
    </div>
  );
}
