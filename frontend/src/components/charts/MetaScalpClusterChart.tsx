import { useCallback, useEffect, useRef } from "react";
import { HistogramSeries } from "lightweight-charts";
import type { IChartApi, ISeriesApi } from "lightweight-charts";
import { ChartCore } from "./ChartCore";
import { chartColors } from "./chartTheme";

export interface ClusterRow {
  Price?: number;
  price?: number;
  Volume?: number;
  volume?: number;
  Delta?: number;
  delta?: number;
}

export interface MetaScalpClusterChartProps {
  rows: ClusterRow[];
  className?: string;
  mode?: "dark" | "light";
}

export function MetaScalpClusterChart({
  rows,
  className,
  mode,
}: MetaScalpClusterChartProps) {
  const chartRef = useRef<IChartApi | null>(null);
  const volumeSeriesRef = useRef<ISeriesApi<"Histogram"> | null>(null);
  const deltaSeriesRef = useRef<ISeriesApi<"Histogram"> | null>(null);

  const handleChartReady = useCallback((chart: IChartApi) => {
    chartRef.current = chart;
  }, []);

  useEffect(() => {
    const chart = chartRef.current;
    if (!chart) return;

    // Remove old series
    if (volumeSeriesRef.current) {
      try { chart.removeSeries(volumeSeriesRef.current); } catch {}
      volumeSeriesRef.current = null;
    }
    if (deltaSeriesRef.current) {
      try { chart.removeSeries(deltaSeriesRef.current); } catch {}
      deltaSeriesRef.current = null;
    }

    if (rows.length === 0) return;

    // Sort by price ascending
    const sorted = [...rows].sort((a, b) => {
      const pa = a.Price ?? a.price ?? 0;
      const pb = b.Price ?? b.price ?? 0;
      return pa - pb;
    });

    // Create series
    const volumeSeries = chart.addSeries(HistogramSeries, {
      color: chartColors.volume,
      priceFormat: { type: "volume", precision: 2 },
      priceLineVisible: false,
      lastValueVisible: false,
    });
    volumeSeriesRef.current = volumeSeries;

    const deltaSeries = chart.addSeries(HistogramSeries, {
      color: chartColors.spread,
      priceFormat: { type: "volume", precision: 2 },
      priceLineVisible: false,
      lastValueVisible: false,
    });
    deltaSeriesRef.current = deltaSeries;

    // Map data — use index as time, price is shown on hover
    const volData = sorted.map((r, i) => ({
      time: (i + 1) as any,
      value: r.Volume ?? r.volume ?? 0,
      color: chartColors.volume,
    }));
    const deltaData = sorted.map((r, i) => ({
      time: (i + 1) as any,
      value: Math.abs(r.Delta ?? r.delta ?? 0),
      color: (r.Delta ?? r.delta ?? 0) >= 0 ? chartColors.up : chartColors.down,
    }));

    volumeSeries.setData(volData);
    deltaSeries.setData(deltaData);

    chart.timeScale().fitContent();
  }, [rows]);

  return (
    <div className="relative h-full w-full">
      <div className="absolute left-3 top-3 z-10 flex gap-3 text-xs">
        <span className="flex items-center gap-1">
          <span className="h-2 w-2 rounded-full bg-blue-500" />
          Volume
        </span>
        <span className="flex items-center gap-1">
          <span className="h-2 w-2 rounded-full bg-emerald-500" />
          Delta+
        </span>
        <span className="flex items-center gap-1">
          <span className="h-2 w-2 rounded-full bg-red-500" />
          Delta-
        </span>
      </div>
      <ChartCore
        mode={mode}
        onChartReady={handleChartReady}
        className={className ?? "h-full w-full min-h-[200px]"}
      />
    </div>
  );
}
